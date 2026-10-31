"""HomeService: hero + rows for a profile, honouring the per-profile home layout."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.domain.entities import SourceType
from app.domain.ports import ContinueWatchingProvider, MetadataProvider
from app.services import ids
from app.services.availability import AvailabilityService
from app.services.catalog import CatalogService
from app.services.channels import ChannelService, programme_dict
from app.services.sources import SourceService
from app.services.tiles import TileContext, meta_tile
from app.services.user_state import HOME_ROWS, HomeLayoutService, ProgressService

log = logging.getLogger(__name__)

ROW_TITLES = dict(HOME_ROWS)
ROW_LAYOUT = {"on_now": "channel", "continue_watching": "landscape"}
HERO_SIZE = 8


class HomeService:
    def __init__(
        self,
        catalog: CatalogService,
        channels: ChannelService,
        progress: ProgressService,
        layout: HomeLayoutService,
        sources: SourceService,
        availability: AvailabilityService,
        meta: MetadataProvider,
    ) -> None:
        self._catalog = catalog
        self._channels = channels
        self._progress = progress
        self._layout = layout
        self._sources = sources
        self._availability = availability
        self._meta = meta

    async def continue_watching(self, profile_id: str, ctx: TileContext) -> list[dict[str, Any]]:
        local = await self._progress.in_progress(profile_id, 30)
        tiles = await self._catalog.tiles_for_ids([p.ref_id for p in local], ctx)
        seen = {t["id"] for t in tiles}
        # merge library-side continue watching (Plex/Jellyfin) – progress made in their own apps
        for src in ctx.sources.values():
            if not src.enabled or src.type not in (SourceType.PLEX.value, SourceType.JELLYFIN.value):
                continue
            try:
                prov = self._sources.provider(src)
                if not isinstance(prov, ContinueWatchingProvider):
                    continue
                items = await asyncio.wait_for(prov.continue_watching(20), timeout=6)
            except Exception as exc:
                log.info("continue watching failed", extra={"source": src.id, "err": type(exc).__name__})
                continue
            for it, pos in items:
                hub = ids.lib_id(src.id, it.ext_id)
                if hub in seen:
                    continue
                row = await self._catalog.title_row(src.id, it.ext_id)
                if row is None:
                    from app.services.sources import LAZY_GEN, item_row, upsert_titles

                    await upsert_titles(self._sources._db, [item_row(src.id, it, LAZY_GEN)])
                    row = await self._catalog.title_row(src.id, it.ext_id)
                if row is None:
                    continue
                t = ctx.title_tile(row)
                t["id"] = hub
                t["kind"] = row.kind
                if it.duration_ms and pos:
                    t["progress_pct"] = round(100.0 * pos / it.duration_ms, 1)
                tiles.append(t)
                seen.add(hub)
        return tiles[:30]

    async def on_now(self, profile_id: str, ctx: TileContext) -> list[dict[str, Any]]:
        pairs = await self._channels.on_now(profile_id, 20)
        out = []
        for ch, prog in pairs:
            out.append(ctx.channel_tile(ch, programme_dict(prog, ch) if prog else None))
        return out

    async def trending(self, ctx: TileContext, limit: int = 20) -> list[dict[str, Any]]:
        if not self._meta.enabled:
            return []
        metas = await self._meta.trending(limit)
        out = []
        for m in metas:
            badges, _ = await self._availability.badges_for(
                m.media_type,
                tmdb_id=m.tmdb_id,
                imdb_id=None,
                title=m.title,
                year=m.year,
                sources=ctx.sources,
                is_in_app=ctx.is_in_app,
            )
            hub = ids.tmdb_id(m.media_type, m.tmdb_id)
            out.append(meta_tile(m, badges, ctx.favorites, ctx.progress_pct(hub)))
        return out

    async def home(self, profile_id: str) -> dict[str, Any]:
        ctx = await self._catalog.tile_context(profile_id)
        source_rows = [
            (f"source:{s.id}", s)
            for s in sorted(ctx.sources.values(), key=lambda x: x.priority)
            if s.enabled
            and s.type != SourceType.STREAMING_SERVICE.value
            and (s.counts or {}).get("channels", 0) != sum((s.counts or {}).values())
        ]
        layout = await self._layout.get(profile_id, [rid for rid, _ in source_rows])
        visible = [r["id"] for r in layout if not r["hidden"]]

        built: dict[str, dict[str, Any]] = {}
        cw = await self.continue_watching(profile_id, ctx)
        recent = await self._catalog.recently_added(ctx, 30)
        trending: list[dict[str, Any]] = []
        has_library = any(s.enabled and s.type != SourceType.STREAMING_SERVICE.value for s in ctx.sources.values())
        if "trending" in visible or not (cw or recent):
            trending = await self.trending(ctx)

        for rid in visible:
            tiles: list[dict[str, Any]] = []
            title = ROW_TITLES.get(rid, rid)
            if rid == "continue_watching":
                tiles = cw
            elif rid == "on_now":
                tiles = await self.on_now(profile_id, ctx)
            elif rid == "recently_added":
                tiles = recent
            elif rid == "watchlist":
                tiles = await self._catalog.tiles_for_ids(await self._catalog.watchlist_ids(profile_id), ctx)
            elif rid == "favorites":
                fav = await self._catalog.favorite_ids(
                    profile_id, kinds={"movie", "show", "vod", "series", "episode", "season"}
                )
                tiles = await self._catalog.tiles_for_ids(fav, ctx)
            elif rid == "trending":
                tiles = trending if not has_library or not (cw or recent) else trending[:20]
            elif rid.startswith("source:"):
                sid = rid.split(":", 1)[1]
                src = ctx.sources.get(sid)
                if src is None:
                    continue
                title = f"{src.name} · Recently added"
                tiles = await self._catalog.recently_added(ctx, 30, source_id=sid)
            if tiles:
                built[rid] = {"id": rid, "title": title, "layout": ROW_LAYOUT.get(rid, "poster"), "tiles": tiles}

        hero: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pool in (cw, recent, trending):
            for t in pool:
                if t["id"] in seen or not (t.get("backdrop") or t.get("poster")):
                    continue
                if t["kind"] in ("channel", "season"):
                    continue
                hero.append(t)
                seen.add(t["id"])
                if len(hero) >= HERO_SIZE:
                    break
            if len(hero) >= HERO_SIZE:
                break
        return {"hero": hero, "rows": [built[r] for r in visible if r in built]}
