"""/home, /search, /titles, /browse, /channels*, /epg, /favorites, /watchlist, /history."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from app.api import schemas as S
from app.api.deps import C, P, SourcesAdmin
from app.api.policy_route import PolicyCatalogRoute
from app.services.adult import adult_scope, require_adult
from app.services.auth import Principal
from app.services.errors import Forbidden, ServiceError
from app.services.listing_cache import listing_cached, normalized_query
from app.services.search import MIN_QUERY_LENGTH, TITLE_CAP, empty_search
from app.services.sports import sports_channels, sports_events, sports_on_now
from app.services.sports_recordings import sports_recordings
from app.services.visibility import require_watch

router = APIRouter(route_class=PolicyCatalogRoute)

IncludeAdult = Annotated[
    bool,
    Query(
        description="Also list titles marked adult. Needs watch.adult and a session unlocked at "
        "/auth/adult-unlock; ignored otherwise."
    ),
]


def listing_scope(p: Principal, include_adult: bool) -> AbstractContextManager[None]:
    # Asking is not enough: the permission, and this session's PIN unlock, both have to hold.
    return adult_scope(include_adult and p.can("watch.adult") and p.adult_unlocked)


@router.get("/home", response_model=S.Home, tags=["catalog"])
async def home(c: C, p: P, include_adult: IncludeAdult = False) -> dict[str, Any]:
    with listing_scope(p, include_adult):
        return await c.requests.annotate(p, await c.home.home(p.profile_id))


@router.get("/sports", response_model=S.SportsPage, tags=["live"])
async def sports(
    c: C,
    p: P,
    language: str | None = None,
    sport: str | None = None,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(40, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await sports_on_now(
        c.channels, p.profile_id, language=language, sport=sport, q=q, limit=limit, offset=offset
    )


@router.get("/sports/recordings", response_model=S.SportsRecordingsPage, tags=["catalog"])
async def sports_recording_list(
    c: C,
    p: P,
    response: Response,
    language: str | None = None,
    sport: str | None = None,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(40, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    # New library surface must not bypass centrally enforced profile policy.
    # Fail closed during partial integrations where the service is unavailable.
    policy = getattr(c, "content_policy", None)
    if policy is None:
        raise Forbidden("Content policy service unavailable.", code="content_policy_unavailable")
    response.headers["Cache-Control"] = "private, no-store"
    return await sports_recordings(
        c.catalog,
        p.profile_id,
        allowed_refs=policy.allowed_refs,
        language=language,
        sport=sport,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/sports/events", response_model=S.SportsEventsPage, tags=["live"])
async def sports_event_list(
    c: C,
    p: P,
    date: str | None = Query(None, max_length=10),
    timezone: str = Query("America/New_York", max_length=64),
    days: int = Query(1, ge=1, le=7),
    language: str | None = None,
    sport: str | None = Query(None, max_length=80),
    status: str = Query("all", pattern="^(all|active|current|upcoming|replay)$"),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(40, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await sports_events(
        c.channels,
        p.profile_id,
        date=date,
        timezone=timezone,
        days=days,
        language=language,
        sport=sport,
        status=status,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/sports/channels", response_model=S.SportsChannelsPage, tags=["live"])
async def sports_channel_list(
    c: C,
    p: P,
    language: str | None = None,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(40, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await c.recorder.annotate(
        p, await sports_channels(c.channels, p.profile_id, language=language, q=q, limit=limit, offset=offset)
    )


@router.get("/search", response_model=S.SearchResults, tags=["catalog"])
async def search(
    c: C,
    p: P,
    response: Response,
    q: str = Query("", max_length=200),
    kinds: str | None = None,
    limit: int = Query(TITLE_CAP, ge=1, le=100),
    language: str | None = None,
    source_id: str | None = None,
    include_adult: IncludeAdult = False,
    expand: str | None = Query(None, pattern="^(titles|channels|programmes)(,(titles|channels|programmes))*$"),
    discover: bool = Query(
        True,
        description="false: only what the hub holds (its titles, channels, broadcasts); no remote provider "
        "is asked, so the answer is quick. A client shows that first and the full answer when it arrives.",
    ),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    kind_set = {k.strip() for k in kinds.split(",") if k.strip()} if kinds else None
    q = normalized_query(q) or ""
    if len(q) < MIN_QUERY_LENGTH:
        # Nothing is looked up for a letter: the box is still being typed in.
        return empty_search()
    expanded = frozenset(expand.split(",")) if expand else frozenset()
    with listing_scope(p, include_adult):
        result = await listing_cached(
            c.db,
            p.profile_id,
            "search",
            (q, tuple(sorted(kind_set or [])), limit, language, source_id, tuple(sorted(expanded)), discover),
            lambda: c.catalog.search(
                q,
                p.profile_id,
                kind_set,
                limit,
                language=language,
                source_id=source_id,
                expand=expanded,
                discover=discover,
            ),
        )
    return await c.recorder.annotate(p, await c.requests.annotate(p, result))


async def _title_rows(c: C, id: str) -> list[Any]:
    """The catalog rows behind a title id, for merging the library account's watch state on open."""
    parts = id.split(":", 2)
    if len(parts) != 3:
        return []
    if parts[0] == "lib":
        row = await c.catalog.title_row(parts[1], parts[2])
        return [row] if row is not None else []
    if parts[0] == "tmdb" and parts[1] in ("movie", "tv") and parts[2].isdigit():
        return await c.catalog.rows_for_tmdb(parts[1], int(parts[2]))
    return []


