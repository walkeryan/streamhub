"""What a title page loads after the title itself: the household's activity and "More like this".

Both are separate requests so the page never waits on them: activity reads only this database,
"More like this" asks the metadata provider (cached like its other answers).

Activity rules (the owner's, 2026-11-18):

- Only profiles the viewer sees on "Who's watching?" (active users whose role, or their own
  override, puts them on shared devices), plus the viewer as "You".
- An adult title never shows activity, whoever asks.
- A kids profile sees other kids profiles' activity only, never a grown-up's.
- One entry per profile (the newest thing they did), newest first, at most ``ACTIVITY_LIMIT``.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from app.infra.models import Profile, Progress, Title, User, Watchlist
from app.services import ids
from app.services.catalog import CatalogService
from app.services.sessions import SessionsService
from app.services.tiles import badge_for_source, hub_id_for_title, meta_tile, sort_badges

log = logging.getLogger(__name__)

ACTIVITY_LIMIT = 6
SIMILAR_LIMIT = 20
# The metadata provider's answer is cached; a cold one may take this long before the row is left empty.
SIMILAR_TIMEOUT_S = 8.0


@dataclass
class Subject:
    """The film or series a title id is about, with every library row behind it."""

    media_type: str | None = None  # movie | tv, when the metadata provider knows it
    tmdb_id: int | None = None
    refs: set[str] = field(default_factory=set)  # the hub and lib ids the title itself is saved under
    rows: list[Title] = field(default_factory=list)  # its library rows (every copy), and the page's own row
    adult: bool = False


async def subject_of(catalog: CatalogService, hub_id: str) -> Subject:
    """Climb from a season or episode to its series; gather every library copy of the result."""
    subject = Subject(refs={hub_id})
    try:
        h = ids.parse(hub_id)
    except ValueError:
        return subject
    top: Title | None = None
    if h.scheme == "lib":
        row = await catalog.title_row(h.a, h.b)
        if row is None:
            return subject
        subject.rows.append(row)
        top = row
        async with catalog._db.session() as s:
            for _ in range(2):  # episode -> season -> series
                if top.kind not in ("season", "episode") or not top.parent_ext_id:
                    break
                above = (
                    await s.execute(
                        select(Title).where(Title.source_id == top.source_id, Title.ext_id == top.parent_ext_id)
                    )
                ).scalar_one_or_none()
                if above is None:
                    break
                subject.rows.append(above)
                top = above
        if top.kind in ("season", "episode"):
            return _finish(subject)  # an orphan: nothing above it is known
        subject.refs |= {hub_id_for_title(top), ids.lib_id(top.source_id, top.ext_id)}
        if top.tmdb_id:
            subject.media_type = ids.tmdb_type_for_kind(top.kind)
            subject.tmdb_id = top.tmdb_id
    elif h.scheme == "tmdb" and h.a in ("movie", "tv") and h.b.isdigit():
        subject.media_type, subject.tmdb_id = h.a, int(h.b)
    else:
        return subject
    if subject.media_type and subject.tmdb_id:
        copies = await catalog.rows_for_tmdb(subject.media_type, subject.tmdb_id)
        subject.refs.add(ids.tmdb_id(subject.media_type, subject.tmdb_id))
        for copy in copies:
            subject.refs.add(ids.lib_id(copy.source_id, copy.ext_id))
            if all(copy.id != r.id for r in subject.rows):
                subject.rows.append(copy)
    return _finish(subject)


def _finish(subject: Subject) -> Subject:
    # A canonical id is adult if any row behind it is, as ``require_adult`` decides.
    subject.adult = any(r.adult for r in subject.rows)
    return subject


# ------------------------------------------------------------------ activity
async def _episodes(catalog: CatalogService, shows: list[Title]) -> dict[str, tuple[int | None, int | None]]:
    """lib id -> (season, episode) of every stored episode of these series copies."""
    out: dict[str, tuple[int | None, int | None]] = {}
    if not shows:
        return out
    async with catalog._db.session() as s:
        for show in shows:
            seasons = list(
                (
                    await s.execute(
                        select(Title).where(
                            Title.source_id == show.source_id,
                            Title.parent_ext_id == show.ext_id,
                            Title.kind == "season",
                        )
                    )
                ).scalars()
            )
            numbers = {x.ext_id: x.season_number for x in seasons}
            episodes = (
                await s.execute(
                    select(Title).where(
                        Title.source_id == show.source_id,
                        Title.parent_ext_id.in_([show.ext_id, *numbers]),
                        Title.kind == "episode",
                    )
                )
            ).scalars()
            for e in episodes:
                season = e.season_number if e.season_number is not None else numbers.get(e.parent_ext_id or "")
                out[ids.lib_id(e.source_id, e.ext_id)] = (season, e.episode_number)
    return out


async def _visible_profiles(catalog: CatalogService, viewer_id: str) -> dict[str, tuple[Profile, str, str | None]]:
    """profile id -> (profile, display name, picture) of everyone whose activity this viewer may see."""
    async with catalog._db.session() as s:
        viewer = await s.get(Profile, viewer_id)
        if viewer is None:
            return {}
        me = await s.get(User, viewer.user_id) if viewer.user_id else None
        out = {viewer.id: (viewer, me.display_name if me else viewer.name, me.picture if me else None)}
        for user, profile, _role in await SessionsService._switchable(s):
            if profile.id == viewer.id:
                continue
            # Grown-ups' viewing is not a kids profile's business.
            if viewer.kids and not profile.kids:
                continue
            out[profile.id] = (profile, user.display_name or profile.name, user.picture)
    return out


def _minutes_left(p: Progress) -> int | None:
    if not p.duration_ms or p.position_ms <= 0:
        return None
    return max(1, math.ceil((p.duration_ms - p.position_ms) / 60_000))


async def title_activity(catalog: CatalogService, hub_id: str, viewer_id: str) -> dict[str, Any]:
    """What the household did with this title (its series, for a season or an episode). See the module notes."""
    out: dict[str, Any] = {"id": hub_id, "entries": []}
    subject = await subject_of(catalog, hub_id)
    if subject.adult:
        return out
    shows = [r for r in subject.rows if r.kind in ids.SHOW_KINDS]
    episodes = await _episodes(catalog, shows)
    # The last episode number of each season, to say "Finished Season 2" rather than "Watched S2 · E8".
    last: dict[int | None, int] = {}
    for season, episode in episodes.values():
        if episode is not None:
            last[season] = max(last.get(season, 0), episode)
    profiles = await _visible_profiles(catalog, viewer_id)
    if not profiles:
        return out
    refs = subject.refs | set(episodes)
    async with catalog._db.session() as s:
        progress = list(
            (
                await s.execute(
                    select(Progress)
                    .where(Progress.profile_id.in_(list(profiles)), Progress.ref_id.in_(list(refs)))
                    .where(or_(Progress.completed.is_(True), Progress.position_ms > 0))
                    .order_by(Progress.updated_at.desc())
                )
            ).scalars()
        )
        listed = list(
            (
                await s.execute(
                    select(Watchlist).where(
                        Watchlist.profile_id.in_(list(profiles)), Watchlist.ref_id.in_(list(subject.refs))
                    )
                )
            ).scalars()
        )
    latest: dict[str, dict[str, Any]] = {}
    for p in progress:  # newest first: the first row per profile is theirs
        if p.profile_id in latest:
            continue
        season, episode = episodes.get(p.ref_id, (None, None))
        entry: dict[str, Any] = {
            "action": "finished" if p.completed else "watching",
            "at": _utc(p.updated_at),
            "season_number": season,
            "episode_number": episode,
        }
        if p.completed:
            entry["season_finished"] = episode is not None and last.get(season) == episode
        else:
            entry["progress_pct"] = (
                round(min(100.0, 100.0 * p.position_ms / p.duration_ms), 1) if p.duration_ms else None
            )
            entry["minutes_left"] = _minutes_left(p)
        latest[p.profile_id] = entry
    for w in listed:
        current = latest.get(w.profile_id)
        if current is None or _utc(w.added_at) > current["at"]:
            latest[w.profile_id] = {"action": "listed", "at": _utc(w.added_at)}
    entries = []
    for profile_id, entry in latest.items():
        profile, name, picture = profiles[profile_id]
        entries.append(
            {
                "profile_id": profile_id,
                "name": name,
                "avatar": profile.avatar,
                "picture": picture,
                "me": profile_id == viewer_id,
                **entry,
            }
        )
    entries.sort(key=lambda e: e["at"], reverse=True)
    out["entries"] = entries[:ACTIVITY_LIMIT]
    return out


def _utc(moment: datetime) -> datetime:
    # Stored moments are UTC; one read back without its zone is still UTC.
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


# ------------------------------------------------------------------ more like this
async def similar_titles(catalog: CatalogService, hub_id: str, profile_id: str) -> dict[str, Any]:
    """The metadata provider's recommendations for this title (its series, for a season or an episode).

    Adult titles stay out like on every list: what the provider marks adult, and any title a library
    row of which is marked adult. A title that plays here carries its library badges, so the page can
    tick it; nothing else is looked up per title (no watch-provider calls for a row of twenty).
    The rating ceiling is applied to the response by the catalog router, as for every listing.
    """
    out: dict[str, Any] = {"id": hub_id, "tiles": []}
    if not catalog.meta.enabled:
        return out
    subject = await subject_of(catalog, hub_id)
    if subject.adult or not subject.media_type or not subject.tmdb_id:
        return out
    try:
        async with asyncio.timeout(SIMILAR_TIMEOUT_S):
            metas = await catalog.meta.recommendations(subject.media_type, subject.tmdb_id, SIMILAR_LIMIT)
    except Exception as exc:
        log.info("recommendations unavailable", extra={"err": type(exc).__name__})
        return out
    metas = [m for m in metas if m.media_type == subject.media_type and m.tmdb_id != subject.tmdb_id]
    if not metas:
        return out
    kinds = ids.MOVIE_KINDS if subject.media_type == "movie" else ids.SHOW_KINDS
    async with catalog._db.session() as s:
        adult = set(
            (
                await s.execute(
                    select(Title.tmdb_id).where(
                        Title.tmdb_id.in_([m.tmdb_id for m in metas]), Title.kind.in_(kinds), Title.adult.is_(True)
                    )
                )
            ).scalars()
        )
    metas = [m for m in metas if m.tmdb_id not in adult]
    ctx = await catalog.tile_context(profile_id)
    matches = await catalog.availability.library_matches_many(metas, general=True)
    tiles = []
    for meta in metas[:SIMILAR_LIMIT]:
        hub = ids.tmdb_id(meta.media_type, meta.tmdb_id)
        rows = [r for r in matches.get(hub, []) if r.source_id in ctx.sources and ctx.sources[r.source_id].enabled]
        badges = [
            badge_for_source(
                ctx.sources[r.source_id], in_app=ctx.is_in_app(ctx.sources[r.source_id].type), quality=r.quality
            )
            for r in rows
        ]
        refs = [ids.lib_id(r.source_id, r.ext_id) for r in rows]
        tiles.append(meta_tile(meta, sort_badges(badges, ctx.sources), ctx.favorites, ctx.progress_pct(hub, *refs)))
    out["tiles"] = tiles
    return out
