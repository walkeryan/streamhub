"""Bounded local genre suggestions; no provider history or external requests."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import Integer, and_, cast, func, or_, select, tuple_
from sqlalchemy.orm import aliased

from app.infra.models import Profile, Progress, Source, Title
from app.services.catalog import CatalogService
from app.services.tiles import TileContext, hub_id_for_title

HISTORY_LIMIT = 50
CANDIDATE_LIMIT = 500


def title_ref_match() -> Any:
    # Parse the small profile-history side, leaving catalog index columns bare.
    # Concatenating every Title's ref defeats the source/ext and TMDB indexes.
    tail = func.substr(Progress.ref_id, 5)
    separator = func.instr(tail, ":")
    library = and_(
        Progress.ref_id.like("lib:%"),
        Title.source_id == func.substr(tail, 1, separator - 1),
        Title.ext_id == func.substr(tail, separator + 1),
    )
    movie = and_(
        Progress.ref_id.like("tmdb:movie:%"),
        Title.kind.in_(("movie", "vod")),
        Title.tmdb_id == cast(func.substr(Progress.ref_id, 12), Integer),
    )
    show = and_(
        Progress.ref_id.like("tmdb:tv:%"),
        Title.kind.in_(("show", "series")),
        Title.tmdb_id == cast(func.substr(Progress.ref_id, 9), Integer),
    )
    return or_(library, movie, show)


async def recommendations(
    catalog: CatalogService, profile_id: str, ctx: TileContext, limit: int = 20
) -> tuple[list[dict[str, Any]], str]:
    """Return unseen available titles, using only meaningful viewing by this profile.

    Genre metadata is a recommendation signal, not a content-safety or audio claim.
    Kids profiles are omitted until a real parental policy is available.
    """
    async with catalog._db.session() as session:
        profile = await session.get(Profile, profile_id)
        if profile is None or profile.kids:
            return [], "Explore your library · Not personalized"
        recent = (
            select(Progress.id)
            .where(
                Progress.profile_id == profile_id,
                or_(Progress.ref_id.like("lib:%"), Progress.ref_id.like("tmdb:%")),
                or_(Progress.completed.is_(True), Progress.position_ms >= 120_000),
            )
            .order_by(Progress.updated_at.desc())
            .limit(HISTORY_LIMIT)
        )
        seed_query = (
            select(Title)
            .join(Progress, title_ref_match())
            .join(Source, Source.id == Title.source_id)
            .where(
                Progress.id.in_(recent),
                Source.enabled.is_(True),
            )
            .limit(CANDIDATE_LIMIT)
        )
        seeds = list((await session.execute(seed_query)).scalars())
        # Episodes often carry no genres; resolve their local season/show ancestry
        # with two bounded batches, without fetching provider metadata.
        lineage = list(seeds)
        for _ in range(2):
            parent_keys = {(r.source_id, r.parent_ext_id) for r in lineage if r.parent_ext_id}
            if not parent_keys:
                break
            lineage = list(
                (
                    await session.execute(
                        select(Title)
                        .where(
                            tuple_(Title.source_id, Title.ext_id).in_(parent_keys),
                        )
                        .limit(CANDIDATE_LIMIT)
                    )
                ).scalars()
            )
            seeds.extend(lineage)
    weights: Counter[str] = Counter()
    labels: dict[str, str] = {}
    seen = set()
    watched_ancestors = set()
    for row in seeds:
        key = hub_id_for_title(row)
        watched_ancestors.add(key)
        if key in seen:
            continue
        seen.add(key)
        for genre in {g.strip() for g in row.genres or [] if isinstance(g, str) and g.strip()}:
            weights[genre.casefold()] += 1
            labels[genre.casefold()] = genre
    genres = [g for g, _ in weights.most_common(3)]
    enabled = [s.id for s in ctx.sources.values() if s.enabled and ctx.is_in_app(s.type)]
    query = select(Title).where(Title.source_id.in_(enabled), Title.kind.in_(("movie", "vod", "show", "series")))
    # Exclude every recorded title for this profile, not just the recent seed window.
    watched_rows = select(Title.id).join(Progress, title_ref_match()).where(Progress.profile_id == profile_id)
    child, parent = aliased(Title), aliased(Title)
    parents = (
        select(parent.id)
        .join(child, and_(parent.source_id == child.source_id, parent.ext_id == child.parent_ext_id))
        .where(child.id.in_(watched_rows))
    )
    grandparents = (
        select(parent.id)
        .join(child, and_(parent.source_id == child.source_id, parent.ext_id == child.parent_ext_id))
        .where(child.id.in_(parents))
    )
    family = watched_rows.union(parents, grandparents)
    # Materializable membership sets avoid a correlated OR/EXISTS scan for
    # every catalog row on large libraries.
    watched_movies = select(Title.tmdb_id).where(
        Title.id.in_(family), Title.kind.in_(("movie", "vod")), Title.tmdb_id.is_not(None)
    )
    watched_shows = select(Title.tmdb_id).where(
        Title.id.in_(family), Title.kind.in_(("show", "series")), Title.tmdb_id.is_not(None)
    )
    query = query.where(
        ~Title.id.in_(family),
        or_(
            Title.tmdb_id.is_(None),
            ~or_(
                and_(Title.kind.in_(("movie", "vod")), Title.tmdb_id.in_(watched_movies)),
                and_(Title.kind.in_(("show", "series")), Title.tmdb_id.in_(watched_shows)),
            ),
        ),
    )
    if genres:
        genre_rows = func.json_each(Title.genres).table_valued("value")
        query = query.where(select(genre_rows.c.value).where(func.lower(genre_rows.c.value).in_(genres)).exists())
    query = await catalog._filter_title_languages(query, await catalog.preferred_languages(profile_id, None))
    async with catalog._db.session() as session:
        rows = list(
            (
                await session.execute(query.order_by(Title.added_at.desc(), Title.id.desc()).limit(CANDIDATE_LIMIT))
            ).scalars()
        )
    rows = [r for r in rows if hub_id_for_title(r) not in watched_ancestors]
    if genres:
        rows.sort(
            key=lambda r: (
                -sum(weights[g.casefold()] for g in r.genres or [] if isinstance(g, str)),
                r.title.casefold(),
                r.id,
            )
        )
    tiles = ctx.merge(ctx.title_tile(r) for r in rows)[: min(max(limit, 1), 30)]
    title = (
        "Because you watched " + ", ".join(labels[g] for g in genres)
        if genres
        else "Explore your library · Not personalized"
    )
    return tiles, title