@router.get("/titles/{id}", response_model=S.TitleDetail, tags=["catalog"])
async def title(id: str, c: C, p: P) -> dict[str, Any]:
    await require_watch(c.db, p.permissions, id)
    # Opening a title is when the user linked to the library server's account picks up what
    # they watched there (see ``ProgressService.merge_account_state``); nobody else asks it.
    await c.progress.merge_account_state(p.profile_id, await _title_rows(c, id))
    # The rating ceiling answers first, in its own words; then the adult lock, before anything is returned.
    detail = await c.catalog.detail(id, p.profile_id)
    await require_adult(c.db, p, id)
    return await c.requests.annotate_detail(p, detail)


@router.get("/discover/{section}", response_model=S.DiscoveryPage, tags=["catalog"])
async def discover(
    section: str,
    c: C,
    p: P,
    response: Response,
    page: int = Query(1, ge=1, le=500),
    sort: str = Query("popular", pattern="^(popular|rating|newest)$"),
    year: int | None = Query(None, ge=1888, le=2100),
    language: str | None = None,
    source_id: str | None = None,
    include_adult: IncludeAdult = False,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    with listing_scope(p, include_adult):
        result = await listing_cached(
            c.db,
            p.profile_id,
            "discover",
            (section, page, sort, year, language, source_id),
            lambda: c.catalog.discover(
                section, p.profile_id, page=page, sort=sort, year=year, language=language, source_id=source_id
            ),
            ttl=30,
        )
    return await c.requests.annotate(p, result)


@router.get("/browse/{section}", response_model=S.BrowsePage, tags=["catalog"])
async def browse(
    section: str,
    c: C,
    p: P,
    response: Response,
    source_id: str | None = None,
    genre: str | None = None,
    category: str | None = None,
    language: str | None = None,
    q: str | None = Query(None, max_length=200),
    sort: str | None = Query(None, pattern="^(added|title|year|rating)$"),
    page: int = Query(1, ge=1),
    include_adult: IncludeAdult = False,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    q = normalized_query(q)
    with listing_scope(p, include_adult):
        return await listing_cached(
            c.db,
            p.profile_id,
            "browse",
            (section, source_id, genre, category, language, q, sort, page),
            lambda: c.catalog.browse(
                section,
                p.profile_id,
                source_id=source_id,
                genre=genre,
                category=category,
                sort=sort,
                page=page,
                language=language,
                q=q,
            ),
            ttl=60,
        )


@router.get("/browse/{section}/categories", tags=["catalog"])
async def browse_categories(
    section: str,
    c: C,
    p: P,
    source_id: str | None = None,
    language: str | None = None,
    q: str | None = Query(None, max_length=200),
    include_adult: IncludeAdult = False,
) -> list[dict[str, Any]]:
    with listing_scope(p, include_adult):
        return await c.catalog.browse_categories(section, p.profile_id, source_id=source_id, language=language, q=q)


@router.get("/browse/{section}/shelves", response_model=S.ShelfList, tags=["catalog"])
async def browse_shelves(
    section: str,
    c: C,
    p: P,
    response: Response,
    source_id: str | None = None,
    language: str | None = None,
    limit_rows: int = Query(12, ge=1, le=40),
    limit_items: int = Query(20, ge=1, le=40),
    include_adult: IncludeAdult = False,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    with listing_scope(p, include_adult):
        return await listing_cached(
            c.db,
            p.profile_id,
            "shelves",
            (section, source_id, language, limit_rows, limit_items),
            lambda: c.catalog.shelves(
                section,
                p.profile_id,
                source_id=source_id,
                language=language,
                limit_rows=limit_rows,
                limit_items=limit_items,
            ),
            ttl=60,
        )


@router.get("/browse/{section}/languages", response_model=list[S.LanguageCount], tags=["catalog"])
async def browse_languages(
    section: str, c: C, p: P, source_id: str | None = None, include_adult: IncludeAdult = False
) -> list[dict[str, Any]]:
    with listing_scope(p, include_adult):
        return await c.catalog.browse_languages(section, p.profile_id, source_id)


@router.get("/browse/{section}/genres", tags=["catalog"])
async def browse_genres(
    section: str,
    c: C,
    p: P,
    source_id: str | None = None,
    language: str | None = None,
    category: str | None = None,
    q: str | None = Query(None, max_length=200),
    include_adult: IncludeAdult = False,
) -> list[dict[str, Any]]:
    with listing_scope(p, include_adult):
        return await c.catalog.genre_facets(
            section, p.profile_id, source_id=source_id, language=language, category=category, q=q
        )


# ---------------------------------------------------------------- channels
@router.get("/channels", response_model=S.ChannelList, tags=["live"])
async def channels(
    c: C,
    p: P,
    source_id: str | None = None,
    group: str | None = None,
    language: str | None = None,
    country: str | None = None,
    favorites: bool = False,
    q: str | None = None,
    list_id: str | None = None,
    limit: int | None = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    has_epg: bool = False,
) -> dict[str, Any]:
    listed = await c.channels.list_channels(
        p.profile_id,
        source_id=source_id,
        group=group,
        language=language,
        country=country,
        favorites=favorites,
        q=q,
        list_id=list_id,
        limit=limit,
        offset=offset,
        has_epg=has_epg,
    )
    return await c.recorder.annotate(p, listed)


@router.get("/channels/groups", response_model=list[S.ChannelGroup], tags=["live"])
async def channel_groups(
    c: C,
    p: P,
    language: str | None = None,
    country: str | None = None,
    source_id: str | None = None,
    q: str | None = None,
    favorites: bool = False,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    return await c.channels.groups(
        p.profile_id,
        language=language,
        country=country,
        source_id=source_id,
        q=q,
        favorites=favorites,
        include_hidden=include_hidden,
    )


@router.patch("/channels/groups/{id}", response_model=S.ChannelGroup, tags=["live"])
async def patch_group(id: str, body: S.GroupPatch, c: C, p: SourcesAdmin) -> dict[str, Any]:
    return await c.channels.update_group(id, body.model_dump(exclude_unset=True))


@router.get("/channels/languages", response_model=list[S.LanguageCount], tags=["live"])
async def channel_languages(c: C, p: P) -> list[dict[str, Any]]:
    return await c.channels.languages()


@router.patch("/channels/{id}", response_model=S.Channel, tags=["live"])
async def patch_channel(id: str, body: S.ChannelPatch, c: C, p: SourcesAdmin) -> dict[str, Any]:
    updated = await c.channels.update_channel(id, body.model_dump(exclude_unset=True), p.profile_id)
    return await c.recorder.annotate(p, updated)


@router.get("/epg", response_model=dict[str, list[S.Programme]], tags=["live"])
async def epg(
    c: C,
    p: P,
    channel_ids: str | None = None,
    country: str | None = None,
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
) -> dict[str, Any]:
    cids = [x.strip() for x in channel_ids.split(",") if x.strip()] if channel_ids else None
    grid = await c.channels.epg_grid(p.profile_id, cids, from_, to, country=country)
    return await c.recorder.annotate(p, grid)


# ---------------------------------------------------------------- favorites / watchlist / history
@router.get("/favorites", response_model=list[S.Tile], tags=["user"])
async def favorites(c: C, p: P, include_adult: IncludeAdult = False) -> list[dict[str, Any]]:
    ctx = await c.catalog.tile_context(p.profile_id)
    with listing_scope(p, include_adult):
        return await c.catalog.tiles_for_ids(await c.favorites.ids(p.profile_id), ctx)


@router.put("/favorites", status_code=204, tags=["user"])
async def put_favorite(body: S.Favorite, c: C, p: P) -> Response:
    await c.favorites.put(p.profile_id, body.kind, body.id, body.sort)
    return Response(status_code=204)


@router.delete("/favorites", status_code=204, tags=["user"])
async def delete_favorite(c: C, p: P, kind: str, id: str) -> Response:
    await c.favorites.delete(p.profile_id, kind, id)
    return Response(status_code=204)


@router.post("/favorites/reorder", status_code=204, tags=["user"])
async def reorder_favorites(body: list[S.Favorite], c: C, p: P) -> Response:
    await c.favorites.reorder(p.profile_id, [f.model_dump() for f in body])
    return Response(status_code=204)


@router.get("/watchlist", response_model=list[S.Tile], tags=["user"])
async def watchlist(c: C, p: P, include_adult: IncludeAdult = False) -> list[dict[str, Any]]:
    ctx = await c.catalog.tile_context(p.profile_id)
    with listing_scope(p, include_adult):
        return await c.catalog.tiles_for_ids(await c.watchlist.ids(p.profile_id), ctx)


@router.put("/watchlist", status_code=204, tags=["user"])
async def put_watchlist(body: S.WatchlistPut, c: C, p: P) -> Response:
    await c.watchlist.put(p.profile_id, body.id)
    return Response(status_code=204)


@router.delete("/watchlist", status_code=204, tags=["user"])
async def delete_watchlist(c: C, p: P, id: str) -> Response:
    await c.watchlist.delete(p.profile_id, id)
    return Response(status_code=204)


@router.get("/history", response_model=list[S.Tile], tags=["user"])
async def history(c: C, p: P, include_adult: IncludeAdult = False) -> list[dict[str, Any]]:
    rows = await c.progress.history(p.profile_id, 60)
    ctx = await c.catalog.tile_context(p.profile_id)
    with listing_scope(p, include_adult):
        return await c.catalog.tiles_for_ids([r.ref_id for r in rows], ctx)


def _bad(msg: str) -> ServiceError:
    return ServiceError(msg)
