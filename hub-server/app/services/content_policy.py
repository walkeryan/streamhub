"""Server content policy: what a profile may see, enforced where rows are read. No inferred ages
or client-only gates.

Two regimes decide a profile's rule set. With ``CONTENT_POLICY_ENABLED`` (legacy, owner-PIN
regime) it is the policy stored on the profile, for every profile. Without it, the default, it
is derived from the user's effective limits (rating ceilings, unknown ratings), and the engine
only runs for a user those limits restrict. ``applies_to`` is the one question a caller asks.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import String, and_, case, cast, exists, func, literal, not_, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import with_loader_criteria

from app.infra.db import Database
from app.infra.models import (
    AgeClassification,
    Channel,
    OwnerCredential,
    Profile,
    Programme,
    RevokedSession,
    Role,
    Source,
    Title,
    User,
)
from app.services.classification import MOVIE_CODES, TV_CODES
from app.services.errors import Conflict, Forbidden, NotFound, ServiceError
from app.services.permissions import MOVIE_ORDER, TV_ORDER, Limits, effective_limits

TOP_KINDS = ("movie", "vod", "show", "series")
SHOW_KINDS = ("show", "series")
# Evidence older than this no longer proves a rating; the classification job refreshes it sooner.
EVIDENCE_FRESH = timedelta(days=7)

active_principal: ContextVar[Any] = ContextVar("content_principal", default=None)

active_policy: ContextVar[tuple[ContentPolicy, Profile] | None] = ContextVar("content_policy", default=None)

# Where ``ContentPolicy.profile`` leaves the user's effective limits on the row it returns.
_LIMITS = "_policy_limits"


def restricts(limits: Limits) -> bool:
    """Do these limits keep any title from their user? A stream count alone does not."""
    return bool(limits.rating_ceiling_movie or limits.rating_ceiling_tv or not limits.allow_unrated)


def rating_key(principal: Any) -> tuple[str | None, str | None, bool] | None:
    """Part of any cache key for a listing computed under this principal's rating limits."""
    limits = getattr(principal, "limits", None)
    if limits is None:
        return None
    return limits.rating_ceiling_movie, limits.rating_ceiling_tv, limits.allow_unrated


def _up_to(order: tuple[str, ...], ceiling: str | None) -> list[str]:
    return list(order[: order.index(ceiling) + 1]) if ceiling in order else list(order)


