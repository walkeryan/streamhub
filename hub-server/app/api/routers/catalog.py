"""/home, /search, /titles, /browse, /channels*, /epg, /favorites, /watchlist, /history."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Response

from app.api import schemas as S
from app.api.deps import Admin, C, P
from app.services.errors import ServiceError

router = APIRouter()


@router.get("/home", response_model=S.Home, tags=["catalog"])
async def home(c: C, p: P) -> dict[str, Any]:
    return await c.home.home(p.profile_id)


@router.get("/search", response_model=S.SearchResults, tags=["catalog"])
async def search(
    c: C,
    p: P,
    q: str = Query(..., min_length=1, max_length=200),
    kinds: str | None = None,
    limit: int = Query(40, ge=1, le=100),
    language: str | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    kind_set = {k.strip() for k in kinds.split(",") if k.strip()} if kinds else None
    return await c.catalog.search(q, p.profile_id, kind_set, limit, language=language, source_id=source_id)


@router.get("/titles/{id}", response_model=S.TitleDetail, tags=["catalog"])
async def title(id: str, c: C, p: P) -> dict[str, Any]:
    return await c.catalog.detail(id, p.profile_id)


@router.get("/browse/{section}", response_model=S.BrowsePage, tags=["catalog"])
async def browse(
    section: str,
    c: C,
    p: P,
    source_id: str | None = None,
    genre: str | None = None,
    category: str | None = None,
    language: str | None = None,
    q: str | None = Query(None, max_length=200),
    sort: str | None = Query(None, pattern="^(added|title|year|rating)$"),
    page: int = Query(1, ge=1),
) -> dict[str, Any]:
    return await c.catalog.browse(
        section,
        p.profile_id,
        source_id=source_id,
        genre=genre,
        category=category,
        sort=sort,
        page=page,
        language=language,
        q=q,
    )


@router.get("/browse/{section}/categories", tags=["catalog"])
async def browse_categories(
    section: str,
    c: C,
    p: P,
    source_id: str | None = None,
    language: str | None = None,
    q: str | None = Query(None, max_length=200),
) -> list[dict[str, Any]]:
    return await c.catalog.browse_categories(section, p.profile_id, source_id=source_id, language=language, q=q)


@router.get("/browse/{section}/languages", response_model=list[S.LanguageCount], tags=["catalog"])
async def browse_languages(section: str, c: C, p: P, source_id: str | None = None) -> list[dict[str, Any]]:
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
) -> list[dict[str, Any]]:
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
    return await c.channels.list_channels(
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
async def patch_group(id: str, body: S.GroupPatch, c: C, p: Admin) -> dict[str, Any]:
    return await c.channels.update_group(id, body.model_dump(exclude_unset=True))


@router.get("/channels/languages", response_model=list[S.LanguageCount], tags=["live"])
async def channel_languages(c: C, p: P) -> list[dict[str, Any]]:
    return await c.channels.languages()


@router.patch("/channels/{id}", response_model=S.Channel, tags=["live"])
async def patch_channel(id: str, body: S.ChannelPatch, c: C, p: Admin) -> dict[str, Any]:
    return await c.channels.update_channel(id, body.model_dump(exclude_unset=True), p.profile_id)


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
    return await c.channels.epg_grid(p.profile_id, cids, from_, to, country=country)


# ---------------------------------------------------------------- favorites / watchlist / history
@router.get("/favorites", response_model=list[S.Tile], tags=["user"])
async def favorites(c: C, p: P) -> list[dict[str, Any]]:
    ctx = await c.catalog.tile_context(p.profile_id)
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
async def watchlist(c: C, p: P) -> list[dict[str, Any]]:
    ctx = await c.catalog.tile_context(p.profile_id)
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
async def history(c: C, p: P) -> list[dict[str, Any]]:
    rows = await c.progress.history(p.profile_id, 60)
    ctx = await c.catalog.tile_context(p.profile_id)
    return await c.catalog.tiles_for_ids([r.ref_id for r in rows], ctx)


def _bad(msg: str) -> ServiceError:
    return ServiceError(msg)
