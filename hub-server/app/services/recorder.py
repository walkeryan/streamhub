"""RecorderService: what to record and when. Moving bytes is the ``CaptureBackend``'s job.

    scheduled → recording → done
        │            ↘ failed      (capture error, capture ended early, free space ran low)
        ├→ missed → done           (no free source connection for ten minutes; done if the
        │                           provider archive supplies the programme afterwards)
        └→ cancelled               (kept until the programme is over so its rule does not re-add it)

A recording is a snapshot of the guide entry, keyed by ``programme_key`` (source feed +
channel + start): programme rows are replaced on every guide refresh, so ``prog:`` ids are
not stable, while the key of an unchanged listing is. One recording, and so one file, exists
per key for the whole household.

The guide stores a title and a category only. Sub-title, season/episode and "repeat" are
taken from what a listing says in those two fields and are otherwise unknown.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import os
import re
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.exc import IntegrityError

from app.infra.db import Database
from app.infra.logging import redact_text
from app.infra.models import Channel, Profile, Programme, Recording, RecordingRule, utcnow
from app.services import ids, recording_policy
from app.services.auth import Principal
from app.services.channels import ChannelService
from app.services.errors import Conflict, Forbidden, NotFound, ServiceError
from app.services.sports import REPLAY, classify_sport, split_matchup

log = logging.getLogger(__name__)

STATES = ("scheduled", "recording", "done", "failed", "missed", "cancelled")
RULE_KINDS = ("series", "team", "keyword")
# A row in one of these may be scheduled again; the others already hold the programme.
DEAD_STATES = ("failed", "missed", "cancelled")
PAD_BEFORE_S = 2 * 60
PAD_AFTER_S = 5 * 60
PAD_AFTER_SPORT_S = 60 * 60
RETRY_EVERY = timedelta(minutes=1)
RETRY_WINDOW = timedelta(minutes=10)
ARCHIVE_WINDOW = timedelta(days=7)
LIST_LIMIT = 500
MAX_RULES = 100
MAX_KEEP = 500
MAX_UPCOMING_PER_RULE = 100
KEYWORD_MIN = 3
ERROR_MAX = 500


class SourceBusy(Conflict):
    """No source connection is free for a capture right now."""

    code = "source_busy"


@dataclass(frozen=True, slots=True)
class CaptureResult:
    path: str | None = None  # absolute, or relative to RECORDINGS_DIR
    size_bytes: int = 0
    error: str | None = None


class CaptureBackend(Protocol):
    async def start(self, recording: Recording) -> None:
        """Begin capturing. Raises ``SourceBusy`` when no connection is free; anything else fails the recording."""

    async def stop(self, recording: Recording) -> CaptureResult:
        """Finalise the capture, running or not, and report the file."""

    def is_running(self, recording_id: str) -> bool: ...

    async def recover_from_archive(self, recording: Recording) -> CaptureResult | None:
        """Fetch an ended programme from the provider archive. None when the channel has none."""


class NullCapture:
    """The backend until a capture implementation is wired in: nothing can be recorded."""

    async def start(self, recording: Recording) -> None:
        raise ServiceError("recording is not configured", code="recording_unavailable")

    async def stop(self, recording: Recording) -> CaptureResult:
        return CaptureResult(error="recording is not configured")

    def is_running(self, recording_id: str) -> bool:
        return False

    async def recover_from_archive(self, recording: Recording) -> CaptureResult | None:
        return None


# ---------------------------------------------------------------------- guide entry → facts
@dataclass(frozen=True, slots=True)
class ProgrammeDetails:
    title: str
    subtitle: str | None
    season: int | None
    episode: int | None
    is_sport: bool
    is_repeat: bool


_EPISODE = re.compile(r"\bS(\d{1,2})\s*[.:\-]?\s*E(?:p\.?\s*)?(\d{1,3})\b", re.IGNORECASE)
_SEPARATORS = " \t-–:|"


def _split_episode(title: str) -> tuple[str, str | None, int | None, int | None]:
    """'Show S02E05 Name' → ('Show', 'Name', 2, 5). Anything without an explicit marker is left whole."""
    found = _EPISODE.search(title)
    head = title[: found.start()].strip(_SEPARATORS) if found else ""
    if not found or not head:
        return title.strip(), None, None, None
    return head, title[found.end() :].strip(_SEPARATORS) or None, int(found.group(1)), int(found.group(2))


def programme_details(title: str, category: str | None) -> ProgrammeDetails:
    base, subtitle, season, episode = _split_episode(title)
    return ProgrammeDetails(
        title=base,
        subtitle=subtitle,
        season=season,
        episode=episode,
        is_sport=classify_sport(title, category) is not None,
        is_repeat=bool(REPLAY.search(f"{title} {category or ''}")),
    )


def programme_key(channel_id: str, start: datetime) -> str:
    """Source feed + channel + start: ``<source_id>:<channel ext_id>@<start, UTC>``."""
    return f"{channel_id.removeprefix('ch:')}@{start.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def series_key(title: str) -> str:
    return ids.normalise_title(_split_episode(title)[0])


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


@lru_cache(maxsize=256)
def _team_pattern(team: str) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(word) for word in team.split())
    return re.compile(rf"(?<!\w){words}(?!\w)", re.IGNORECASE)


def rule_matches(kind: str, match: str, title: str, category: str | None = None) -> bool:
    if kind == "series":
        wanted = series_key(match)
        return bool(wanted) and series_key(title) == wanted
    if kind == "team":
        # The classifier does not extract teams, so a game is a sports listing naming the team as whole words.
        return classify_sport(title, category) is not None and bool(_team_pattern(match).search(title))
    if kind == "keyword":
        return _fold(match) in _fold(title)
    return False


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def _safe(text: str | None, max_bytes: int = 90) -> str:
    cleaned = " ".join(_UNSAFE.sub(" ", text or "").split()).strip(" .")
    return cleaned.encode()[:max_bytes].decode(errors="ignore").strip(" .")


def recording_filename(recording: Recording, tz: tzinfo | None = None) -> str:
    """Path of a recording's file, relative to RECORDINGS_DIR, laid out so a library server recognises it.

    Dates are in ``tz`` (the server's zone when omitted). Names are not unique: two airings
    of an unnumbered series on one day share one, so the writer must not overwrite.
    """
    title = _safe(recording.title) or "Untitled"
    subtitle = _safe(recording.subtitle)
    start = recording.starts_at.astimezone(tz)
    if recording.is_sport:
        name = f"{title} - {subtitle}" if subtitle else title
        return f"Sports/{name} ({start:%Y-%m-%d}).mkv"
    if recording.season is not None and recording.episode is not None:
        code = f"S{recording.season:02d}E{recording.episode:02d}"
        name = f"{title} - {code} - {subtitle}" if subtitle else f"{title} - {code}"
        return f"{title}/Season {recording.season:02d}/{name}.mkv"
    if recording.rule_id is not None:
        # Recorded by a rule, so one of many: filed as a series by air date.
        return f"{title}/{title} - {start:%Y-%m-%d}.mkv"
    return f"{title} ({start:%Y-%m-%d %H%M}).mkv"


# ---------------------------------------------------------------------- rule evaluation
@dataclass(slots=True)
class _Candidate:
    channel_id: str
    channel_name: str
    order: tuple[int, int]  # channel number, then row id
    title: str
    category: str | None
    start: datetime
    stop: datetime
    evidence: dict[str, Any]
    details: ProgrammeDetails
    key: str
    episode: tuple[Any, ...] | None


@dataclass(slots=True)
class _Event:
    """Something this profile already records, to compare candidates against."""

    key: str
    episode: tuple[Any, ...] | None
    text: str
    is_sport: bool
    start: datetime
    stop: datetime


def _episode_id(key: str, season: int | None, episode: int | None, subtitle: str | None) -> tuple[Any, ...] | None:
    """Identifies an episode across airings, when the listing says which one it is."""
    if season is not None and episode is not None:
        return (key, season, episode)
    if subtitle:
        return (key, _fold(subtitle))
    return None


def _same_programme(a: _Candidate | _Event, b: _Candidate | _Event) -> bool:
    # Two known, different episodes at once are a marathon beside a premiere, not one event.
    return a.key == b.key and (a.episode is None or b.episode is None or a.episode == b.episode)


def _clusters(kind: str, candidates: list[_Candidate]) -> list[list[_Candidate]]:
    """Group airings of one event: overlapping in time and, except for a team's games, equal in title."""
    clusters: list[list[_Candidate]] = []
    spans: list[tuple[datetime, datetime]] = []
    for c in candidates:
        for i, members in enumerate(clusters):
            start, stop = spans[i]
            if (kind == "team" or _same_programme(members[0], c)) and c.start < stop and c.stop > start:
                members.append(c)
                spans[i] = (start, max(stop, c.stop))
                break
        else:
            clusters.append([c])
            spans.append((c.start, c.stop))
    return clusters


def _same_event(rule: RecordingRule, c: _Candidate, e: _Event) -> bool:
    if c.start >= e.stop or c.stop <= e.start:
        return False
    if rule.kind == "team":
        return e.is_sport and bool(_team_pattern(rule.match).search(e.text))
    return _same_programme(c, e)


def _rule_identity(kind: str, match: str) -> str:
    return series_key(match) if kind == "series" else _fold(match)


def _error(exc: BaseException) -> str:
    message = exc.message if isinstance(exc, ServiceError) else f"{type(exc).__name__}: {exc}"
    return redact_text(message)[:ERROR_MAX]


class RecorderService:
    def __init__(
        self,
        db: Database,
        channels: ChannelService,
        *,
        recordings_dir: str | Path | None = None,
        min_free_gb: int = 50,
        capture: CaptureBackend | None = None,
        clock: Callable[[], datetime] = utcnow,
        free_bytes: Callable[[], int] | None = None,
    ) -> None:
        self._db = db
        self._channels = channels
        self.recordings_dir = Path(recordings_dir) if recordings_dir else None
        self.min_free_gb = min_free_gb
        # Replaced by the composition root once a capture implementation exists.
        self.capture: CaptureBackend = capture or NullCapture()
        self.clock = clock
        self._free_bytes = free_bytes
        self.content_policy: Any = None
        self._tick_lock = asyncio.Lock()
        self._apply_lock = asyncio.Lock()
        # Serialises everything that starts, stops or removes a capture.
        self._lock = asyncio.Lock()
        self._retry_at: dict[str, datetime] = {}
        self._archive_tried: set[str] = set()
        self._recovery: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        """Off until RECORDINGS_DIR names an existing, writable directory. Nothing is created for it."""
        root = self.recordings_dir
        return root is not None and root.is_dir() and os.access(root, os.W_OK)

    # ------------------------------------------------------------------ guards
    def _require_view(self, p: Principal) -> None:
        if not recording_policy.can_view(p):
            raise Forbidden("You cannot see recordings.", code="recording_forbidden")

    def _require_schedule(self, p: Principal) -> None:
        if not recording_policy.can_schedule(p):
            raise Forbidden("You cannot schedule recordings.", code="recording_forbidden")
        if not self.enabled:
            raise Conflict("Recording is not configured.", code="recording_unavailable")

    async def _ceiling(self, p: Principal) -> Any:
        """What a rating-limited caller's recordings must pass; ``None`` for a caller nothing is kept from.

        A recording is judged by the evidence it kept of its programme, the way the guide judges
        the programme itself. The padding around it is not judged.
        """
        policy = self.content_policy
        if policy is None or not policy.applies_to(p):
            return None
        profile = await policy.profile(p.profile_id, p.limits)
        return policy.programme_clause(Recording.age_evidence, profile) if policy.restricts(profile) else None

    async def _owned(self, p: Principal, recording_id: str) -> Recording:
        query = select(Recording).where(Recording.id == recording_id)
        ceiling = await self._ceiling(p)
        if ceiling is not None:
            query = query.where(ceiling)
        async with self._db.session() as s:
            row = (await s.execute(query)).scalar_one_or_none()
        if row is None or not (recording_policy.sees_all(p) or row.profile_id == p.profile_id):
            raise NotFound("recording not found")
        return row

    async def _owned_rule(self, p: Principal, rule_id: str) -> RecordingRule:
        async with self._db.session() as s:
            row = await s.get(RecordingRule, rule_id)
        if row is None or not (recording_policy.sees_all(p) or row.profile_id == p.profile_id):
            raise NotFound("rule not found")
        return row

    # ------------------------------------------------------------------ playback
    def file_of(self, row: Recording) -> Path | None:
        """A recording's file, while it is where the capture put it. Never anything outside RECORDINGS_DIR."""
        if not row.path or self.recordings_dir is None:
            return None
        try:
            root = self.recordings_dir.resolve()
            target = (root / row.path).resolve()
            return target if root in target.parents and target.is_file() else None
        except OSError:
            return None

    async def playable(self, p: Principal, recording_id: str) -> tuple[Recording, Path]:
        """A recording this caller may watch, and its file: what ``list`` shows them, once it has a file.

        That is a finished recording, and also one that failed part-way and kept what it had.
        """
        self._require_view(p)
        row = await self._owned(p, recording_id)
        path = self.file_of(row) if row.state in ("done", "failed") else None
        if path is None:
            raise NotFound("recording not found")
        return row, path

    async def stored(self, recording_id: str) -> Path:
        """The file behind a stream grant that was issued for a recording, if both are still there."""
        async with self._db.session() as s:
            row = await s.get(Recording, recording_id)
        path = self.file_of(row) if row is not None else None
        if path is None:
            raise NotFound("recording not found")
        return path

    # ------------------------------------------------------------------ reads
    async def list(self, p: Principal, *, state: str | None) -> builtins.list[Recording]:
        """The caller's recordings (everyone's for whoever manages users), earliest first, within the
        caller's rating ceiling. Cancelled ones only on request."""
        self._require_view(p)
        if state is not None and state not in STATES:
            raise ServiceError("unknown state", code="invalid_request")
        if not self.enabled:
            return []
        # The newest LIST_LIMIT, so a long history never hides what is coming up.
        query = select(Recording).order_by(Recording.starts_at.desc(), Recording.id.desc()).limit(LIST_LIMIT)
        query = query.where(Recording.state == state if state is not None else Recording.state != "cancelled")
        if not recording_policy.sees_all(p):
            query = query.where(Recording.profile_id == p.profile_id)
        ceiling = await self._ceiling(p)
        if ceiling is not None:
            query = query.where(ceiling)
        async with self._db.session() as s:
            return list((await s.execute(query)).scalars())[::-1]

    async def list_rules(self, p: Principal) -> builtins.list[RecordingRule]:
        self._require_view(p)
        if not self.enabled:
            return []
        query = select(RecordingRule).order_by(RecordingRule.created_at, RecordingRule.id)
        if not recording_policy.sees_all(p):
            query = query.where(RecordingRule.profile_id == p.profile_id)
        async with self._db.session() as s:
            return list((await s.execute(query)).scalars())

    async def _profile_names(self) -> dict[str, str]:
        async with self._db.session() as s:
            return {pid: name for pid, name in (await s.execute(select(Profile.id, Profile.name))).all()}

    async def to_api(self, rows: builtins.list[Recording]) -> builtins.list[dict[str, Any]]:
        names = await self._profile_names() if rows else {}
        return [
            {
                "id": r.id,
                "profile_id": r.profile_id,
                "profile_name": names.get(r.profile_id, ""),
                "rule_id": r.rule_id,
                "channel_id": r.channel_id,
                "channel_name": r.channel_name,
                "title": r.title,
                "subtitle": r.subtitle,
                "season": r.season,
                "episode": r.episode,
                "is_sport": r.is_sport,
                "teams": split_matchup(r.title) if r.is_sport else [],
                "starts_at": r.starts_at,
                "ends_at": r.ends_at,
                "pad_before_s": r.pad_before_s,
                "pad_after_s": r.pad_after_s,
                "state": r.state,
                "error": r.error,
                "size_bytes": r.size_bytes,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]

    async def rules_to_api(self, rows: builtins.list[RecordingRule]) -> builtins.list[dict[str, Any]]:
        names = await self._profile_names() if rows else {}
        refs = set()
        for r in rows:
            parts = (r.channel_id or "").split(":", 2)
            if len(parts) == 3:
                refs.add((parts[1], parts[2]))
        channels: dict[str, str] = {}
        if refs:
            async with self._db.session() as s:
                found = await s.execute(
                    select(Channel.source_id, Channel.ext_id, Channel.name).where(
                        tuple_(Channel.source_id, Channel.ext_id).in_(list(refs))
                    )
                )
                channels = {ids.channel_id(source_id, ext_id): name for source_id, ext_id, name in found.all()}
        return [
            {
                "id": r.id,
                "profile_id": r.profile_id,
                "profile_name": names.get(r.profile_id, ""),
                "kind": r.kind,
                "match": r.match,
                "channel_id": r.channel_id,
                "channel_name": channels.get(r.channel_id) if r.channel_id else None,
                "new_only": r.new_only,
                "keep": r.keep,
                "enabled": r.enabled,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    async def annotate(self, p: Principal, payload: Any) -> Any:
        """Stamp ``recording_state`` on every programme of an API payload.

        Looked up by programme key, on the finished payload: programme ids change with each
        guide refresh and recording state changes independently of the guide. The state is
        the household's, whoever scheduled it, because a programme is only recorded once.
        """
        if not self.enabled or not recording_policy.can_view(p):
            return payload
        found: builtins.list[tuple[dict[str, Any], datetime]] = []

        def collect(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                if (
                    str(value.get("id") or "").startswith("prog:")
                    and str(value.get("channel_id") or "").startswith("ch:")
                    and isinstance(value.get("start"), datetime)
                ):
                    found.append((value, value["start"]))
                for item in value.values():
                    collect(item)

        collect(payload)
        if not found:
            return payload
        async with self._db.session() as s:
            rows = await s.execute(
                select(Recording.programme_key, Recording.state).where(
                    Recording.starts_at >= min(start for _, start in found),
                    Recording.starts_at <= max(start for _, start in found),
                    Recording.state != "cancelled",
                )
            )
            states = {key: state for key, state in rows.all()}
        for programme, start in found:
            programme["recording_state"] = states.get(programme_key(programme["channel_id"], start))
        return payload

    # ------------------------------------------------------------------ transitions
    async def _transition(self, row: Recording, expect: tuple[str, ...], state: str, **values: Any) -> Recording | None:
        """Compare-and-set on the state, so the tick and a caller cannot both move a recording."""
        async with self._db.session() as s:
            res = await s.execute(
                update(Recording)
                .where(Recording.id == row.id, Recording.state.in_(expect))
                .values(state=state, updated_at=self.clock(), **values)
            )
            if not int(getattr(res, "rowcount", 0) or 0):
                return None
            await s.commit()
            return await s.get(Recording, row.id, populate_existing=True)

    def _remove_file(self, path: str | None) -> None:
        """Delete a recording's file, and the folders it leaves empty. Never anything outside RECORDINGS_DIR."""
        if not path or self.recordings_dir is None:
            return
        try:
            root = self.recordings_dir.resolve()
            target = (root / path).resolve()
            if root not in target.parents or not target.is_file():
                return
            target.unlink()
        except OSError as exc:
            log.warning("recording file not removed", extra={"err": _error(exc)})
            return
        for folder in target.parents:
            if folder == root:
                break
            try:
                folder.rmdir()
            except OSError:
                break  # not empty

    def _snapshot(
        self,
        *,
        profile_id: str,
        rule_id: str | None,
        channel_id: str,
        channel_name: str,
        title: str,
        category: str | None,
        start: datetime,
        stop: datetime,
        evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Everything a recording keeps of its guide entry; nothing is read from the guide again."""
        d = programme_details(title, category)
        return {
            "profile_id": profile_id,
            "rule_id": rule_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "programme_key": programme_key(channel_id, start),
            "title": d.title[:512],
            "subtitle": d.subtitle[:512] if d.subtitle else None,
            "season": d.season,
            "episode": d.episode,
            "is_sport": d.is_sport,
            "age_evidence": dict(evidence or {}),
            "starts_at": start,
            "ends_at": stop,
            "pad_before_s": PAD_BEFORE_S,
            "pad_after_s": PAD_AFTER_SPORT_S if d.is_sport else PAD_AFTER_S,
            "error": None,
            "path": None,
            "size_bytes": None,
            "created_at": self.clock(),
        }

    # ------------------------------------------------------------------ one programme
    async def _resolve(
        self, programme_id: str, channel_id: str | None, start: datetime | None
    ) -> tuple[Programme, Channel]:
        """The guide entry a client means, and the channel to record it from.

        ``programme_id`` alone is trusted as is. A guide refresh replaces the feed's rows and
        SQLite hands their ids out again, so an id from a guide fetched before the refresh can
        name another programme: with ``channel_id`` and ``start``, as the client saw them, the
        entry is found by what identifies it, and a mismatch that cannot be resolved is refused.
        """
        if not programme_id.startswith("prog:"):
            raise NotFound("unknown programme")
        try:
            programme: Programme | None = await self._channels.programme(programme_id)
        except NotFound:
            programme = None
        changed = Conflict("The guide has changed. Refresh it and try again.", code="guide_changed")
        if channel_id is not None:
            ch = await self._channels.channel_by_hub_id(channel_id)
            on_channel = programme is not None and (programme.feed, programme.epg_channel) == (ch.source_id, ch.epg_id)
            if start is None:
                if programme is None:
                    raise NotFound("programme not found")
                if not on_channel:
                    raise changed
            elif programme is None or not on_channel or programme.start != start:
                async with self._db.session() as s:
                    programme = (
                        await s.execute(
                            select(Programme)
                            .where(
                                Programme.feed == ch.source_id,
                                Programme.epg_channel == ch.epg_id,
                                Programme.start == start,
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
            if programme is None:
                raise NotFound("programme not found")
            return programme, ch
        if programme is None:
            raise NotFound("programme not found")
        if start is not None and programme.start != start:
            raise changed
        # Several channels can share one guide entry (HD/SD variants): the lowest number wins.
        async with self._db.session() as s:
            found = (
                await s.execute(
                    select(Channel)
                    .where(Channel.source_id == programme.feed, Channel.epg_id == programme.epg_channel)
                    .order_by(Channel.number, Channel.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
        if found is None:
            raise NotFound("programme not found")
        return programme, found

    async def schedule_programme(
        self, p: Principal, programme_id: str, channel_id: str | None = None, start: datetime | None = None
    ) -> Recording:
        return (await self.schedule_or_get(p, programme_id, channel_id, start))[0]

    async def schedule_or_get(
        self, p: Principal, programme_id: str, channel_id: str | None = None, start: datetime | None = None
    ) -> tuple[Recording, bool]:
        """The recording for this programme and whether this call scheduled it (False: it already was)."""
        self._require_schedule(p)
        programme, ch = await self._resolve(programme_id, channel_id, start)
        # Checked before anything about the programme is echoed back, including an existing recording.
        if self.content_policy is not None and self.content_policy.applies_to(p):
            await self.content_policy.require(p.profile_id, ids.prog_id(programme.id))
        if programme.stop <= self.clock():
            raise Conflict("That programme has already ended.", code="programme_ended")
        values = self._snapshot(
            profile_id=p.profile_id,
            rule_id=None,
            channel_id=ids.channel_id(ch.source_id, ch.ext_id),
            channel_name=ch.name,
            title=programme.title,
            category=programme.category,
            start=programme.start,
            stop=programme.stop,
            evidence=programme.age_evidence,
        )
        for _ in range(2):
            async with self._db.session() as s:
                existing = (
                    await s.execute(select(Recording).where(Recording.programme_key == values["programme_key"]))
                ).scalar_one_or_none()
            if existing is None:
                row = Recording(id=secrets.token_hex(8), state="scheduled", updated_at=self.clock(), **values)
                try:
                    async with self._db.session() as s:
                        s.add(row)
                        await s.commit()
                except IntegrityError:
                    continue  # lost a race for the key: look again
                return row, True
            if existing.state not in DEAD_STATES:
                if not (recording_policy.sees_all(p) or existing.profile_id == p.profile_id):
                    raise Conflict("This programme is already being recorded.", code="already_scheduled")
                return existing, False
            # A cancelled, failed or missed attempt is taken over by whoever asks again.
            async with self._lock:
                self._remove_file(existing.path)
                revived = await self._transition(existing, DEAD_STATES, "scheduled", **values)
            if revived is not None:
                self._archive_tried.discard(revived.id)
                return revived, True
        raise Conflict("This programme is already being recorded.", code="already_scheduled")

    async def cancel(self, p: Principal, recording_id: str) -> None:
        """Cancel an upcoming or running recording; delete a finished one together with its file."""
        self._require_schedule(p)
        async with self._lock:
            row = await self._owned(p, recording_id)
            self._retry_at.pop(row.id, None)
            if row.state == "scheduled":
                await self._transition(row, ("scheduled",), "cancelled")
            elif row.state == "recording":
                try:
                    result = await self.capture.stop(row)
                except Exception as exc:
                    log.warning("capture did not stop cleanly", extra={"recording": row.id, "err": _error(exc)})
                    result = CaptureResult()
                self._remove_file(result.path)
                await self._transition(row, ("recording",), "cancelled", error=None, path=None, size_bytes=None)
            elif row.state != "cancelled":
                self._remove_file(row.path)
                async with self._db.session() as s:
                    await s.execute(delete(Recording).where(Recording.id == row.id))
                    await s.commit()

    # ------------------------------------------------------------------ rules
    def _clean_rule(self, kind: str, match: str, keep: int | None) -> str:
        match = " ".join((match or "").split())[:256]
        if kind not in RULE_KINDS:
            raise ServiceError("unknown rule kind", code="invalid_request")
        if not _rule_identity(kind, match) or (kind == "keyword" and len(match) < KEYWORD_MIN):
            raise ServiceError("That is too short to match on.", code="invalid_request")
        if keep is not None and not 1 <= keep <= MAX_KEEP:
            raise ServiceError(f"keep must be between 1 and {MAX_KEEP}", code="invalid_request")
        return match

    async def add_rule(
        self, p: Principal, kind: str, match: str, channel_id: str | None, new_only: bool, keep: int | None
    ) -> RecordingRule:
        return (await self.add_or_get_rule(p, kind, match, channel_id, new_only, keep))[0]

    async def add_or_get_rule(
        self, p: Principal, kind: str, match: str, channel_id: str | None, new_only: bool, keep: int | None
    ) -> tuple[RecordingRule, bool]:
        """The rule and whether this call created it (False: the profile already had the same one)."""
        self._require_schedule(p)
        match = self._clean_rule(kind, match, keep)
        if channel_id is not None:
            ch = await self._channels.channel_by_hub_id(channel_id)
            channel_id = ids.channel_id(ch.source_id, ch.ext_id)
        async with self._db.session() as s:
            mine = list(
                (await s.execute(select(RecordingRule).where(RecordingRule.profile_id == p.profile_id))).scalars()
            )
            for rule in mine:
                same = _rule_identity(rule.kind, rule.match) == _rule_identity(kind, match)
                if rule.kind == kind and rule.channel_id == channel_id and same:
                    return rule, False
            if len(mine) >= MAX_RULES:
                raise Conflict("Too many recording rules.", code="rule_limit")
            row = RecordingRule(
                id=secrets.token_hex(8),
                profile_id=p.profile_id,
                kind=kind,
                match=match,
                channel_id=channel_id,
                new_only=bool(new_only),
                keep=keep,
                enabled=True,
                created_at=self.clock(),
            )
            s.add(row)
            await s.commit()
        # The guide is already here: do not wait for its next refresh.
        await self.apply_rules()
        return row, True

    async def _drop_scheduled(self, rule_id: str) -> None:
        async with self._lock, self._db.session() as s:
            await s.execute(delete(Recording).where(Recording.rule_id == rule_id, Recording.state == "scheduled"))
            await s.commit()

    async def update_rule(self, p: Principal, rule_id: str, patch: dict[str, Any]) -> RecordingRule:
        """Change ``match``, ``channel_id``, ``new_only``, ``keep`` or ``enabled``.

        What a rule has scheduled exists only while the rule asks for it: a change to what it
        matches, or disabling it, drops its upcoming recordings and schedules afresh.
        """
        self._require_schedule(p)
        rule = await self._owned_rule(p, rule_id)
        before = (rule.match, rule.channel_id, rule.new_only, rule.enabled)
        values: dict[str, Any] = {}
        if "match" in patch or "keep" in patch:
            match = self._clean_rule(rule.kind, patch.get("match", rule.match), patch.get("keep", rule.keep))
            values.update(match=match, keep=patch.get("keep", rule.keep))
        if "channel_id" in patch:
            values["channel_id"] = patch["channel_id"]
            if patch["channel_id"] is not None:
                ch = await self._channels.channel_by_hub_id(patch["channel_id"])
                values["channel_id"] = ids.channel_id(ch.source_id, ch.ext_id)
        for flag in ("new_only", "enabled"):
            if patch.get(flag) is not None:
                values[flag] = bool(patch[flag])
        if values:
            async with self._db.session() as s:
                await s.execute(update(RecordingRule).where(RecordingRule.id == rule.id).values(**values))
                await s.commit()
                rule = await s.get(RecordingRule, rule.id, populate_existing=True) or rule
        if (rule.match, rule.channel_id, rule.new_only, rule.enabled) != before:
            await self._drop_scheduled(rule.id)
            if rule.enabled:
                await self.apply_rules()
        return rule

    async def delete_rule(self, p: Principal, rule_id: str) -> None:
        """Remove a rule and what it had scheduled. Finished recordings stay."""
        self._require_schedule(p)
        rule = await self._owned_rule(p, rule_id)
        await self._drop_scheduled(rule.id)
        async with self._db.session() as s:
            await s.execute(delete(RecordingRule).where(RecordingRule.id == rule.id))
            await s.commit()

    async def _candidates(
        self, profile_id: str, rules: builtins.list[RecordingRule], now: datetime
    ) -> dict[str, builtins.list[_Candidate]]:
        """Future programmes each rule matches, on the channels its profile may watch, earliest first."""
        from app.services.content_policy import active_policy

        out: dict[str, builtins.list[_Candidate]] = {rule.id: [] for rule in rules}
        matched: dict[tuple[str, str, str | None], bool] = {}
        policy = self.content_policy
        # Rules run outside any request, so the profile's content policy is applied here.
        reset = active_policy.set(await policy.scope(profile_id) if policy is not None else None)
        try:
            eligible, _ = await self._channels._channel_query(profile_id, all_languages=True)
            ch = eligible.subquery()
            query = (
                select(
                    ch.c.id,
                    ch.c.source_id,
                    ch.c.ext_id,
                    ch.c.name,
                    ch.c.number,
                    Programme.title,
                    Programme.category,
                    Programme.start,
                    Programme.stop,
                    Programme.age_evidence,
                )
                .join(Programme, (Programme.feed == ch.c.source_id) & (Programme.epg_channel == ch.c.epg_id))
                .where(Programme.start > now)
                .order_by(Programme.start, ch.c.number, ch.c.id, Programme.id)
            )
            async with self._db.session() as s:
                # One streamed pass over the guide for all of the profile's rules.
                rows = await s.stream(query.execution_options(yield_per=500))
                async for pk, source_id, ext_id, name, number, title, category, start, stop, evidence in rows:
                    channel_id = ids.channel_id(source_id, ext_id)
                    for rule in rules:
                        if rule.channel_id is not None and rule.channel_id != channel_id:
                            continue
                        memo = (rule.id, title, category)
                        if memo not in matched:
                            matched[memo] = rule_matches(rule.kind, rule.match, title, category)
                        if matched[memo]:
                            details = programme_details(title, category)
                            key = ids.normalise_title(details.title)
                            out[rule.id].append(
                                _Candidate(
                                    channel_id=channel_id,
                                    channel_name=name,
                                    order=(number, pk),
                                    title=title,
                                    category=category,
                                    start=start,
                                    stop=stop,
                                    evidence=evidence,
                                    details=details,
                                    key=key,
                                    episode=_episode_id(key, details.season, details.episode, details.subtitle),
                                )
                            )
        finally:
            active_policy.reset(reset)
        return out

    async def _apply_profile(self, profile_id: str, rules: builtins.list[RecordingRule], now: datetime) -> int:
        candidates = await self._candidates(profile_id, rules, now)
        if not any(candidates.values()):
            return 0
        async with self._db.session() as s:
            mine = list((await s.execute(select(Recording).where(Recording.profile_id == profile_id))).scalars())
        # A failed or missed attempt does not stand in the way of another airing.
        held = [r for r in mine if r.state not in ("failed", "missed")]
        events = []
        for r in held:
            key = ids.normalise_title(r.title)
            episode = _episode_id(key, r.season, r.episode, r.subtitle)
            events.append(_Event(key, episode, f"{r.title} {r.subtitle or ''}", r.is_sport, r.starts_at, r.ends_at))
        episodes = {e.episode for e, r in zip(events, held, strict=True) if r.state != "cancelled"}
        created = 0
        for rule in rules:
            upcoming = sum(1 for r in mine if r.rule_id == rule.id and r.state == "scheduled")
            wanted = [c for c in candidates[rule.id] if not (rule.new_only and c.details.is_repeat)]
            for cluster in _clusters(rule.kind, wanted):
                if upcoming >= MAX_UPCOMING_PER_RULE:
                    break
                if any(_same_event(rule, c, e) for c in cluster for e in events):
                    continue
                # The same event on several channels: a rule naming a channel only ever sees that
                # one; otherwise the first by channel number.
                pick = min(cluster, key=lambda c: c.order)
                if rule.new_only and pick.episode is not None and pick.episode in episodes:
                    continue
                row = Recording(
                    id=secrets.token_hex(8),
                    state="scheduled",
                    updated_at=now,
                    **self._snapshot(
                        profile_id=profile_id,
                        rule_id=rule.id,
                        channel_id=pick.channel_id,
                        channel_name=pick.channel_name,
                        title=pick.title,
                        category=pick.category,
                        start=pick.start,
                        stop=pick.stop,
                        evidence=pick.evidence,
                    ),
                )
                try:
                    async with self._db.session() as s:
                        s.add(row)
                        await s.commit()
                except IntegrityError:
                    # The household already has this programme (another profile's, or a dead attempt),
                    # or the rule went away meanwhile.
                    continue
                events.append(_Event(pick.key, pick.episode, pick.title, pick.details.is_sport, pick.start, pick.stop))
                episodes.add(pick.episode)
                upcoming += 1
                created += 1
        return created

    async def apply_rules(self) -> int:
        """Schedule every future programme an enabled rule matches. Run after each guide refresh; idempotent."""
        if not self.enabled:
            return 0
        async with self._apply_lock:
            async with self._db.session() as s:
                rules = list(
                    (
                        await s.execute(
                            select(RecordingRule)
                            .where(RecordingRule.enabled.is_(True))
                            .order_by(RecordingRule.created_at, RecordingRule.id)
                        )
                    ).scalars()
                )
            by_profile: dict[str, builtins.list[RecordingRule]] = {}
            for rule in rules:
                by_profile.setdefault(rule.profile_id, []).append(rule)
            created = 0
            now = self.clock()
            for profile_id, profile_rules in by_profile.items():
                try:
                    created += await self._apply_profile(profile_id, profile_rules, now)
                except NotFound:
                    continue  # the profile was deleted under us; its rules go with it
            if created:
                log.info("recordings scheduled by rules", extra={"count": created})
            return created

    # ------------------------------------------------------------------ tick
    def _low_space(self) -> bool:
        try:
            if self._free_bytes is not None:
                free = self._free_bytes()
            else:
                free = shutil.disk_usage(self.recordings_dir or "").free
        except OSError as exc:
            log.warning("free space unknown", extra={"err": _error(exc)})
            return False
        return free < self.min_free_gb * 1024**3

    async def _fresh(self, row: Recording, state: str) -> Recording | None:
        async with self._db.session() as s:
            fresh = await s.get(Recording, row.id)
        return fresh if fresh is not None and fresh.state == state else None

    async def _start(self, row: Recording, now: datetime) -> None:
        due = row.starts_at - timedelta(seconds=row.pad_before_s)
        async with self._lock:
            fresh = await self._fresh(row, "scheduled")
            if fresh is None:
                return
            try:
                await self.capture.start(fresh)
            except Exception as exc:
                if not (isinstance(exc, ServiceError) and exc.code == SourceBusy.code):
                    self._retry_at.pop(row.id, None)
                    await self._transition(fresh, ("scheduled",), "failed", error=_error(exc))
                elif now >= max(due, fresh.created_at) + RETRY_WINDOW:
                    self._retry_at.pop(row.id, None)
                    note = "No source connection was free when this programme started."
                    await self._transition(fresh, ("scheduled",), "missed", error=note)
                else:
                    self._retry_at[row.id] = now + RETRY_EVERY
                return
            self._retry_at.pop(row.id, None)
            if await self._transition(fresh, ("scheduled",), "recording", error=None) is None:
                # The row went away while the capture was starting (its profile was deleted).
                self._remove_file((await self.capture.stop(fresh)).path)

    async def _finish(self, row: Recording, error: str | None = None, *, early: bool = False) -> None:
        async with self._lock:
            fresh = await self._fresh(row, "recording")
            if fresh is None:
                return
            try:
                result = await self.capture.stop(fresh)
            except Exception as exc:
                result = CaptureResult(error=_error(exc))
            error = error or result.error
            if not error and early:
                error = "The capture stopped before the programme ended."
            if not error and not result.path:
                error = "The capture produced no file."
            await self._transition(
                fresh,
                ("recording",),
                "failed" if error else "done",
                error=error[:ERROR_MAX] if error else None,
                path=result.path,
                size_bytes=result.size_bytes if result.path else None,
            )

    async def _step(self, row: Recording, now: datetime, low: bool) -> None:
        end = row.ends_at + timedelta(seconds=row.pad_after_s)
        if row.state == "recording":
            if low:
                note = f"Stopped: free space for recordings fell below {self.min_free_gb} GB."
                await self._finish(row, note)
            elif now >= end:
                await self._finish(row)
            elif not self.capture.is_running(row.id):
                # Also a recording left over from before a restart: what was captured is kept.
                await self._finish(row, early=True)
            return
        if now < row.starts_at - timedelta(seconds=row.pad_before_s):
            return
        if now >= end:
            note = "The programme ended before recording could start."
            await self._transition(row, ("scheduled",), "missed", error=note)
        elif low:
            note = f"Not started: free space for recordings is below {self.min_free_gb} GB."
            await self._transition(row, ("scheduled",), "failed", error=note)
        elif now >= self._retry_at.get(row.id, now):
            await self._start(row, now)

    async def _prune(self, now: datetime) -> None:
        async with self._db.session() as s:
            # A cancelled recording only has to outlast the programme its rule would re-add.
            await s.execute(delete(Recording).where(Recording.state == "cancelled", Recording.ends_at <= now))
            await s.commit()
            rules = list((await s.execute(select(RecordingRule).where(RecordingRule.keep.is_not(None)))).scalars())
            extra: builtins.list[Recording] = []
            for rule in rules:
                done = (
                    await s.execute(
                        select(Recording)
                        .where(Recording.rule_id == rule.id, Recording.state == "done")
                        .order_by(Recording.starts_at.desc(), Recording.id.desc())
                        .offset(rule.keep or 0)
                    )
                ).scalars()
                extra.extend(done)
        for row in extra:
            async with self._lock, self._db.session() as s:
                self._remove_file(row.path)
                await s.execute(delete(Recording).where(Recording.id == row.id, Recording.state == "done"))
                await s.commit()

    async def _recover(self, rows: builtins.list[Recording]) -> None:
        # One at a time: an archive fetch occupies a source connection like any viewer.
        for row in rows:
            self._archive_tried.add(row.id)
            try:
                result = await self.capture.recover_from_archive(row)
            except Exception as exc:
                result = CaptureResult(error=_error(exc))
            if result is None:
                continue
            if result.error or not result.path:
                note = f"{row.error or 'Missed.'} Archive: {result.error or 'no file'}"[:ERROR_MAX]
                await self._transition(row, ("missed",), "missed", error=note)
            else:
                values = {"error": None, "path": result.path, "size_bytes": result.size_bytes}
                if await self._transition(row, ("missed",), "done", **values) is None:
                    self._remove_file(result.path)  # deleted or scheduled again while the fetch ran

    async def _spawn_recovery(self, now: datetime) -> None:
        """Ask the backend for missed programmes that have ended. Beside the tick: a fetch can take minutes."""
        if self._recovery is not None and not self._recovery.done():
            return
        async with self._db.session() as s:
            missed = list(
                (
                    await s.execute(
                        select(Recording)
                        .where(
                            Recording.state == "missed",
                            Recording.ends_at <= now,
                            Recording.ends_at > now - ARCHIVE_WINDOW,
                        )
                        .order_by(Recording.starts_at, Recording.id)
                    )
                ).scalars()
            )
        pending = [row for row in missed if row.id not in self._archive_tried]
        if pending:
            self._recovery = asyncio.create_task(self._recover(pending))

    async def tick(self, now: datetime) -> None:
        """Start what is due, stop what has finished, retry what is waiting, enforce ``keep`` and free space."""
        if not self.enabled:
            return
        async with self._tick_lock:
            async with self._db.session() as s:
                rows = list(
                    (
                        await s.execute(
                            select(Recording)
                            .where(
                                Recording.state.in_(("scheduled", "recording")),
                                Recording.starts_at <= now + timedelta(seconds=PAD_BEFORE_S),
                            )
                            .order_by(Recording.starts_at, Recording.id)
                        )
                    ).scalars()
                )
            active = {row.id for row in rows}
            self._retry_at = {key: at for key, at in self._retry_at.items() if key in active}
            low = bool(rows) and self._low_space()
            for row in rows:
                try:
                    await self._step(row, now, low)
                except Exception:
                    # One recording must not stall the rest.
                    log.exception("recorder step failed", extra={"recording": row.id})
            await self._prune(now)
            await self._spawn_recovery(now)

    async def run_tick(self) -> None:
        await self.tick(self.clock())

    async def wait_idle(self) -> None:
        if self._recovery is not None:
            await asyncio.gather(self._recovery, return_exceptions=True)

    async def shutdown(self) -> None:
        """Abandon an archive fetch in flight; it is attempted again after the next start."""
        if self._recovery is not None and not self._recovery.done():
            self._recovery.cancel()
        await self.wait_idle()
