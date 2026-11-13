"""HomeService: hero + rows for a profile, honouring the per-profile home layout."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.entities import SourceType
from app.domain.ports import MetadataProvider
from app.services import ids
from app.services.adult import adult_included, adult_scope
from app.services.availability import AvailabilityService
from app.services.catalog import NEW_RELEASES, CatalogService, is_replay_shelf, shelf_path
from app.services.channels import ChannelService
from app.services.recommendations import recommendations
from app.services.sources import SourceService
from app.services.tiles import TileContext, meta_tile
from app.services.user_state import HOME_ROWS, HomeLayoutService, ProgressService

log = logging.getLogger(__name__)

ROW_TITLES = dict(HOME_ROWS)
ROW_LAYOUT = {"on_now": "channel", "continue_watching": "landscape"}
ROW_PATHS = {"on_now": "/live", "watchlist": "/favorites?tab=watchlist", "favorites": "/favorites"}
HERO_SIZE = 8
SHELF_ROWS = 8  # chosen automatically, after New Releases
SHELF_TILES = 20
OWNER_SHELF_ROWS = 12
SECTION_LABEL = {"movies": "Movies", "shows": "Shows"}


def shelf_row_id(section: str, name: str) -> str:
    return f"shelf:{section}:{name}"


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
        # Shared provider credentials cannot attribute external history to a profile.
        return tiles[:30]

    async def on_now(self, profile_id: str, ctx: TileContext) -> tuple[list[dict[str, Any]], str]:
        # Ask for more than the row shows so a favourite is not cut off before it can lead.
        pairs, _ = await self._channels.on_now(profile_id, 100)
        out = []
        for ch, prog in pairs:
            out.append(ctx.channel_tile(ch, prog))
        out.sort(key=lambda tile: not tile["is_favorite"])  # stable: recent channels keep their order
        title = "On Now" if any(prog for _, prog in pairs) else "Explore live TV"
        return out[:20], title

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
                general=True,
            )
            hub = ids.tmdb_id(m.media_type, m.tmdb_id)
            out.append(meta_tile(m, badges, ctx.favorites, ctx.progress_pct(hub)))
        return out

    @staticmethod
    def _shelf_rows(stats: dict[str, list[dict[str, Any]]], owner: list[str]) -> list[tuple[str, str]]:
        """(section, shelf) per shelf row, in order: New Releases, then the owner's list or the automatic choice."""
        known = {section: [shelf["name"] for shelf in shelves] for section, shelves in stats.items()}
        picks = [("movies", NEW_RELEASES)] if NEW_RELEASES in known["movies"] else []
        if owner:
            for entry in owner:
                # "shows:Netflix" names one side; a bare name stands for the shelf wherever it exists.
                scope, _, name = entry.partition(":")
                wanted = [(scope, name)] if scope in known and name else [(s, entry) for s in ("movies", "shows")]
                picks += [pick for pick in wanted if pick[1] in known[pick[0]] and pick not in picks]
            return picks[: OWNER_SHELF_ROWS + 1]
        rest = {
            section: [(section, name) for name in names if (section, name) not in picks]
            for section, names in known.items()
        }
        # Alternate the two sides; New Releases already is a movie row, so a show shelf follows it.
        turn = "shows" if picks else "movies"
        chosen: list[tuple[str, str]] = []
        while len(chosen) < SHELF_ROWS and (rest["movies"] or rest["shows"]):
            if rest[turn]:
                chosen.append(rest[turn].pop(0))
            turn = "movies" if turn == "shows" else "shows"
        return picks + chosen

    @staticmethod
    def _order(layout: list[dict[str, Any]], source_ids: list[str], shelf_ids: list[str]) -> list[str]:
        """Visible row ids in display order.

        The layout service numbers rows by their position in HOME_ROWS. A row still on that default
        takes Home's own place instead: shelves right after On Now, Recently Added last. A position
        the profile saved is kept as it is.
        """
        all_ids = [rid for rid, _ in HOME_ROWS] + source_ids + shelf_ids
        default = {rid: float((i + 1) * 10) for i, rid in enumerate(all_ids)}
        place = dict(default)
        for i, rid in enumerate(shelf_ids):
            place[rid] = default["on_now"] + 10 * (i + 1) / (len(shelf_ids) + 1)
        place["recently_added"] = float((len(all_ids) + 1) * 10)

        def key(row: dict[str, Any]) -> tuple[float, int]:
            rid = row["id"]
            moved = rid not in default or row["sort"] != default[rid]
            return (float(row["sort"]) if moved else place[rid], all_ids.index(rid) if rid in default else len(all_ids))

        return [row["id"] for row in sorted((r for r in layout if not r["hidden"]), key=key)]

    async def _hero(
        self,
        profile_id: str,
        ctx: TileContext,
        stats: dict[str, list[dict[str, Any]]],
        languages: list[str],
        pools: tuple[list[dict[str, Any]], ...],
    ) -> list[dict[str, Any]]:
        # Shelves arrive ranked: New Releases when there is one, otherwise the largest movie shelf.
        shelf = next((s["name"] for s in stats["movies"] if not is_replay_shelf(s["name"])), None)
        if shelf is not None:
            for billboard in (True, False):
                hero = await self._catalog.spotlight("movies", shelf, ctx, languages, HERO_SIZE, billboard=billboard)
                if hero:
                    return hero
        # Nothing to feature from a shelf (no categories, or no sources yet): what this profile is
        # watching, then the newest titles, then trending.
        if adult_included():
            # The admin's opt-in lists adult rows; the billboard still never shows one.
            with adult_scope(False):
                watching = await self.continue_watching(profile_id, ctx)
                recent = await self._catalog.recently_added(ctx, 30, languages=languages)
            pools = (watching, recent, *pools[2:])
        hero = []
        seen: set[str] = set()
        for pool in pools:
            for t in pool:
                if t["id"] in seen or not (t.get("backdrop") or t.get("poster")):
                    continue
                if t["kind"] in ("channel", "season"):
                    continue
                hero.append(t)
                seen.add(t["id"])
                if len(hero) >= HERO_SIZE:
                    return hero
        return hero

    async def home(self, profile_id: str) -> dict[str, Any]:
        ctx = await self._catalog.tile_context(profile_id)
        source_ids = [
            f"source:{s.id}"
            for s in sorted(ctx.sources.values(), key=lambda x: x.priority)
            if s.enabled
            and s.type != SourceType.STREAMING_SERVICE.value
            and (s.counts or {}).get("channels", 0) != sum((s.counts or {}).values())
        ]
        languages = await self._catalog.preferred_languages(profile_id, None)
        stats = {section: await self._catalog.shelf_stats(section, ctx, languages) for section in ("movies", "shows")}
        shelves = {
            shelf_row_id(*pick): pick for pick in self._shelf_rows(stats, await self._catalog.home_shelf_names())
        }
        layout = await self._layout.get(profile_id, source_ids + list(shelves))
        visible = self._order(layout, source_ids, list(shelves))

        built: dict[str, dict[str, Any]] = {}
        cw = await self.continue_watching(profile_id, ctx)
        recent = await self._catalog.recently_added(ctx, 30, languages=languages)
        trending: list[dict[str, Any]] = []
        has_library = any(s.enabled and s.type != SourceType.STREAMING_SERVICE.value for s in ctx.sources.values())
        if "trending" in visible or not (cw or recent):
            trending = await self.trending(ctx)
        shelf_tiles = {
            section: await self._catalog.shelf_items(
                section,
                [name for rid, (side, name) in shelves.items() if side == section and rid in visible],
                ctx,
                languages,
                SHELF_TILES,
            )
            for section in ("movies", "shows")
        }
        # A name on both sides ("Netflix", "Kids") needs its side in the row title.
        on_both = {s["name"] for s in stats["movies"]} & {s["name"] for s in stats["shows"]}

        for rid in visible:
            tiles: list[dict[str, Any]] = []
            title = ROW_TITLES.get(rid, rid)
            path = ROW_PATHS.get(rid)
            if rid == "continue_watching":
                tiles = cw
            elif rid == "recommendations":
                tiles, title = await recommendations(self._catalog, profile_id, ctx)
            elif rid == "on_now":
                tiles, title = await self.on_now(profile_id, ctx)
            elif rid == "seasonal_halloween":
                tiles = await self._catalog.seasonal_movies(profile_id, ctx)
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
            elif rid in shelves:
                section, name = shelves[rid]
                tiles = shelf_tiles[section].get(name, [])
                title = f"{name} · {SECTION_LABEL[section]}" if name in on_both else name
                path = shelf_path(section, name)
            elif rid.startswith("source:"):
                sid = rid.split(":", 1)[1]
                src = ctx.sources.get(sid)
                # With one library source this row would repeat Recently Added title for title.
                if src is None or len(source_ids) < 2:
                    continue
                title = f"{src.name} · Recently added"
                tiles = await self._catalog.recently_added(ctx, 30, source_id=sid, languages=languages)
            if tiles:
                built[rid] = {
                    "id": rid,
                    "title": title,
                    "layout": ROW_LAYOUT.get(rid, "poster"),
                    "tiles": tiles,
                    "browse_path": path,
                }

        hero = await self._hero(profile_id, ctx, stats, languages, (cw, recent, trending))
        return {"hero": hero, "rows": [built[r] for r in visible if r in built]}
