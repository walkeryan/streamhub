"""Discovery learned locally from one profile's viewing and explicit ratings."""

import asyncio
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import aliased

from app.container import Container
from app.infra.models import Progress, RecommendationVote, Title
from app.services import ids
from app.services.auth import Principal
from app.services.errors import ServiceError
from app.services.recommendations import recommendations, title_ref_match
from app.services.tiles import TileContext, hub_id_for_title
from app.services.title_extras import similar_titles


async def episodes(c: Container, p: Principal, ctx: TileContext, today: date) -> tuple[list[Any], list[Any]]:
    season = aliased(Title)
    series = aliased(Title)
    enabled = [s.id for s in ctx.sources.values() if s.enabled and ctx.is_in_app(s.type)]
    ancestry = (
        select(Title, season, series)
        .join(season, and_(season.source_id == Title.source_id, season.ext_id == Title.parent_ext_id))
        .outerjoin(series, and_(series.source_id == season.source_id, series.ext_id == season.parent_ext_id))
        .where(
            Title.kind == "episode",
            Title.source_id.in_(enabled),
            Title.adult.is_not(True),
            season.adult.is_not(True),
            series.adult.is_not(True),
        )
    )
    watched = (
        ancestry.join(Progress, title_ref_match())
        .add_columns(Progress.updated_at)
        .where(Progress.profile_id == p.profile_id, Progress.completed.is_(True))
        .order_by(Progress.updated_at.desc())
        .limit(150)
    )
    shows: dict[str, tuple[Title, datetime, tuple[int, int]]] = {}
    async with c.db.session() as s:
        for episode, parent, grandparent, at in (await s.execute(watched)).all():
            show = parent if parent.kind in ids.SHOW_KINDS else grandparent
            if not show or show.kind not in ids.SHOW_KINDS or episode.episode_number is None:
                continue
            key = hub_id_for_title(show)
            number = (episode.season_number or parent.season_number or 1, episode.episode_number)
            if key in shows:
                old, last, index = shows[key]
                shows[key] = (old, max(last, at), max(index, number))
            elif len(shows) < 24:
                shows[key] = (show, at, number)
        new: dict[tuple[str, int, int], tuple[datetime, dict[str, Any]]] = {}
        if shows:
            parents = [show.id for show, _, _ in shows.values()]
            tmdb = [show.tmdb_id for show, _, _ in shows.values() if show.tmdb_id]
            query = (
                ancestry.where(
                    season.id.in_(parents)
                    | series.id.in_(parents)
                    | (season.kind.in_(ids.SHOW_KINDS) & season.tmdb_id.in_(tmdb))
                    | series.tmdb_id.in_(tmdb),
                    Title.added_at > min(last for _, last, _ in shows.values()),
                )
                .order_by(Title.added_at.desc())
                .limit(1000)
            )
            for row, parent, grandparent in (await s.execute(query)).all():
                show = parent if parent.kind in ids.SHOW_KINDS else grandparent
                if not show or row.episode_number is None:
                    continue
                key = hub_id_for_title(show)
                match = shows.get(key)
                number = (row.season_number or parent.season_number or 1, row.episode_number)
                ref = hub_id_for_title(row)
                if (
                    not match
                    or not row.added_at
                    or row.added_at <= match[1]
                    or number <= match[2]
                    or (ref in ctx.progress and (ctx.progress[ref].completed or ctx.progress[ref].position_ms > 0))
                ):
                    continue
                tile = ctx.title_tile(row)
                tile["subtitle"] = f"{show.title} · S{number[0]} E{number[1]}"
                new.setdefault((key, *number), (row.added_at, tile))

    upcoming: list[dict[str, Any]] = []
    if c.tmdb.enabled:
        slots = asyncio.Semaphore(4)

        async def schedule(show: Title) -> None:
            if not show.tmdb_id:
                return
            async with slots:
                try:
                    async with asyncio.timeout(3):
                        rows = await c.tmdb.upcoming_episodes(show.tmdb_id, today)
                except (TimeoutError, OSError):
                    return
            for row in rows:
                upcoming.append(
                    {
                        "show": ctx.title_tile(show),
                        "season": row.get("season_number"),
                        "episode": row.get("episode_number"),
                        "title": row.get("name") or "Upcoming episode",
                        "air_date": row["air_date"],
                    }
                )

        await asyncio.gather(*(schedule(show) for show, _, _ in shows.values()))
    upcoming.sort(key=lambda item: (item["air_date"], item["show"]["title"], item["episode"] or 0))
    return [tile for _, tile in sorted(new.values(), key=lambda pair: pair[0], reverse=True)][:30], upcoming


async def feed(c: Container, p: Principal, today: date) -> dict[str, Any]:
    ctx = await c.catalog.tile_context(p.profile_id)
    local, reason = await recommendations(c.catalog, p.profile_id, ctx, 30)
    async with c.db.session() as s:
        votes = list(
            await s.scalars(
                select(RecommendationVote)
                .where(RecommendationVote.profile_id == p.profile_id)
                .order_by(RecommendationVote.updated_at.desc())
            )
        )
    ratings = {v.ref_id: v.vote for v in votes}
    meaningful = sorted(
        (v for v in ctx.progress.values() if v.completed or v.position_ms >= 120000),
        key=lambda v: v.updated_at,
        reverse=True,
    )
    new, soon = await episodes(c, p, ctx, today)
    suggestions = list(local)
    if c.tmdb.enabled:
        # Ask the metadata source about a title. Personal viewing records and votes stay on this hub.
        seeds = list(
            dict.fromkeys(
                [v.ref_id for v in votes if v.vote == "up"]
                + [v.ref_id for v in meaningful if ratings.get(v.ref_id) != "down"]
            )
        )[:3]
        extras = await asyncio.gather(*(similar_titles(c.catalog, ref, p.profile_id) for ref in seeds))
        for group in extras:
            suggestions.extend(group["tiles"])
        if not seeds or not any(t["kind"] in ids.MOVIE_KINDS and not t.get("sources") for t in suggestions):
            try:
                newest = await c.catalog.discover("movies", p.profile_id, sort="newest")
                suggestions.extend(newest.get("tiles", []))
            except ServiceError:
                pass
    seen = {r.ref_id for r in meaningful}
    chosen: dict[str, dict[str, Any]] = {}
    for t in suggestions:
        key = t["id"]
        if key not in seen and ratings.get(key) != "down":
            chosen.setdefault(key, t)
    movies = [t for t in chosen.values() if t["kind"] in ids.MOVIE_KINDS]
    # Prioritize recent releases within the learned recommendations.
    movies.sort(key=lambda t: not (t.get("year") and today.year - 1 <= t["year"] <= today.year))
    series = [t for t in chosen.values() if t["kind"] in ids.SHOW_KINDS]
    return await c.requests.annotate(
        p,
        dict(
            new_episodes=new,
            coming_up=soon,
            movies=movies[:30],
            series=series[:20],
            reason=reason,
            history_used=bool(meaningful),
            feedback={k: v for k, v in ratings.items() if k in chosen},
        ),
    )