class ContentPolicy:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db, self.settings = db, settings
        self.owner_pin: Any = None

    @property
    def enabled(self) -> bool:
        """The legacy owner-PIN regime. Not "is anything filtered": ask ``applies_to`` for that."""
        return bool(self.settings.content_policy_enabled)

    def applies_to(self, principal: Any) -> bool:
        """Does the engine run for this caller's requests?

        Under the legacy flag, for everyone (each stored policy then decides what it filters).
        Otherwise only for a user whose own limits restrict them, as loaded for this request.
        """
        if self.enabled:
            return True
        limits = getattr(principal, "limits", None)
        return limits is not None and restricts(limits)

    async def profile(self, profile_id: str, limits: Limits | None = None) -> Profile:
        """The profile with its user's effective limits attached, ready for ``values``.

        ``limits`` skips the lookup for a caller that already validated them this request.
        A profile no user owns yet has nothing to derive a ceiling from.
        """
        async with self.db.session() as session:
            row = await session.get(Profile, profile_id)
            if row is None:
                raise NotFound("profile not found")
            if limits is None and row.user_id and not self.enabled:
                user = await session.get(User, row.user_id)
                role = await session.get(Role, user.role_id) if user is not None and user.role_id else None
                if user is not None and role is not None:
                    limits = effective_limits(role.limits or {}, user.limits or {})
        setattr(row, _LIMITS, limits)
        return row

    async def scope(self, profile_id: str) -> tuple[ContentPolicy, Profile] | None:
        """What to set ``active_policy`` to for work done on a profile's behalf outside a request."""
        profile = await self.profile(profile_id)
        return (self, profile) if self.restricts(profile) else None

    def values(self, profile: Profile) -> dict[str, Any]:
        """The rule set for a profile: whether it is restricted and which rating codes it admits."""
        if self.enabled:
            stored = profile.content_policy or {}
            return {
                "restricted": bool(stored.get("restricted", profile.kids and not profile.is_admin)),
                "allowed_movie_codes": list(stored.get("allowed_movie_codes", [])),
                "allowed_tv_codes": list(stored.get("allowed_tv_codes", [])),
                "allow_unknown": False,
            }
        limits: Limits | None = getattr(profile, _LIMITS, None)
        if limits is None or not restricts(limits):
            return {
                "restricted": False,
                "allowed_movie_codes": list(MOVIE_ORDER),
                "allowed_tv_codes": list(TV_ORDER),
                "allow_unknown": True,
            }
        return {
            "restricted": True,
            "allowed_movie_codes": _up_to(MOVIE_ORDER, limits.rating_ceiling_movie),
            "allowed_tv_codes": _up_to(TV_ORDER, limits.rating_ceiling_tv),
            "allow_unknown": limits.allow_unrated,
        }

    def is_restricted(self, profile: Profile) -> bool:
        """Legacy regime only: a profile the owner PIN stands guard over. Never true without the flag."""
        return self.enabled and self.values(profile)["restricted"]

    def restricts(self, profile: Profile) -> bool:
        """Does the engine filter anything for this profile (loaded by ``profile``), in either regime?"""
        return bool(self.values(profile)["restricted"])

    def _records(self, evidence: Any, profile: Profile) -> tuple[Any, Any, Any]:
        """One evidence document's records, the "known US rating" test and the "admitted code" test."""
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
        return records, known, allowed

    def evidence_clause(self, evidence: Any, profile: Profile) -> Any:
        """Proven within the ceiling: one known US rating, and it is an admitted one."""
        records, known, allowed = self._records(evidence, profile)
        return and_(
            func.json_extract(evidence, "$.status") == "known",
            func.json_extract(evidence, "$.assessment_region") == "US",
            exists(select(1).select_from(records).where(known, allowed)),
            ~exists(select(1).select_from(records).where(known, ~allowed)),
        )

    def above_clause(self, evidence: Any, profile: Profile) -> Any:
        """Rated above the ceiling: any known US rating that is not admitted, however old or contested."""
        records, known, allowed = self._records(evidence, profile)
        return exists(select(1).select_from(records).where(known, ~allowed))

    def programme_clause(self, evidence: Any, profile: Profile) -> Any:
        if self.values(profile)["allow_unknown"]:
            return not_(self.above_clause(evidence, profile))
        return and_(
            self.evidence_clause(evidence, profile),
            func.json_extract(evidence, "$.scope") == "programme",
            func.json_extract(evidence, "$.provider") == "xmltv",
        )

    @staticmethod
    def _subject(cols: Any) -> Any:
        """The classification key of a movie or show row (``cols``: the entity or a table alias's columns)."""
        mt = case((cols.kind.in_(("movie", "vod")), "movie"), else_="tv")
        return literal("tmdb:") + mt + ":" + cast(cols.tmdb_id, String)

    @staticmethod
    def _typed() -> list[Any]:
        """Classification rows that are what their key says: TMDB's, for that movie or that series."""
        return [
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
        ]

    def _proven(self, profile: Profile) -> list[Any]:
        return [
            *self._typed(),
            AgeClassification.observed_at >= datetime.now(UTC) - EVIDENCE_FRESH,
            self.evidence_clause(AgeClassification.evidence, profile),
        ]

    def _admitted(self, cols: Any, profile: Profile) -> Any:
        """A movie or show row with fresh proof that it is within the ceiling."""
        return and_(
            cols.tmdb_id > 0,
            self._subject(cols).in_(select(AgeClassification.subject_key).where(*self._proven(profile))),
        )

    def _refused(self, cols: Any, profile: Profile) -> Any:
        """A movie or show row marked adult, or known to be rated above the ceiling."""
        rated_above = exists(
            select(1).where(
                AgeClassification.subject_key == self._subject(cols),
                *self._typed(),
                self.above_clause(AgeClassification.evidence, profile),
            )
        )
        # A row without a TMDB id has no key, so nothing matches: unknown, never NULL.
        return or_(cols.adult.is_(True), rated_above)

    def title_clause(self, profile: Profile, title: Any = Title) -> Any:
        if self.enabled:
            # Episode/season IDs are different namespaces. Without explicit episode
            # classification, restricted playback stays unknown rather than inheriting.
            return and_(title.kind.in_(TOP_KINDS), self._admitted(title, profile))
        # Limits regime. A season or episode is judged by its show, one or two rows up; plain
        # table aliases, so the scope this clause is part of is not applied to them in turn.
        parent, grand = Title.__table__.alias("policy_parent"), Title.__table__.alias("policy_show")

        def above(child: Any, row: Any) -> Any:
            return and_(row.c.source_id == child.source_id, row.c.ext_id == child.parent_ext_id)

        def shows(test: Any) -> Any:
            direct = exists(
                select(1).select_from(parent).where(above(title, parent), parent.c.kind.in_(SHOW_KINDS), test(parent.c))
            )
            via_season = exists(
                select(1)
                .select_from(parent.join(grand, above(parent.c, grand)))
                .where(above(title, parent), parent.c.kind == "season", grand.c.kind.in_(SHOW_KINDS), test(grand.c))
            )
            return or_(direct, via_season)

        top = title.kind.in_(TOP_KINDS)
        if self.values(profile)["allow_unknown"]:
            # Everything but what is known to be over the ceiling. An unmatched episode is unknown.
            own = not_(self._refused(title, profile))
            inherited = not_(shows(lambda cols: self._refused(cols, profile)))
        else:
            own = self._admitted(title, profile)
            inherited = shows(lambda cols: and_(cols.adult.is_not(True), self._admitted(cols, profile)))
        # Marked adult is over every ceiling, whatever a rating says.
        return and_(title.adult.is_not(True), or_(and_(top, own), and_(not_(top), inherited)))

    def channel_clause(self, profile: Profile, channel: Any = Channel, at: datetime | None = None) -> Any:
        p = Programme.__table__.alias("policy_airing")
        at = at or datetime.now(UTC)
        current = and_(p.c.feed == channel.source_id, p.c.epg_channel == channel.epg_id, p.c.start <= at, p.c.stop > at)
        allowed = self.programme_clause(p.c.age_evidence, profile)
        nothing_refused = ~exists(select(1).select_from(p).where(current, not_(func.coalesce(allowed, False))))
        if self.values(profile)["allow_unknown"]:
            # No guide, or a guide that does not say: unknown, and unknown is admitted here.
            return nothing_refused
        return and_(exists(select(1).select_from(p).where(current, allowed)), nothing_refused)

    def scope_statement(self, statement: Any, profile: Profile) -> Any:
        if not self.restricts(profile):
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
        if not self.restricts(profile):
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
                    allowed.update(await self._canonical(session, profile, canonical))
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

    async def _canonical(self, session: Any, profile: Profile, refs: list[str]) -> set[str]:
        """Which ``tmdb:`` ids the profile's rule set admits."""
        keys = select(AgeClassification.subject_key).where(AgeClassification.subject_key.in_(refs))
        if self.enabled or not self.values(profile)["allow_unknown"]:
            found = set((await session.execute(keys.where(*self._proven(profile)))).scalars())
            if self.enabled:
                return found
        else:
            above = keys.where(*self._typed(), self.above_clause(AgeClassification.evidence, profile))
            found = set(refs) - set((await session.execute(above)).scalars())
        if not found:
            return found
        # A title the catalog marks adult is over every ceiling under its canonical id too.
        rows = await session.execute(
            select(Title.kind, Title.tmdb_id).where(
                Title.adult.is_(True),
                Title.kind.in_(TOP_KINDS),
                Title.tmdb_id.in_({int(ref.rsplit(":", 1)[1]) for ref in found}),
            )
        )
        adult = {f"tmdb:{'movie' if kind in ('movie', 'vod') else 'tv'}:{tmdb}" for kind, tmdb in rows.all()}
        return found - adult

    async def require(self, profile_id: str, ref: str, at: datetime | None = None) -> None:
        if ref not in await self.allowed_refs(profile_id, [ref], at=at):
            raise Forbidden("This content is blocked for the current profile.", code="content_restricted")

    async def filter_payload(self, profile_id: str, payload: Any) -> Any:
        profile = await self.profile(profile_id)
        if not self.restricts(profile):
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

    async def update(
        self, profile_id: str, data: dict[str, Any], *, authorization: dict[str, Any] | None = None
    ) -> Profile:
        if not self.enabled:
            raise Conflict("Content policy is disabled", code="content_policy_disabled")
        if self.owner_pin is None or not await self.owner_pin.configured():
            raise Conflict("Set up the owner PIN before changing parental policy", code="owner_pin_setup_required")
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
            if authorization is not None:
                a = authorization
                marker = "action:" + str(a["jti"])
                live = select(literal(marker), literal(datetime.now(UTC) + timedelta(days=1))).where(
                    select(Profile.id)
                    .where(
                        Profile.id == a["sub"],
                        Profile.token_version == a["ver"],
                        Profile.is_admin.is_(True),
                        func.coalesce(func.json_extract(Profile.content_policy, "$.restricted"), False).is_(False),
                    )
                    .exists(),
                    ~select(RevokedSession.session_id)
                    .where(RevokedSession.session_id.in_([str(a["sid"]), marker]))
                    .exists(),
                    select(Profile.id)
                    .where(Profile.id == profile_id, Profile.token_version == a["target_ver"])
                    .exists(),
                    select(OwnerCredential.id)
                    .where(
                        OwnerCredential.id == 1,
                        OwnerCredential.credential_version == a["opv"],
                        OwnerCredential.password_hash.is_not(None),
                    )
                    .exists(),
                    literal(a["target"]) == profile_id,
                    literal(a["action"]) == "content_policy",
                    literal(a["exp"]) > datetime.now(UTC).timestamp(),
                )
                claimed = await session.execute(
                    insert(RevokedSession)
                    .from_select(["session_id", "expires_at"], live)
                    .on_conflict_do_nothing()
                    .returning(RevokedSession.session_id)
                )
                if claimed.scalar_one_or_none() is None:
                    raise Forbidden("Owner policy approval expired or already used", code="owner_pin_invalid")
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
        if not self.restricts(profile):
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
        if self.values(profile)["allow_unknown"]:
            # Gaps and unrated programmes are unknown, which this profile is allowed; only a
            # programme known to be over the ceiling closes the interval.
            if len(rows) > 1000 or any(not allowed for _, _, allowed in rows):
                raise Forbidden("Programme interval is restricted", code="content_restricted")
            return
        if not rows or len(rows) > 1000:
            raise Forbidden("Programme coverage unavailable", code="content_restricted")
        for begins, ends, allowed in rows:
            if not allowed or begins > cursor:
                raise Forbidden("Programme interval is restricted or unknown", code="content_restricted")
            cursor = max(cursor, ends)
        if cursor < stop:
            raise Forbidden("Programme coverage incomplete", code="content_restricted")

    async def _governs(self, profile_id: str) -> bool:
        """Is a grant held by this profile subject to the engine right now?"""
        if self.enabled:
            return True
        try:
            return self.restricts(await self.profile(profile_id))
        except NotFound:
            return False  # nobody to restrict; the session check refuses a grant for a removed profile

    async def require_grant(self, grant: Any, auth: Any) -> None:
        if not await self._governs(grant.profile_id):
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
        if not grant.live or not grant.content_ref or not grant.content_ref.startswith("ch:"):
            return
        try:
            profile = await self.profile(grant.profile_id)
        except NotFound:
            return
        if not self.restricts(profile):
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
