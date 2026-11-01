"""Disabled-by-default server content policy. No inferred ages or client-only gates."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import String, and_, case, cast, exists, func, literal, not_, or_, select
from sqlalchemy.orm import with_loader_criteria

from app.infra.db import Database
from app.infra.models import AgeClassification, Channel, Profile, Programme, Source, Title
from app.services.classification import MOVIE_CODES, TV_CODES
from app.services.errors import Conflict, Forbidden, NotFound, ServiceError

active_principal: ContextVar[Any] = ContextVar("content_principal", default=None)

active_policy: ContextVar[tuple[ContentPolicy, Profile] | None] = ContextVar("content_policy", default=None)


class ContentPolicy:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db, self.settings = db, settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.content_policy_enabled)

    async def profile(self, profile_id: str) -> Profile:
        async with self.db.session() as session:
            row = await session.get(Profile, profile_id)
        if row is None:
            raise NotFound("profile not found")
        return row

    def values(self, profile: Profile) -> dict[str, Any]:
        stored = profile.content_policy or {}
        return {
            "restricted": bool(stored.get("restricted", profile.kids and not profile.is_admin)),
            "allowed_movie_codes": list(stored.get("allowed_movie_codes", [])),
            "allowed_tv_codes": list(stored.get("allowed_tv_codes", [])),
            "allow_unknown": False,
        }

    def is_restricted(self, profile: Profile) -> bool:
        return self.enabled and self.values(profile)["restricted"]

    def evidence_clause(self, evidence: Any, profile: Profile) -> Any:
        values = self.values(profile)
        records = func.json_each(evidence, "$.records").table_valued("value")
        code, system = func.json_extract(records.c.value, "$.code"), func.json_extract(records.c.value, "$.system")
        allowed = or_(
            and_(system == "US-MPA", code.in_(values["allowed_movie_codes"])),
            and_(system == "US-TVPG", code.in_(values["allowed_tv_codes"])),
        )
        known = and_(
            func.json_extract(records.c.value, "$.country") == "US",
            func.json_extract(records.c.value, "$.status") == "known",
        )
        return and_(
            func.json_extract(evidence, "$.status") == "known",
            func.json_extract(evidence, "$.assessment_region") == "US",
            exists(select(1).select_from(records).where(known, allowed)),
            ~exists(select(1).select_from(records).where(known, ~allowed)),
        )

    def programme_clause(self, evidence: Any, profile: Profile) -> Any:
        return and_(
            self.evidence_clause(evidence, profile),
            func.json_extract(evidence, "$.scope") == "programme",
            func.json_extract(evidence, "$.provider") == "xmltv",
        )

    def title_clause(self, profile: Profile, title: Any = Title) -> Any:
        mt = case((title.kind.in_(("movie", "vod")), "movie"), else_="tv")
        key = literal("tmdb:") + mt + ":" + cast(title.tmdb_id, String)
        # Episode/season IDs are different namespaces. Without explicit episode
        # classification, restricted playback stays unknown rather than inheriting.
        return and_(
            title.kind.in_(("movie", "vod", "show", "series")),
            title.tmdb_id > 0,
            key.in_(
                select(AgeClassification.subject_key).where(
                    AgeClassification.provider == "tmdb",
                    func.json_extract(AgeClassification.evidence, "$.subject_key") == AgeClassification.subject_key,
                    or_(
                        and_(
                            AgeClassification.subject_key.like("tmdb:movie:%"),
                            func.json_extract(AgeClassification.evidence, "$.scope") == "movie",
                        ),
                        and_(
                            AgeClassification.subject_key.like("tmdb:tv:%"),
                            func.json_extract(AgeClassification.evidence, "$.scope") == "series",
                        ),
                    ),
                    AgeClassification.observed_at >= datetime.now(UTC) - timedelta(days=7),
                    self.evidence_clause(AgeClassification.evidence, profile),
                )
            ),
        )

    def channel_clause(self, profile: Profile, channel: Any = Channel, at: datetime | None = None) -> Any:
        p = Programme.__table__.alias("policy_airing")
        at = at or datetime.now(UTC)
        current = and_(p.c.feed == channel.source_id, p.c.epg_channel == channel.epg_id, p.c.start <= at, p.c.stop > at)
        allowed = self.programme_clause(p.c.age_evidence, profile)
        return and_(
            exists(select(1).select_from(p).where(current, allowed)),
            ~exists(select(1).select_from(p).where(current, not_(func.coalesce(allowed, False)))),
        )

    def scope_statement(self, statement: Any, profile: Profile) -> Any:
        if not self.is_restricted(profile):
            return statement
        return statement.options(
            with_loader_criteria(Title, self.title_clause(profile), include_aliases=True),
            with_loader_criteria(Channel, self.channel_clause(profile), include_aliases=True),
            with_loader_criteria(
                Programme, self.programme_clause(Programme.age_evidence, profile), include_aliases=True
            ),
        )

    async def allowed_refs(self, profile_id: str, refs: list[str], at: datetime | None = None) -> set[str]:
        profile = await self.profile(profile_id)
        if not self.is_restricted(profile):
            return set(refs)
        allowed: set[str] = set()
        canonical, programmes = [], []
        libraries: dict[str, list[str]] = {}
        channels: dict[str, list[str]] = {}
        for ref in set(refs):
            parts = ref.split(":", 2)
            if len(parts) == 3 and parts[0] == "tmdb" and parts[1] in {"movie", "tv"} and parts[2].isdigit():
                canonical.append(ref)
            elif len(parts) == 3 and parts[0] in {"lib", "ch"}:
                (libraries if parts[0] == "lib" else channels).setdefault(parts[1], []).append(parts[2])
            elif len(parts) == 2 and parts[0] == "prog" and parts[1].isdigit():
                programmes.append(int(parts[1]))
        reset = active_policy.set(None)
        try:
            async with self.db.session() as session:
                if canonical:
                    allowed.update(
                        (
                            await session.execute(
                                select(AgeClassification.subject_key).where(
                                    AgeClassification.subject_key.in_(canonical),
                                    AgeClassification.provider == "tmdb",
                                    func.json_extract(AgeClassification.evidence, "$.subject_key")
                                    == AgeClassification.subject_key,
                                    or_(
                                        and_(
                                            AgeClassification.subject_key.like("tmdb:movie:%"),
                                            func.json_extract(AgeClassification.evidence, "$.scope") == "movie",
                                        ),
                                        and_(
                                            AgeClassification.subject_key.like("tmdb:tv:%"),
                                            func.json_extract(AgeClassification.evidence, "$.scope") == "series",
                                        ),
                                    ),
                                    AgeClassification.observed_at >= datetime.now(UTC) - timedelta(days=7),
                                    self.evidence_clause(AgeClassification.evidence, profile),
                                )
                            )
                        ).scalars()
                    )
                for source, ext_ids in libraries.items():
                    hits = (
                        await session.execute(
                            select(Title.ext_id).where(
                                Title.source_id == source, Title.ext_id.in_(ext_ids), self.title_clause(profile)
                            )
                        )
                    ).scalars()
                    allowed.update(f"lib:{source}:{ext}" for ext in hits)
                for source, ext_ids in channels.items():
                    hits = (
                        await session.execute(
                            select(Channel.ext_id).where(
                                Channel.source_id == source,
                                Channel.ext_id.in_(ext_ids),
                                self.channel_clause(profile, at=at),
                            )
                        )
                    ).scalars()
                    allowed.update(f"ch:{source}:{ext}" for ext in hits)
                if programmes:
                    programme_hits = (
                        await session.execute(
                            select(Programme.id).where(
                                Programme.id.in_(programmes), self.programme_clause(Programme.age_evidence, profile)
                            )
                        )
                    ).scalars()
                    allowed.update(f"prog:{pk}" for pk in programme_hits)
        finally:
            active_policy.reset(reset)
        return allowed

    async def require(self, profile_id: str, ref: str, at: datetime | None = None) -> None:
        if ref not in await self.allowed_refs(profile_id, [ref], at=at):
            raise Forbidden("This content is blocked for the current profile.", code="content_restricted")

    async def filter_payload(self, profile_id: str, payload: Any) -> Any:
        profile = await self.profile(profile_id)
        if not self.is_restricted(profile):
            return payload
        refs: set[str] = set()

        def collect(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                for field in ("id", "programme_id", "channel_id"):
                    ref = value.get(field)
                    if isinstance(ref, str) and ref.startswith(("tmdb:", "lib:", "ch:", "prog:")):
                        refs.add(ref)
                for item in value.values():
                    collect(item)

        collect(payload)
        allowed = await self.allowed_refs(profile_id, list(refs))

        def clean(value: Any) -> Any:
            if isinstance(value, list):
                return [item for entry in value if (item := clean(entry)) is not None]
            if not isinstance(value, dict):
                return value
            identity = value.get("programme_id") or value.get("id")
            if isinstance(identity, str) and identity in refs and identity not in allowed:
                return None
            if "channel_id" in value and not value.get("programme_id") and value["channel_id"] not in allowed:
                return None
            result = {key: clean(item) for key, item in value.items()}
            if "feeds" in result and not result["feeds"]:
                return None
            # Metadata discovery can have unavailable titles absent from the local
            # catalog; filter those here after SQL scoping of stored rows.
            if "results" in result:
                result["play_here"] = [x for x in result["results"] if any(s["playable_in_app"] for s in x["sources"])]
                result["open_in_app"] = [
                    x for x in result["results"] if x["sources"] and not any(s["playable_in_app"] for s in x["sources"])
                ]
            return result

        return clean(payload)

    async def update(self, profile_id: str, data: dict[str, Any]) -> Profile:
        if not self.enabled:
            raise Conflict("Content policy is disabled", code="content_policy_disabled")
        if (
            data.get("allow_unknown")
            or set(data.get("allowed_movie_codes", [])) - MOVIE_CODES
            or set(data.get("allowed_tv_codes", [])) - TV_CODES
        ):
            raise ServiceError(
                "Unknown content must stay blocked; use recognized US classification codes",
                code="invalid_content_policy",
            )
        async with self.db.session() as session:
            profile = await session.get(Profile, profile_id)
            if profile is None:
                raise NotFound("profile not found")
            if profile.is_admin and data.get("restricted"):
                raise ServiceError(
                    "Use a separate restricted profile; the owner profile cannot be restricted.",
                    code="invalid_content_policy",
                )
            profile.content_policy = {
                "restricted": bool(data.get("restricted")),
                "allowed_movie_codes": sorted(set(data.get("allowed_movie_codes", []))),
                "allowed_tv_codes": sorted(set(data.get("allowed_tv_codes", []))),
                "allow_unknown": False,
            }
            profile.token_version += 1
            await session.commit()
        return profile

    async def require_channel_interval(self, profile_id: str, ref: str, start: datetime, stop: datetime) -> None:
        profile = await self.profile(profile_id)
        if not self.is_restricted(profile):
            return
        parts = ref.split(":", 2)
        if len(parts) != 3 or parts[0] != "ch" or stop <= start or stop - start > timedelta(days=1):
            raise Forbidden("Unverified programme interval", code="content_restricted")
        reset = active_policy.set(None)
        try:
            async with self.db.session() as session:
                channel = (
                    await session.execute(
                        select(Channel).where(Channel.source_id == parts[1], Channel.ext_id == parts[2])
                    )
                ).scalar_one_or_none()
                if channel is None:
                    raise Forbidden("Unknown channel", code="content_restricted")
                rows = (
                    await session.execute(
                        select(
                            Programme.start,
                            Programme.stop,
                            self.programme_clause(Programme.age_evidence, profile).label("allowed"),
                        )
                        .where(
                            Programme.feed == channel.source_id,
                            Programme.epg_channel == channel.epg_id,
                            Programme.start < stop,
                            Programme.stop > start,
                        )
                        .order_by(Programme.start)
                        .limit(1001)
                    )
                ).all()
        finally:
            active_policy.reset(reset)
        cursor = start
        if not rows or len(rows) > 1000:
            raise Forbidden("Programme coverage unavailable", code="content_restricted")
        for begins, ends, allowed in rows:
            if not allowed or begins > cursor:
                raise Forbidden("Programme interval is restricted or unknown", code="content_restricted")
            cursor = max(cursor, ends)
        if cursor < stop:
            raise Forbidden("Programme coverage incomplete", code="content_restricted")

    async def require_grant(self, grant: Any, auth: Any) -> None:
        if not self.enabled:
            return
        if not grant.content_ref or not grant.session_id or grant.profile_version is None:
            raise Forbidden("Start playback again with the current profile", code="content_restricted")
        await auth.session_valid(grant.profile_id, grant.profile_version, grant.session_id)
        async with self.db.session() as session:
            source = await session.get(Source, grant.source_id)
        if source is None or not source.enabled:
            raise Forbidden("Source unavailable", code="content_restricted")
        if grant.policy_at and grant.policy_until and grant.content_ref.startswith("ch:"):
            await self.require_channel_interval(
                grant.profile_id,
                grant.content_ref,
                datetime.fromisoformat(grant.policy_at),
                datetime.fromisoformat(grant.policy_until),
            )
        else:
            await self.require(grant.profile_id, grant.content_ref)

    async def guard_stream(self, grant: Any, auth: Any, stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        last_check = 0.0
        try:
            async for chunk in stream:
                if time.monotonic() - last_check >= 1:
                    await self.require_grant(grant, auth)
                    last_check = time.monotonic()
                yield chunk
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    async def require_segment(self, grant: Any, auth: Any, path: Path, manifest: Path) -> None:
        await self.require_grant(grant, auth)
        if not self.enabled or not grant.live or not grant.content_ref or not grant.content_ref.startswith("ch:"):
            return
        if not self.is_restricted(await self.profile(grant.profile_id)):
            return
        # A signed channel grant is not permission for every programme carried
        # by the channel. Bind each segment to its actual wall-clock interval.
        cursor = None
        duration = None
        try:
            text = manifest.read_text()
            if len(text) > 2_000_000:
                raise ValueError("manifest too large")
            for line in text.splitlines():
                if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
                    cursor = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
                elif line.startswith("#EXTINF:"):
                    duration = float(line.split(":", 1)[1].split(",", 1)[0])
                elif line and not line.startswith("#"):
                    if cursor is None or duration is None or duration <= 0 or duration > 120:
                        raise ValueError("missing segment interval")
                    ends = cursor + timedelta(seconds=duration)
                    if Path(line).name == path.name:
                        if ends > datetime.now(UTC) + timedelta(seconds=2):
                            raise ValueError("future segment")
                        await self.require_channel_interval(grant.profile_id, grant.content_ref, cursor, ends)
                        return
                    cursor, duration = ends, None
        except (OSError, ValueError, TypeError):
            pass
        raise Forbidden("Segment programme evidence unavailable", code="content_restricted")
