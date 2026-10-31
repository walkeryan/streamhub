"""CatalogService: titles, browse, search, tile resolution for arbitrary hub ids."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import Select, String, cast, func, or_, select

from app.domain.entities import CatalogItem, MediaKind
from app.domain.ports import DetailProvider, ImageAuthProvider, MetadataProvider
from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.models import Channel, Favorite, Progress, Source, Title, Watchlist
from app.services import ids
from app.services.availability import ATTRIBUTION, AvailabilityService
from app.services.context import absolute
from app.services.errors import NotFound
from app.services.sources import LAZY_GEN, SourceService, item_row, upsert_titles
from app.services.tiles import TileContext, hub_id_for_title, meta_tile, row_to_item

log = logging.getLogger(__name__)

PAGE_SIZE = 60
BROWSE_KINDS: dict[str, tuple[str, ...]] = {
    "movies": ("movie", "vod"),
    "shows": ("show", "series"),
    "vod": ("vod",),
    "series": ("series",),
}
SORTS = {"added", "title", "year", "rating"}


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class CatalogService:
    def __init__(
        self,
        db: Database,
        crypto: Crypto,
        sources: SourceService,
        availability: AvailabilityService,
        meta: MetadataProvider,
    ) -> None:
        self._db = db
        self._crypto = crypto
        self.sources = sources
        self.availability = availability
        self.meta = meta

    # ------------------------------------------------------------------ context
    def image_fn(self, srcs: dict[str, Source]) -> Callable[[Source | None, str | None], str | None]:
        def image(src: Source | None, url: str | None) -> str | None:
            if not url or src is None:
                return url
            try:
                prov = self.sources.provider(src)
            except Exception:
                return url
            if isinstance(prov, ImageAuthProvider) and prov.image_headers(url) is not None:
                token = self._crypto.sign_json({"s": src.id, "u": url})
                return absolute(f"/api/v1/images/{token}")
            return url

        return image

    async def tile_context(self, profile_id: str | None) -> TileContext:
        srcs = await self.sources.rows_by_id()
        favorites: set[str] = set()
        progress: dict[str, Progress] = {}
        if profile_id:
            async with self._db.session() as s:
                favorites = set(
                    (await s.execute(select(Favorite.ref_id).where(Favorite.profile_id == profile_id))).scalars()
                )
                for p in (await s.execute(select(Progress).where(Progress.profile_id == profile_id))).scalars():
                    progress[p.ref_id] = p
        return TileContext(
            sources=srcs,
            image=self.image_fn(srcs),
            is_in_app=self.sources.registry.is_in_app,
            favorites=favorites,
            progress=progress,
        )

    def _enabled(self, ctx: TileContext) -> list[str]:
        return [sid for sid, s in ctx.sources.items() if s.enabled]

    # ------------------------------------------------------------------ resolution
    async def title_row(self, source_id: str, ext_id: str) -> Title | None:
        async with self._db.session() as s:
            return (
                await s.execute(select(Title).where(Title.source_id == source_id, Title.ext_id == ext_id))
            ).scalar_one_or_none()

    async def rows_for_tmdb(self, media_type: str, tmdb_id: int) -> list[Title]:
        kinds = ids.MOVIE_KINDS if media_type == "movie" else ids.SHOW_KINDS
        async with self._db.session() as s:
            return list(
                (
                    await s.execute(select(Title).where(Title.tmdb_id == tmdb_id, Title.kind.in_(kinds)))
                ).scalars()
            )

    async def tiles_for_ids(self, ref_ids: list[str], ctx: TileContext) -> list[dict[str, Any]]:
        """Resolve hub ids (tmdb:/lib:/ch:) to tiles, preserving order and skipping unknown ids."""
        from app.services.channels import programme_dict  # local import avoids cycle

        out: dict[str, dict[str, Any]] = {}
        lib_keys: list[tuple[str, str]] = []
        ch_keys: list[tuple[str, str]] = []
        tmdb_keys: list[tuple[str, int]] = []
        for rid in ref_ids:
            try:
                h = ids.parse(rid)
            except ValueError:
                continue
            if h.scheme == "lib":
                lib_keys.append((h.a, h.b))
            elif h.scheme == "ch":
                ch_keys.append((h.a, h.b))
            elif h.scheme == "tmdb" and h.b.isdigit():
                tmdb_keys.append((h.a, int(h.b)))
        async with self._db.session() as s:
            for sid, ext in lib_keys:
                row = (
                    await s.execute(select(Title).where(Title.source_id == sid, Title.ext_id == ext))
                ).scalar_one_or_none()
                if row and row.source_id in ctx.sources:
                    t = ctx.title_tile(row)
                    t["id"] = ids.lib_id(sid, ext)  # keep the id the client stored
                    out[t["id"]] = t
            now_map: dict[str, Any] = {}
            chans: list[Channel] = []
            for sid, ext in ch_keys:
                ch = (
                    await s.execute(select(Channel).where(Channel.source_id == sid, Channel.ext_id == ext))
                ).scalar_one_or_none()
                if ch:
                    chans.append(ch)
        if chans:
            from app.services.channels import now_next

            nn = await now_next(self._db, chans)
            for ch in chans:
                now = nn.get(ch.epg_id or "", (None, None))[0]
                now_map[ch.ext_id] = programme_dict(now, ch) if now else None
                t = ctx.channel_tile(ch, now_map[ch.ext_id])
                out[t["id"]] = t
        for media_type, tid in tmdb_keys:
            hub = ids.tmdb_id(media_type, tid)
            rows = await self.rows_for_tmdb(media_type, tid)
            rows = [r for r in rows if r.source_id in ctx.sources]
            if rows:
                merged = ctx.merge(ctx.title_tile(r) for r in rows)
                if merged:
                    out[hub] = merged[0]
                    continue
            meta = await self.meta.details(media_type, tid) if self.meta.enabled else None
            if meta:
                badges, _ = await self.availability.badges_for(
                    media_type, tmdb_id=tid, imdb_id=meta.imdb_id, title=meta.title, year=meta.year,
                    sources=ctx.sources, is_in_app=ctx.is_in_app,
                )
                out[hub] = meta_tile(meta, badges, ctx.favorites, ctx.progress_pct(hub))
        return [out[r] for r in ref_ids if r in out]

    # ------------------------------------------------------------------ detail
    async def _lazy_store(self, source_id: str, items: list[CatalogItem]) -> None:
        await upsert_titles(self._db, [item_row(source_id, it, LAZY_GEN) for it in items])

    async def children_rows(self, row: Title) -> list[Title]:
        """Seasons/episodes for a show/series/season, fetched lazily from the adapter and stored."""
        src = await self.sources.get(row.source_id)
        prov = self.sources.provider(src)
        if isinstance(prov, DetailProvider):
            try:
                kids = await prov.children(row_to_item(row))
                await self._lazy_store(row.source_id, kids)
            except Exception as exc:
                log.warning("children fetch failed", extra={"source": row.source_id, "err": type(exc).__name__})
        async with self._db.session() as s:
            if row.kind in ("show", "series"):
                seasons = list(
                    (
                        await s.execute(
                            select(Title)
                            .where(Title.source_id == row.source_id, Title.parent_ext_id == row.ext_id,
                                   Title.kind == "season")
                            .order_by(Title.season_number)
                        )
                    ).scalars()
                )
                season_ids = [x.ext_id for x in seasons]
                eps = list(
                    (
                        await s.execute(
                            select(Title)
                            .where(Title.source_id == row.source_id, Title.parent_ext_id.in_(season_ids),
                                   Title.kind == "episode")
                            .order_by(Title.season_number, Title.episode_number)
                        )
                    ).scalars()
                ) if season_ids else []
                return seasons + eps
            return list(
                (
                    await s.execute(
                        select(Title)
                        .where(Title.source_id == row.source_id, Title.parent_ext_id == row.ext_id)
                        .order_by(Title.episode_number)
                    )
                ).scalars()
            )

    async def refresh_detail(self, row: Title) -> Title:
        src = await self.sources.get(row.source_id)
        prov = self.sources.provider(src)
        if isinstance(prov, DetailProvider) and row.kind in ("vod", "movie", "episode", "show", "series"):
            try:
                item = await prov.item_detail(row_to_item(row))
                data = item_row(row.source_id, item, LAZY_GEN)
                async with self._db.session() as s:
                    db_row = await s.get(Title, row.id)
                    if db_row is not None:
                        for k in ("overview", "backdrop", "tmdb_id", "imdb_id", "duration_ms", "year", "genres"):
                            v = data.get(k)
                            if v:
                                setattr(db_row, k, v)
                        extra = dict(db_row.extra or {})
                        extra.update({k: v for k, v in (data.get("extra") or {}).items() if v is not None})
                        db_row.extra = extra
                        await s.commit()
                        await s.refresh(db_row)
                        return db_row
            except Exception as exc:
                log.info("detail fetch failed", extra={"source": row.source_id, "err": type(exc).__name__})
        return row

    async def detail(self, hub_id: str, profile_id: str) -> dict[str, Any]:
        try:
            h = ids.parse(hub_id)
        except ValueError as exc:
            raise NotFound("unknown id") from exc
        ctx = await self.tile_context(profile_id)
        if h.scheme == "ch":
            tiles = await self.tiles_for_ids([hub_id], ctx)
            if not tiles:
                raise NotFound("channel not found")
            return {**tiles[0], "overview": "", "genres": [], "runtime_min": None, "cast": [],
                    "seasons": [], "episodes": [], "attribution": ATTRIBUTION}
        if h.scheme == "lib":
            row = await self.title_row(h.a, h.b)
            if row is None or row.source_id not in ctx.sources:
                raise NotFound("title not found")
            row = await self.refresh_detail(row)
            if row.tmdb_id and row.kind in ("movie", "vod", "show", "series"):
                # prefer the merged TMDB view but keep this source first
                merged = await self._tmdb_detail(
                    ids.tmdb_type_for_kind(row.kind) or "movie", row.tmdb_id, ctx, preferred=row
                )
                if merged:
                    return merged
            return await self._lib_detail(row, ctx)
        if h.scheme == "tmdb" and h.b.isdigit():
            d = await self._tmdb_detail(h.a, int(h.b), ctx)
            if d:
                return d
        raise NotFound("title not found")

    async def _lib_detail(self, row: Title, ctx: TileContext, base: dict[str, Any] | None = None) -> dict[str, Any]:
        tile = base or ctx.title_tile(row)
        tile.update(
            {
                "id": tile.get("id") or ids.lib_id(row.source_id, row.ext_id),
                "overview": row.overview or "",
                "genres": list(row.genres or []),
                "runtime_min": (row.duration_ms // 60000) if row.duration_ms else None,
                "cast": [],
                "seasons": [],
                "episodes": [],
                "attribution": ATTRIBUTION,
            }
        )
        if row.kind in ("show", "series", "season"):
            kids = await self.children_rows(row)
            for k in kids:
                t = ctx.title_tile(k)
                t["id"] = ids.lib_id(k.source_id, k.ext_id)
                t["kind"] = k.kind
                (tile["seasons"] if k.kind == "season" else tile["episodes"]).append(t)
        return tile

    async def _tmdb_detail(
        self, media_type: str, tmdb_id: int, ctx: TileContext, preferred: Title | None = None
    ) -> dict[str, Any] | None:
        hub = ids.tmdb_id(media_type, tmdb_id)
        meta = await self.meta.details(media_type, tmdb_id) if self.meta.enabled else None
        badges, matches = await self.availability.badges_for(
            media_type,
            tmdb_id=tmdb_id,
            imdb_id=meta.imdb_id if meta else None,
            title=meta.title if meta else None,
            year=meta.year if meta else None,
            sources=ctx.sources,
            is_in_app=ctx.is_in_app,
        )
        matches = [m for m in matches if m.source_id in ctx.sources]
        if preferred is not None:
            matches = [preferred] + [m for m in matches if m.id != preferred.id]
            # make sure the preferred source badge leads among in-app badges
            badges.sort(key=lambda b: (not b["playable_in_app"], b["source_id"] != preferred.source_id))
        if meta is None and not matches:
            return None
        if meta is not None:
            tile = meta_tile(meta, badges, ctx.favorites, ctx.progress_pct(hub))
            detail = {
                **tile,
                "overview": meta.overview or "",
                "genres": meta.genres,
                "runtime_min": meta.runtime_min,
                "cast": meta.cast,
                "seasons": [],
                "episodes": [],
                "attribution": ATTRIBUTION,
            }
        else:
            primary = matches[0]
            tile = ctx.title_tile(primary)
            tile["sources"] = badges
            detail = await self._lib_detail(primary, ctx, base=tile)
            detail["id"] = hub
            return detail
        # seasons/episodes from the best library match (playable), else none
        show_src = next((m for m in matches if m.kind in ("show", "series")), None)
        if media_type == "tv" and show_src is not None:
            lib = await self._lib_detail(show_src, ctx, base=ctx.title_tile(show_src))
            detail["seasons"] = lib["seasons"]
            detail["episodes"] = lib["episodes"]
        if not detail.get("poster") and matches:
            detail["poster"] = ctx.title_tile(matches[0])["poster"]
        return detail

    # ------------------------------------------------------------------ browse
    def _browse_query(self, kinds: tuple[str, ...], source_ids: list[str], source_id: str | None,
                      genre: str | None) -> Select[tuple[Title]]:
        q = select(Title).where(Title.kind.in_(kinds), Title.source_id.in_(source_ids))
        if source_id:
            q = q.where(Title.source_id == source_id)
        if genre:
            q = q.where(
                or_(
                    func.lower(cast(Title.genres, String)).like(
                        f'%"{_escape_like(genre.lower())}"%', escape="\\"
                    ),
                    Title.group_name == genre,
                )
            )
        return q

    async def browse(
        self, section: str, profile_id: str, *, source_id: str | None, genre: str | None, sort: str | None,
        page: int,
    ) -> dict[str, Any]:
        kinds = BROWSE_KINDS.get(section)
        if kinds is None:
            raise NotFound("unknown section")
        ctx = await self.tile_context(profile_id)
        enabled = self._enabled(ctx)
        page = max(1, page)
        q = self._browse_query(kinds, enabled, source_id, genre)
        order = {
            "title": [Title.norm_title.asc()],
            "year": [Title.year.desc().nulls_last(), Title.norm_title],
            "rating": [Title.rating.desc().nulls_last(), Title.norm_title],
        }.get(sort or "added", [Title.added_at.desc().nulls_last(), Title.id.desc()])
        async with self._db.session() as s:
            total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = list(
                (await s.execute(q.order_by(*order).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))).scalars()
            )
        tiles = ctx.merge(ctx.title_tile(r) for r in rows)
        return {"tiles": tiles, "page": page, "total": int(total)}

    async def genres(self, section: str) -> list[str]:
        kinds = BROWSE_KINDS.get(section, ())
        async with self._db.session() as s:
            rows = (await s.execute(select(Title.genres).where(Title.kind.in_(kinds)).limit(5000))).scalars()
        out: set[str] = set()
        for g in rows:
            out.update(x for x in g or [] if isinstance(x, str))
        return sorted(out)

    # ------------------------------------------------------------------ search
    async def search(self, q: str, profile_id: str, kinds: set[str] | None, limit: int) -> dict[str, Any]:
        from app.services.channels import ChannelService

        ctx = await self.tile_context(profile_id)
        enabled = self._enabled(ctx)
        q = q.strip()
        limit = max(1, min(limit, 100))
        norm = ids.normalise_title(q)
        want = kinds or {"movie", "show", "vod", "series", "episode", "channel"}
        lib_kinds = [k for k in ("movie", "show", "vod", "series", "episode") if k in want]
        rows: list[Title] = []
        if norm and lib_kinds:
            like = f"%{_escape_like(norm)}%"
            async with self._db.session() as s:
                rows = list(
                    (
                        await s.execute(
                            select(Title)
                            .where(Title.source_id.in_(enabled), Title.kind.in_(lib_kinds),
                                   Title.norm_title.like(like, escape="\\"))
                            .order_by((Title.norm_title == norm).desc(), Title.norm_title.like(f"{_escape_like(norm)}%", escape="\\").desc(),
                                      Title.added_at.desc().nulls_last())
                            .limit(limit * 3)
                        )
                    ).scalars()
                )
        play_here = ctx.merge(ctx.title_tile(r) for r in rows)
        seen = {t["id"] for t in play_here}
        open_in_app: list[dict[str, Any]] = []
        if self.meta.enabled and ({"movie", "show"} & want):
            metas = await self.meta.search(q, limit=20)
            results = await asyncio.gather(
                *[
                    self.availability.badges_for(
                        m.media_type, tmdb_id=m.tmdb_id, imdb_id=m.imdb_id, title=m.title, year=m.year,
                        sources=ctx.sources, is_in_app=ctx.is_in_app,
                    )
                    for m in metas
                ]
            )
            for m, (badges, _) in zip(metas, results, strict=True):
                hub = ids.tmdb_id(m.media_type, m.tmdb_id)
                tile = meta_tile(m, badges, ctx.favorites, ctx.progress_pct(hub))
                if hub in seen:
                    continue
                if any(b["playable_in_app"] for b in badges):
                    play_here.append(tile)
                    seen.add(hub)
                elif badges:
                    open_in_app.append(tile)
        channels: list[dict[str, Any]] = []
        if "channel" in want:
            chsvc = ChannelService(self._db, self.sources)
            res = await chsvc.list_channels(profile_id, q=q, language=None, all_languages=True, limit=limit)
            channels = res["channels"]
        return {
            "play_here": play_here[:limit],
            "open_in_app": open_in_app[:limit],
            "channels": channels[:limit],
            "attribution": ATTRIBUTION,
        }

    # ------------------------------------------------------------------ lists used by home
    async def recently_added(self, ctx: TileContext, limit: int = 30, source_id: str | None = None,
                             kinds: tuple[str, ...] = ("movie", "show", "vod", "series")) -> list[dict[str, Any]]:
        enabled = self._enabled(ctx)
        q = select(Title).where(Title.kind.in_(kinds), Title.source_id.in_(enabled), Title.added_at.is_not(None))
        if source_id:
            q = q.where(Title.source_id == source_id)
        async with self._db.session() as s:
            rows = list((await s.execute(q.order_by(Title.added_at.desc()).limit(limit * 2))).scalars())
        return ctx.merge(ctx.title_tile(r) for r in rows)[:limit]

    async def watchlist_ids(self, profile_id: str) -> list[str]:
        async with self._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(Watchlist.ref_id)
                        .where(Watchlist.profile_id == profile_id)
                        .order_by(Watchlist.added_at.desc())
                    )
                ).scalars()
            )

    async def favorite_ids(self, profile_id: str, kinds: set[str] | None = None) -> list[str]:
        q = select(Favorite).where(Favorite.profile_id == profile_id).order_by(Favorite.sort, Favorite.created_at)
        async with self._db.session() as s:
            rows = list((await s.execute(q)).scalars())
        return [f.ref_id for f in rows if kinds is None or f.kind in kinds]

    def item_hub_id(self, row: Title) -> str:
        return hub_id_for_title(row)

    @staticmethod
    def kind_of(hub_id: str) -> str | None:
        try:
            h = ids.parse(hub_id)
        except ValueError:
            return None
        if h.scheme == "ch":
            return MediaKind.CHANNEL.value
        if h.scheme == "tmdb":
            return "movie" if h.a == "movie" else "show"
        return None
