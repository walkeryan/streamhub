"""Guide reconciliation, tuner reservations and retention estimates for the DVR."""

from __future__ import annotations

import secrets
import shutil
from datetime import datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.infra.models import Channel, Programme, Recording, RecordingRule, Source
from app.services import ids
from app.services.auth import Principal
from app.services.content_policy import active_policy
from app.services.errors import Conflict
from app.services.event_slots import parse_event_slot
from app.services.recorder import programme_details, programme_key

if TYPE_CHECKING:
    from app.services.recorder import RecorderService

Airing = tuple[Programme, Channel]
MAX_PADDING = timedelta(hours=6)
GUIDE_DRIFT = timedelta(hours=6)
RERUN_HORIZON = timedelta(days=14)


def _plain(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _same(row: Recording, programme: Programme, *, rerun: bool) -> bool:
    if rerun and programme.stop - programme.start + timedelta(seconds=2) < row.ends_at - row.starts_at:
        return False  # a highlights edit can carry the same title and episode number
    facts = programme_details(programme.title, programme.category)
    if _plain(facts.title) != _plain(row.title):
        return False
    if row.season is not None and row.episode is not None:
        return (row.season, row.episode) == (facts.season, facts.episode)
    if row.subtitle:
        return _plain(row.subtitle) == _plain(facts.subtitle)
    if facts.episode is not None or facts.subtitle:
        return False
    if row.guide_description and programme.description:
        return (not rerun or facts.is_repeat) and _plain(row.guide_description) == _plain(programme.description)
    # A bare title can track a nearby shift, but does not identify a later repeat.
    return not rerun and abs((row.ends_at - row.starts_at) - (programme.stop - programme.start)) <= timedelta(minutes=2)


def _airing(value: Airing) -> dict[str, Any]:
    programme, channel = value
    return {
        "programme_id": ids.prog_id(programme.id),
        "channel_id": ids.channel_id(channel.source_id, channel.ext_id),
        "channel_name": channel.name,
        "title": programme.title,
        "start": programme.start,
        "stop": programme.stop,
    }


def _window(row: Recording) -> tuple[datetime, datetime]:
    return row.starts_at - timedelta(seconds=row.pad_before_s), row.ends_at + timedelta(seconds=row.pad_after_s)


class RecordingPlanner:
    def __init__(self, recorder: RecorderService):
        self.rec = recorder

    async def find_airings(self, row: Recording, now: datetime, *, rerun: bool) -> list[Airing]:
        source, external = row.channel_id.removeprefix("ch:").split(":", 1)
        eligible, _ = await self.rec._channels._channel_query(
            row.profile_id, all_languages=True, include_personal_hidden=not rerun
        )
        query = (
            select(Programme, Channel)
            .join(Channel, (Programme.feed == Channel.source_id) & (Programme.epg_channel == Channel.epg_id))
            .join(Source, Source.id == Channel.source_id)
            .where(Source.enabled.is_(True), Channel.id.in_(select(eligible.subquery().c.id)))
        )
        if rerun:
            # Narrow in SQL before testing parsed episode identity. No wildcards from a guide title.
            query = query.where(
                Programme.start > now,
                Programme.start < now + RERUN_HORIZON,
                Programme.title.istartswith(row.title, autoescape=True),
            )
        else:
            query = query.where(
                Channel.source_id == source,
                Channel.ext_id == external,
                Programme.stop > now,
                Programme.start.between(row.starts_at - GUIDE_DRIFT, row.starts_at + GUIDE_DRIFT),
            )
        query = query.order_by(Programme.start, Channel.number, Channel.id)
        async with self.rec._db.session() as session:
            rows = (await session.execute(query)).all()
        return [(p, c) for p, c in rows if _same(row, p, rerun=rerun)]

    async def capacity(
        self,
        row: Recording,
        *,
        higher_only: bool = False,
        now: datetime | None = None,
        overlaps: list[Recording] | None = None,
    ) -> tuple[int | None, int]:
        source = row.channel_id.split(":", 2)[1]
        start, end = _window(row)
        shared = getattr(self.rec.capture, "can_share_now", None)
        if now and start < now:
            start = now
        async with self.rec._db.session() as session:
            src = await session.get(Source, source)
            limit = self.rec._channels._sources.max_connections(src) if src else None
            if not limit or start >= end or (shared and start <= self.rec.clock() < end and shared(row)):
                return limit, 0
            query = select(Recording).where(
                Recording.state.in_(("scheduled", "recording")),
                Recording.guide_review.is_(False),
                Recording.channel_id.startswith(f"ch:{source}:", autoescape=True),
                Recording.programme_key != row.programme_key,
                Recording.starts_at < end + MAX_PADDING,
                Recording.ends_at > start - MAX_PADDING,
            )
            peers = list(await session.scalars(query))
        events: list[tuple[datetime, int]] = []
        for other in peers:
            if (
                higher_only
                and other.state != "recording"
                and (-other.priority, other.starts_at, other.id) >= (-row.priority, row.starts_at, row.id)
            ):
                continue
            a, b = _window(other)
            left, right = max(start, a), min(end, b)
            if left < right:
                events.extend(((left, 1), (right, -1)))
                if overlaps is not None:
                    overlaps.append(other)
        used = peak = 0
        for _, change in sorted(events):  # an ending reservation releases before the next starts
            used += change
            peak = max(peak, used)
        return limit, max(0, peak + 1 - limit)

    async def plan(self, p: Principal, row: Recording) -> dict[str, Any]:
        now = self.rec.clock()
        peers: list[Recording] = []
        limit, count = await self.capacity(row, overlaps=peers)
        alternatives: list[dict[str, Any]] = []
        if row.guide_review:
            alternatives = [_airing(pair) for pair in await self.find_airings(row, now, rerun=False)]
        elif count:
            for programme, channel in await self.find_airings(row, now, rerun=True):
                possible = self.for_airing(row.profile_id, programme, channel, row.priority)
                if possible.programme_key != row.programme_key and (await self.capacity(possible))[1] == 0:
                    alternatives.append(_airing((programme, channel)))
                    if len(alternatives) == 6:
                        break
        evictions: list[str] = []
        async with self.rec._db.session() as session:
            source_name = await session.scalar(select(Source.name).where(Source.id == row.channel_id.split(":", 2)[1]))
            conflict_titles = []
            if count:
                visible = await self.rec._ceiling(p)
                for peer in peers:
                    if peer.profile_id == p.profile_id and (
                        visible is None
                        or await session.scalar(select(Recording.id).where(Recording.id == peer.id, visible))
                    ):
                        conflict_titles.append(peer.title)
            if row.state == "scheduled" and row.rule_id and not row.protected:
                rule = await session.get(RecordingRule, row.rule_id)
                if rule and rule.keep and rule.profile_id == row.profile_id:
                    targets = await session.scalars(
                        select(Recording)
                        .where(
                            Recording.rule_id == rule.id,
                            Recording.profile_id == rule.profile_id,
                            Recording.protected.is_(False),
                            Recording.replacement_for.is_(None),
                            Recording.state == "done",
                        )
                        .order_by(Recording.starts_at.desc(), Recording.id.desc())
                        .offset(max(0, rule.keep - 1))
                    )
                    visible = await self.rec._ceiling(p)
                    for old in targets:
                        if visible is None or await session.scalar(
                            select(Recording.id).where(Recording.id == old.id, visible)
                        ):
                            evictions.append(old.title)
            samples = list(
                await session.scalars(
                    select(Recording)
                    .where(Recording.profile_id == row.profile_id, Recording.state == "done", Recording.size_bytes > 0)
                    .order_by(Recording.ends_at.desc())
                    .limit(20)
                )
            )
        rates = []
        for sample in samples:
            left, right = _window(sample)
            duration = ((sample.captured_until or right) - (sample.captured_from or left)).total_seconds()
            if duration > 0 and sample.size_bytes:
                rates.append(sample.size_bytes / duration)
        try:
            free = (
                self.rec._free_bytes()
                if self.rec._free_bytes
                else shutil.disk_usage(self.rec.recordings_dir or "").free
            )
        except OSError:
            free = None
        start, end = _window(row)
        return {
            "source_name": source_name,
            "connection_limit": limit,
            "conflict_count": count,
            "overlapping_titles": conflict_titles,
            "alternates": alternatives,
            "evictions": evictions,
            "reserve_bytes": self.rec.min_free_gb * 1024**3,
            "free_bytes": free,
            "estimated_bytes": int(median(rates) * (end - start).total_seconds()) if rates else None,
        }

    def for_airing(self, profile_id: str, programme: Programme, channel: Channel, priority: int) -> Recording:
        snapshot = self.rec._snapshot(
            profile_id=profile_id,
            rule_id=None,
            channel_id=ids.channel_id(channel.source_id, channel.ext_id),
            channel_name=channel.name,
            title=programme.title,
            category=programme.category,
            start=programme.start,
            stop=programme.stop,
            evidence=programme.age_evidence,
        )
        return Recording(
            id=secrets.token_hex(8),
            state="scheduled",
            priority=priority,
            guide_description=programme.description,
            **snapshot,
        )

    async def rebind(self, row: Recording, match: Airing, *, automatic: bool = False) -> bool:
        p, channel = match
        key = programme_key(ids.channel_id(channel.source_id, channel.ext_id), p.start)
        try:
            async with self.rec._db.session() as session:
                query = update(Recording).where(
                    Recording.id == row.id, Recording.state == "scheduled", Recording.programme_key == row.programme_key
                )
                if automatic:
                    query = query.where(Recording.follow_guide.is_(True))
                changed = await session.execute(
                    query.values(
                        starts_at=p.start,
                        ends_at=p.stop,
                        programme_key=key,
                        channel_id=ids.channel_id(channel.source_id, channel.ext_id),
                        channel_name=channel.name,
                        guide_review=False,
                        guide_description=p.description,
                        age_evidence=p.age_evidence or {},
                        updated_at=self.rec.clock(),
                    )
                )
                await session.commit()
                return bool(getattr(changed, "rowcount", 0))
        except IntegrityError:
            return False

    async def change(self, p: Principal, rid: str, patch: dict[str, Any]) -> Recording:
        self.rec._require_schedule(p)
        async with self.rec._lock:
            row = await self.rec._owned(p, rid)
            changes = {k: patch[k] for k in ("protected", "priority") if patch.get(k) is not None}
            if "priority" in changes and row.state != "scheduled":
                raise Conflict("Only upcoming recordings have scheduling priority.")
            if "protected" in changes and row.state not in ("done", "failed"):
                raise Conflict("Only retained recordings can be protected.")
            if patch.get("keep_original_time") or patch.get("airing"):
                if row.state != "scheduled" or (
                    not row.guide_review and (not patch.get("airing") or not (await self.capacity(row))[1])
                ):
                    raise Conflict("This recording no longer needs a guide choice.")
                if patch.get("airing"):
                    choice = patch["airing"]
                    matches = await self.find_airings(row, self.rec.clock(), rerun=not row.guide_review)
                    chosen = next(
                        (
                            m
                            for m in matches
                            if _airing(m)["channel_id"] == choice["channel_id"] and m[0].start == choice["start"]
                        ),
                        None,
                    )
                    if (
                        chosen
                        and not row.guide_review
                        and (await self.capacity(self.for_airing(row.profile_id, *chosen, row.priority)))[1]
                    ):
                        raise Conflict("This airing now has a tuner conflict.")
                    if not chosen or not await self.rebind(row, chosen):
                        raise Conflict("This airing changed. Check the guide again.")
                else:
                    changes.update(guide_review=False, follow_guide=False)
            if changes:
                await self.rec._transition(row, (row.state,), row.state, **changes)
        return await self.rec._owned(p, rid)

    async def refreshed(self, now: datetime) -> None:
        async with self.rec._db.session() as session:
            rows = list(
                await session.scalars(
                    select(Recording).where(
                        Recording.rule_id.is_(None),
                        Recording.replacement_for.is_(None),
                        Recording.follow_guide.is_(True),
                        Recording.state == "scheduled",
                        Recording.ends_at > now,
                    )
                )
            )
        for row in rows:
            policy = self.rec.content_policy
            token = active_policy.set(await policy.scope(row.profile_id) if policy else None)
            try:
                # Legacy one-offs made from an event-slot channel never had an EPG row.
                async with self.rec._db.session() as session:
                    source, external = row.channel_id.removeprefix("ch:").split(":", 1)
                    name = await session.scalar(
                        select(Channel.name).where(Channel.source_id == source, Channel.ext_id == external)
                    )
                if name and parse_event_slot(name):
                    continue
                matches = await self.find_airings(row, now, rerun=False)
                exact = next((m for m in matches if m[0].start == row.starts_at), None)
                if exact and exact[0].stop == row.ends_at and not row.guide_review:
                    continue
                if exact or len(matches) == 1:
                    async with self.rec._lock:
                        if await self.rebind(row, exact or matches[0], automatic=True):
                            continue
                if not row.guide_review:
                    async with self.rec._lock, self.rec._db.session() as session:
                        await session.execute(
                            update(Recording)
                            .where(
                                Recording.id == row.id,
                                Recording.state == "scheduled",
                                Recording.programme_key == row.programme_key,
                                Recording.follow_guide.is_(True),
                            )
                            .values(guide_review=True, updated_at=now)
                        )
                        await session.commit()
            finally:
                active_policy.reset(token)

    async def schedule_repair(self, row: Recording, now: datetime) -> None:
        if not row.recover_rerun or row.replacement_for or row.ends_at > now:
            return
        policy = self.rec.content_policy
        token = active_policy.set(await policy.scope(row.profile_id) if policy else None)
        try:
            matches = await self.find_airings(row, now, rerun=True)
            async with self.rec._lock, self.rec._db.session() as session:
                current = await session.get(Recording, row.id)
                if not current or current.state not in ("missed", "failed") or not current.recover_rerun:
                    return
                active = await session.scalar(
                    select(Recording.id)
                    .where(Recording.replacement_for == row.id, Recording.state.in_(("scheduled", "recording", "done")))
                    .limit(1)
                )
                if active:
                    return
                for programme, channel in matches:
                    child = self.for_airing(row.profile_id, programme, channel, -1)
                    existing = await session.scalar(
                        select(Recording).where(Recording.programme_key == child.programme_key)
                    )
                    if existing:
                        if (
                            row.rule_id
                            and existing.rule_id == row.rule_id
                            and existing.profile_id == row.profile_id
                            and existing.state == "scheduled"
                            and existing.replacement_for is None
                        ):
                            existing.replacement_for = row.id
                            await session.commit()
                            return
                        continue
                    if (await self.capacity(child))[1]:
                        continue
                    child.replacement_for = row.id
                    session.add(child)
                    try:
                        await session.commit()
                    except IntegrityError:
                        await session.rollback()
                    return
        finally:
            active_policy.reset(token)

    async def promote(self, row: Recording) -> None:
        path = self.rec.playable_file(row)
        if not row.replacement_for:
            return
        valid = False
        if path and row.captured_from and row.captured_until:
            material = await self.rec.probe.run(str(path), {})
            required = (row.ends_at - row.captured_from).total_seconds()
            valid = bool(
                material
                and material.get("video")
                and (material.get("duration_ms") or 0) >= (required - 2) * 1000
                and row.captured_from <= row.starts_at + timedelta(seconds=2)
                and row.captured_until >= row.ends_at
            )
        async with self.rec._lock, self.rec._db.session() as session:
            new = await session.get(Recording, row.id)
            original = await session.get(Recording, row.replacement_for)
            if (
                not new
                or new.state != "done"
                or not original
                or not original.recover_rerun
                or new.profile_id != original.profile_id
                or new.replacement_for != original.id
                or original.state not in ("failed", "missed")
            ):
                return
            if not valid or new.ends_at - new.starts_at + timedelta(seconds=2) < original.ends_at - original.starts_at:
                new.state, new.error = "failed", "Rerun could not be verified as a complete recording."
                discarded = new.path
                new.path, new.size_bytes = None, None
                await session.commit()
                if discarded != original.path:
                    self.rec._remove_file(discarded)
                return
            old_path = original.path
            original.path, original.size_bytes = new.path, new.size_bytes
            original.state, original.error, original.recovered_at = "done", None, self.rec.clock()
            original.captured_from, original.captured_until = new.captured_from, new.captured_until
            original.age_evidence = new.age_evidence
            await session.execute(delete(Recording).where(Recording.id == new.id))
            await session.commit()
            if old_path != original.path:
                self.rec._remove_file(old_path)
