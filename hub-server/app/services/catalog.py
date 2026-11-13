"""CatalogService: titles, browse, search, tile resolution for arbitrary hub ids."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Select, String, case, cast, func, literal, or_, select, text, true

from app.adapters.language.inferrer import normalise_code
from app.domain.entities import CatalogItem, MediaKind, MetaTitle, SourceType
from app.domain.ports import DetailProvider, ImageAuthProvider, MetadataProvider
from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.models import Channel, Favorite, Profile, Progress, Setting, Source, Title, Watchlist
from app.services import ids
from app.services.adult import general_audience, is_general_audience
from app.services.availability import ATTRIBUTION, AvailabilityService
from app.services.context import absolute
from app.services.errors import BadGateway, Forbidden, NotFound
from app.services.search import (
    CHANNEL_CAP,
    EXPANDED_CAP,
    PROGRAMME_CAP,
    search_channels,
    search_programmes,
    section_order,
    split_year,
    title_rank,
)
from app.services.search_match import CANDIDATE_LIMIT, anchors, search_tokens, token_match_rank
from app.services.sources import LAZY_GEN, SourceService, item_row, upsert_titles
from app.services.tiles import TileContext, badge_for_source, hub_id_for_title, meta_tile, row_to_item, sort_badges

log = logging.getLogger(__name__)

PAGE_SIZE = 60
BROWSE_KINDS: dict[str, tuple[str, ...]] = {
    "movies": ("movie", "vod"),
    "shows": ("show", "series"),
    "vod": ("vod",),
    "series": ("series",),
}
SORTS = {"added", "title", "year", "rating"}
NEW_RELEASES = "New Releases"
# A shelf that received nothing for this long counts half when shelves are ranked.
SHELF_FRESH = timedelta(days=30)
HOME_SHELVES = "home_shelves"
# All the remote lookups of one search may take this long together; the answer then goes out without the rest.
DISCOVERY_BUDGET = 2.5
# How long the metadata provider's answer for a query is kept, and for how many queries.
DISCOVERY_TTL = 300.0
DISCOVERY_KEPT = 256
# One-typo matches join a title search only when it found fewer than this, for a query at least this long.
TYPO_BELOW = 5
TYPO_MIN_LENGTH = 4


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def is_replay_shelf(name: str) -> bool:
    """Recorded sports events: a library of fixtures, not something to put on a billboard."""
    return "replay" in name.lower()


def shelf_path(section: str, name: str) -> str:
    """Where a client's "See all" goes for one shelf."""
    return f"/browse/{section}?category={quote(name, safe='')}"


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
        self._metadata_slots = asyncio.Semaphore(6)
        # What the metadata provider answered for a query, for a few minutes; and the searches still
        # in flight, so that one outliving its budget is waited on, not asked again.
        self._discovered: dict[str, tuple[float, list[MetaTitle]]] = {}
        self._discovering: dict[str, asyncio.Task[list[MetaTitle]]] = {}
        self._late: set[asyncio.Future[Any]] = set()

    content_policy: Any = None

    # ------------------------------------------------------------------ context
    def image_fn(self, srcs: dict[str, Source]) -> Callable[[Source | None, str | None, str | None], str | None]:
        from app.services.image_grants import WEB_SCHEMES, image_payload

        proxied: dict[str, bool] = {}  # per call: the answer needs a config decrypt

        def via_hub(src: Source, url: str) -> bool:
            try:
                prov = self.sources.provider(src)
            except Exception:
                return False
            if isinstance(prov, ImageAuthProvider) and prov.image_headers(url) is not None:
                return True
            if src.id not in proxied:
                proxied[src.id] = self.sources.proxied_image_headers(src) is not None
            # Clients of a fully proxied source cannot reach its hosts, artwork included.
            return proxied[src.id] and url.startswith(WEB_SCHEMES)

        def image(src: Source | None, url: str | None, ref: str | None = None) -> str | None:
            if not url or src is None or not via_hub(src, url):
                return url
            payload = image_payload(src.id, url, ref, self.content_policy)
            if payload is None:
                return None
            token = self._crypto.sign_json(payload)
            return absolute(f"/api/v1/images/{token}")

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

    async def rows_for_tmdb(self, media_type: str, tmdb_id: int, *, general: bool = False) -> list[Title]:
        kinds = ids.MOVIE_KINDS if media_type == "movie" else ids.SHOW_KINDS
        query = select(Title).where(Title.tmdb_id == tmdb_id, Title.kind.in_(kinds))
        if general:
            query = query.where(general_audience())
        async with self._db.session() as s:
            return list((await s.execute(query)).scalars())

    async def tiles_for_ids(self, ref_ids: list[str], ctx: TileContext) -> list[dict[str, Any]]:
        """Resolve hub ids (tmdb:/lib:/ch:) to tiles, preserving order and skipping unknown ids.

        These are lists (home rows, favorites, history), so adult rows are skipped like
        anywhere else unless the request included them.
        """
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
                if row and row.source_id in ctx.sources and is_general_audience(row):
                    t = ctx.title_tile(row)
                    t["id"] = ids.lib_id(sid, ext)  # keep the id the client stored
                    out[t["id"]] = t
            chans: list[Channel] = []
            for sid, ext in ch_keys:
                ch = (
                    await s.execute(select(Channel).where(Channel.source_id == sid, Channel.ext_id == ext))
                ).scalar_one_or_none()
                if ch:
                    chans.append(ch)
        if chans:
            from app.services.channels import now_next, present_now_next
            from app.services.event_slots import DEFAULT_SLOT_CONFIG, parse_event_slot

            nn = await now_next(self._db, chans)
            # The slot clock is a source setting; it is read only when a slot is among the channels.
            slots = any(parse_event_slot(ch.name) for ch in chans)
            configs = await self.sources.slot_configs() if slots else {}
            for ch in chans:
                current, following = nn.get((ch.source_id, ch.epg_id or ""), (None, None))
                config = configs.get(ch.source_id, DEFAULT_SLOT_CONFIG)
                t = ctx.channel_tile(ch, present_now_next(ch, current, following, config)[0])
                out[t["id"]] = t
        for media_type, tid in tmdb_keys:
            hub = ids.tmdb_id(media_type, tid)
            rows = await self.rows_for_tmdb(media_type, tid, general=True)
            rows = [r for r in rows if r.source_id in ctx.sources]
            if rows:
                merged = ctx.merge(ctx.title_tile(r) for r in rows)
                if merged:
                    out[hub] = merged[0]
                    continue
            meta = await self.meta.details(media_type, tid) if self.meta.enabled else None
            if meta:
                badges, _ = await self.availability.badges_for(
                    media_type,
                    tmdb_id=tid,
                    imdb_id=meta.imdb_id,
                    title=meta.title,
                    year=meta.year,
                    sources=ctx.sources,
                    is_in_app=ctx.is_in_app,
                    general=True,
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
                for kid in kids:
                    # Seasons and episodes carry no category of their own; without this an
                    # adult series would leak through its episodes and language filters.
                    kid.language = kid.language or row.language
                    kid.adult = kid.adult or bool(row.adult)
                await self._lazy_store(row.source_id, kids)
            except Exception as exc:
                log.warning("children fetch failed", extra={"source": row.source_id, "err": type(exc).__name__})
        async with self._db.session() as s:
            if row.kind in ("show", "series"):
                seasons = list(
                    (
                        await s.execute(
                            select(Title)
                            .where(
                                Title.source_id == row.source_id,
                                Title.parent_ext_id == row.ext_id,
                                Title.kind == "season",
                            )
                            .order_by(Title.season_number)
                        )
                    ).scalars()
                )
                season_ids = [x.ext_id for x in seasons]
                eps = (
                    list(
                        (
                            await s.execute(
                                select(Title)
                                .where(
                                    Title.source_id == row.source_id,
                                    Title.parent_ext_id.in_(season_ids),
                                    Title.kind == "episode",
                                )
                                .order_by(Title.season_number, Title.episode_number)
                            )
                        ).scalars()
                    )
                    if season_ids
                    else []
                )
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
            return {
                **tiles[0],
                "overview": "",
                "genres": [],
                "runtime_min": None,
                "cast": [],
                "seasons": [],
                "episodes": [],
                "attribution": ATTRIBUTION,
            }
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
        if h.scheme == "tmdb" and h.a in {"movie", "tv"} and h.b.isdigit():
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
        try:
            async with asyncio.timeout(8):
                meta = await self.meta.details(media_type, tmdb_id) if self.meta.enabled else None
        except Exception:
            meta = None
        if meta and (meta.media_type != media_type or meta.tmdb_id != tmdb_id):
            meta = None
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
            refs = [ids.lib_id(m.source_id, m.ext_id) for m in matches]
            tile = meta_tile(meta, badges, ctx.favorites, ctx.progress_pct(hub, *refs))
            tile["is_favorite"] = hub in ctx.favorites or any(ref in ctx.favorites for ref in refs)
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
            refs = [ids.lib_id(m.source_id, m.ext_id) for m in matches]
            tile["is_favorite"] = hub in ctx.favorites or any(ref in ctx.favorites for ref in refs)
            tile["progress_pct"] = ctx.progress_pct(hub, *refs)
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

    async def discover(
        self,
        section: str,
        profile_id: str,
        *,
        page: int = 1,
        sort: str = "popular",
        year: int | None = None,
        language: str | None = None,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        media_type = {"movies": "movie", "shows": "tv"}.get(section)
        if media_type is None:
            raise NotFound("Unknown discovery section")
        if not self.meta.enabled:
            raise BadGateway(
                "Connect metadata in Settings to discover movies and series. Your library remains available."
            )
        policy = self.content_policy
        if policy is None:
            raise Forbidden("Content policy service is unavailable.", code="content_policy_unavailable")
        restricted = policy.restricts(await policy.profile(profile_id))
        ctx = await self.tile_context(profile_id)
        enabled = self._enabled(ctx)
        if source_id:
            enabled = [sid for sid in enabled if sid == source_id]
        languages = await self.preferred_languages(profile_id, language)
        try:
            async with asyncio.timeout(12):
                async with self._metadata_slots:
                    metas, total = await self.meta.discover(media_type, page, sort, year)
                by_id = {
                    ids.tmdb_id(meta.media_type, meta.tmdb_id): meta
                    for meta in metas[:20]
                    if meta.media_type == media_type
                }
                # Restricted discovery uses verified classification only. Provider
                # counts are not counts of permitted titles and must not be shown.
                if restricted:
                    allowed = await policy.allowed_refs(profile_id, list(by_id), proven=True)
                    by_id = {hub: meta for hub, meta in by_id.items() if hub in allowed}
                matches = await self.availability.library_matches_many(list(by_id.values()), general=True)
                match_ids = {row.id for rows in matches.values() for row in rows if row.source_id in enabled}
                eligible_ids: set[int] = set()
                if match_ids:
                    query = self._filter_title_languages(select(Title).where(Title.id.in_(match_ids)), languages)
                    async with self._db.session() as session:
                        eligible_ids = {row.id for row in (await session.execute(query)).scalars()}
                scoped_sources = {sid: src for sid, src in ctx.sources.items() if sid in enabled}

                async def tile(hub: str, meta: Any) -> dict[str, Any]:
                    badges = [
                        badge_for_source(
                            ctx.sources[row.source_id],
                            in_app=ctx.is_in_app(ctx.sources[row.source_id].type),
                            quality=row.quality,
                        )
                        for row in matches[hub]
                        if row.id in eligible_ids
                    ]
                    async with self._metadata_slots:
                        badges += await self.availability.handoff_badges(media_type, meta.tmdb_id, scoped_sources)
                    return meta_tile(meta, sort_badges(badges, ctx.sources), ctx.favorites, ctx.progress_pct(hub))

                tiles = await asyncio.gather(*(tile(hub, meta) for hub, meta in by_id.items()))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise BadGateway("Metadata discovery is temporarily unavailable. Your library remains available.") from exc
        return {
            "tiles": tiles,
            "page": page,
            "page_size": 20,
            "total": None if restricted else total,
            "has_more": page < min(500, (total + 19) // 20),
            "coverage_note": (
                "Only titles with permitted, verified classifications are shown. "
                "Pages may be sparse; a permitted total is unavailable."
            )
            if restricted
            else (
                "Metadata catalog; coverage varies. Source and language filters affect availability, "
                "not which titles are discovered."
            ),
        }

    # ------------------------------------------------------------------ browse
    def _browse_query(
        self, kinds: tuple[str, ...], source_ids: list[str], source_id: str | None, genre: str | None
    ) -> Select[Any]:
        q = select(Title).where(Title.kind.in_(kinds), Title.source_id.in_(source_ids), general_audience())
        if source_id:
            q = q.where(Title.source_id == source_id)
        if genre:
            values = func.json_each(Title.genres).table_valued("value")
            q = q.where(
                select(values.c.value).where(func.lower(func.trim(values.c.value)) == genre.strip().lower()).exists()
            )
        return q

    async def preferred_languages(self, profile_id: str, language: str | None) -> list[str]:
        if language is None:
            async with self._db.session() as s:
                profile = await s.get(Profile, profile_id)
            values = profile.languages if profile else []
        else:
            values = language.split(",")
        if any(str(v).strip().lower() == "all" for v in values):
            return []
        return [normalise_code(str(v)) or str(v).strip() for v in values if str(v).strip()]

    @staticmethod
    def _filter_title_languages(query: Select[Any], languages: list[str]) -> Select[Any]:
        # Title.language is fixed at sync (explicit tag, source default, category hint).
        # It is a provider hint, not verified audio metadata; unknown stays "und".
        return query.where(Title.language.in_(languages)) if languages else query

    async def browse_categories(
        self,
        section: str,
        profile_id: str,
        *,
        source_id: str | None = None,
        language: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        kinds = BROWSE_KINDS.get(section)
        if kinds is None:
            raise NotFound("unknown section")
        ctx = await self.tile_context(profile_id)
        query = self._browse_query(kinds, self._enabled(ctx), source_id, None)
        if q and q.strip():
            query = query.where(Title.norm_title.like("%" + _escape_like(ids.normalise_title(q)) + "%", escape="\\"))
        query = self._filter_title_languages(query, await self.preferred_languages(profile_id, language))
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    query.with_only_columns(Title.group_name, func.count(func.distinct(self._canonical_key())))
                    .group_by(Title.group_name)
                    .order_by(Title.group_name)
                )
            ).all()
        return [{"name": group, "count": int(count)} for group, count in rows if group]

    async def browse_languages(
        self, section: str, profile_id: str, source_id: str | None = None
    ) -> list[dict[str, Any]]:
        kinds = BROWSE_KINDS.get(section)
        if kinds is None:
            raise NotFound("unknown section")
        ctx = await self.tile_context(profile_id)
        query = self._browse_query(kinds, self._enabled(ctx), source_id, None)
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    query.with_only_columns(Title.language, func.count(func.distinct(self._canonical_key()))).group_by(
                        Title.language
                    )
                )
            ).all()
        counts = {code: int(count) for code, count in rows}
        return [
            {"code": code, "name": self.sources.language.language_name(code), "count": count}
            for code, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ]

    @staticmethod
    def _canonical_key() -> ColumnElement[str]:
        media_type = case((Title.kind.in_(ids.MOVIE_KINDS), "movie"), else_="tv")
        return case(
            (Title.tmdb_id != 0, literal("tmdb:") + media_type + ":" + cast(Title.tmdb_id, String)),
            else_=literal("lib:") + Title.source_id + ":" + Title.ext_id,
        )

    async def _enrich_page(self, tiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Only this bounded page; existing TMDB detail cache serves repeated IDs."""
        if not self.meta.enabled:
            return tiles
        semaphore = asyncio.Semaphore(6)

        async def enrich(tile: dict[str, Any]) -> dict[str, Any]:
            if not tile["id"].startswith("tmdb:"):
                return tile
            parsed = ids.parse(tile["id"])
            try:
                # Includes semaphore wait, so upstream outages cannot turn a page
                # into 60 sequential retry windows.
                async with asyncio.timeout(8):
                    async with semaphore:
                        meta = await self.meta.details(parsed.a, int(parsed.b))
            except Exception:
                return tile
            if meta is None or meta.media_type != parsed.a or meta.tmdb_id != int(parsed.b):
                return tile
            enriched = meta_tile(meta, tile["sources"], set(), tile.get("progress_pct"))
            enriched["is_favorite"] = tile["is_favorite"]
            for field in ("poster", "backdrop", "logo", "rating"):
                if enriched.get(field) is None:
                    enriched[field] = tile.get(field)
            return enriched

        return list(await asyncio.gather(*(enrich(tile) for tile in tiles)))

    async def browse(
        self,
        section: str,
        profile_id: str,
        *,
        source_id: str | None,
        genre: str | None,
        sort: str | None,
        page: int,
        language: str | None = None,
        q: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        kinds = BROWSE_KINDS.get(section)
        if kinds is None:
            raise NotFound("unknown section")
        ctx = await self.tile_context(profile_id)
        enabled = self._enabled(ctx)
        page = max(1, page)
        text_query = q
        query = self._browse_query(kinds, enabled, source_id, genre)
        if category is not None:
            query = query.where(Title.group_name == category)
        if text_query and text_query.strip():
            query = query.where(
                Title.norm_title.like("%" + _escape_like(ids.normalise_title(text_query)) + "%", escape="\\")
            )
        query = self._filter_title_languages(query, await self.preferred_languages(profile_id, language))
        # Count and page identities before loading variants. Merging raw pages
        # allowed the same TMDB film to recur on later pages and inflated totals.
        key = self._canonical_key()
        grouped = (
            query.with_only_columns(
                key.label("hub"),
                func.min(Title.norm_title).label("title_sort"),
                func.max(Title.year).label("year_sort"),
                func.max(Title.rating).label("rating_sort"),
                func.max(Title.added_at).label("added_sort"),
                func.max(Title.id).label("id_sort"),
            )
            .group_by(key)
            .subquery()
        )
        orders: dict[str, list[Any]] = {
            "title": [grouped.c.title_sort],
            "year": [grouped.c.year_sort.desc().nulls_last(), grouped.c.title_sort],
            "rating": [grouped.c.rating_sort.desc().nulls_last(), grouped.c.title_sort],
        }
        order = orders.get(sort or "added", [grouped.c.added_sort.desc().nulls_last(), grouped.c.id_sort.desc()])
        tracked = ctx.favorites | set(ctx.progress)
        lib_key = literal("lib:") + Title.source_id + ":" + Title.ext_id
        async with self._db.session() as session:
            total = int((await session.execute(select(func.count()).select_from(grouped))).scalar_one())
            hubs = list(
                (
                    await session.execute(
                        select(grouped.c.hub)
                        .order_by(*order, grouped.c.hub)
                        .offset((page - 1) * PAGE_SIZE)
                        .limit(PAGE_SIZE)
                    )
                ).scalars()
            )
            if not hubs:
                return {"tiles": [], "page": page, "total": total, "has_more": False}
            page_query = query.where(key.in_(hubs))
            representatives = page_query.with_only_columns(func.min(Title.id)).group_by(key, Title.source_id)
            rows = list(
                (
                    await session.execute(
                        select(Title).where(Title.id.in_(representatives)).order_by(Title.source_id, Title.id)
                    )
                ).scalars()
            )
            refs = (
                list(
                    (
                        await session.execute(
                            select(key, lib_key).where(
                                key.in_(hubs), Title.source_id.in_(enabled), lib_key.in_(tracked)
                            )
                        )
                    ).all()
                )
                if tracked
                else []
            )
        merged = {tile["id"]: tile for tile in ctx.merge(ctx.title_tile(row) for row in rows)}
        references: dict[str, list[str]] = {}
        for hub, ref in refs:
            references.setdefault(hub, []).append(ref)
        for hub, tile in merged.items():
            matched_refs = sorted(references.get(hub, []))
            tile["is_favorite"] = hub in ctx.favorites or any(ref in ctx.favorites for ref in matched_refs)
            tile["progress_pct"] = ctx.progress_pct(hub, *matched_refs)
        tiles = await self._enrich_page([merged[hub] for hub in hubs])
        return {"tiles": tiles, "page": page, "total": total, "has_more": page * PAGE_SIZE < total}

    # ------------------------------------------------------------------ shelves
    async def shelf_stats(
        self,
        section: str,
        ctx: TileContext,
        languages: list[str],
        *,
        source_id: str | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Shelves of a section, best first: New Releases, then by size, stale shelves counting half."""
        kinds = BROWSE_KINDS.get(section)
        if kinds is None:
            raise NotFound("unknown section")
        query = self._filter_title_languages(self._browse_query(kinds, self._enabled(ctx), source_id, None), languages)
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    query.where(Title.group_name.is_not(None), Title.group_name != "")
                    .with_only_columns(
                        Title.group_name, func.count(func.distinct(self._canonical_key())), func.max(Title.added_at)
                    )
                    .group_by(Title.group_name)
                )
            ).all()
        fresh_since = (now or datetime.now(UTC)) - SHELF_FRESH

        def rank(row: Any) -> tuple[bool, float, str]:
            name, count, latest = row
            fresh = latest is not None and latest >= fresh_since
            return name != NEW_RELEASES, -(count if fresh else count / 2), name

        return [{"name": name, "count": int(count)} for name, count, _ in sorted(rows, key=rank)]

    async def shelf_items(
        self,
        section: str,
        names: list[str],
        ctx: TileContext,
        languages: list[str],
        limit: int,
        *,
        source_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """The first tiles of each named shelf in one query: titles with a poster first, then newest."""
        kinds = BROWSE_KINDS.get(section)
        if kinds is None:
            raise NotFound("unknown section")
        if not names:
            return {}
        query = self._filter_title_languages(self._browse_query(kinds, self._enabled(ctx), source_id, None), languages)
        # Twice the limit: variants of one title merge into a single tile afterwards.
        position = (
            func.row_number()
            .over(
                partition_by=Title.group_name,
                order_by=(Title.poster.is_(None), Title.added_at.desc().nulls_last(), Title.id.desc()),
            )
            .label("position")
        )
        ranked = query.where(Title.group_name.in_(names)).with_only_columns(Title.id, position).subquery()
        async with self._db.session() as session:
            rows = list(
                (
                    await session.execute(
                        select(Title)
                        .join(ranked, ranked.c.id == Title.id)
                        .where(ranked.c.position <= limit * 2)
                        .order_by(ranked.c.position)
                    )
                ).scalars()
            )
        by_shelf: dict[str, list[Title]] = {}
        for row in rows:
            by_shelf.setdefault(str(row.group_name), []).append(row)
        return {name: ctx.merge(ctx.title_tile(row) for row in found)[:limit] for name, found in by_shelf.items()}

    async def shelves(
        self,
        section: str,
        profile_id: str,
        *,
        language: str | None = None,
        source_id: str | None = None,
        limit_rows: int = 12,
        limit_items: int = 20,
    ) -> dict[str, Any]:
        ctx = await self.tile_context(profile_id)
        languages = await self.preferred_languages(profile_id, language)
        stats = (await self.shelf_stats(section, ctx, languages, source_id=source_id))[:limit_rows]
        items = await self.shelf_items(
            section, [shelf["name"] for shelf in stats], ctx, languages, limit_items, source_id=source_id
        )
        return {
            "shelves": [
                {**shelf, "items": items[shelf["name"]], "browse_path": shelf_path(section, shelf["name"])}
                for shelf in stats
                if items.get(shelf["name"])
            ]
        }

    async def spotlight(
        self, section: str, shelf: str, ctx: TileContext, languages: list[str], limit: int, *, billboard: bool
    ) -> list[dict[str, Any]]:
        """Newest titles of one shelf fit to feature; never an adult row, whatever the request asked to list.

        ``billboard`` keeps titles with a backdrop and a rating of 6 or more; without it any artwork will do.
        """
        art = Title.backdrop.is_not(None) if billboard else or_(Title.backdrop.is_not(None), Title.poster.is_not(None))
        query = self._browse_query(BROWSE_KINDS[section], self._enabled(ctx), None, None).where(
            Title.group_name == shelf, Title.adult.is_not(True), art
        )
        if billboard:
            query = query.where(Title.rating >= 6)
        query = self._filter_title_languages(query, languages)
        async with self._db.session() as session:
            rows = list(
                (
                    await session.execute(
                        query.order_by(Title.added_at.desc().nulls_last(), Title.id.desc()).limit(limit * 2)
                    )
                ).scalars()
            )
        return ctx.merge(ctx.title_tile(row) for row in rows)[:limit]

    async def home_shelf_names(self) -> list[str]:
        """The owner's ordered ``home_shelves`` setting; empty means Home chooses its shelves."""
        async with self._db.session() as session:
            row = await session.get(Setting, HOME_SHELVES)
        value = row.value if row is not None else None
        return [str(name).strip() for name in value if str(name).strip()] if isinstance(value, list) else []

    async def unknown_shelves(self, entries: list[str]) -> list[str]:
        """The ``home_shelves`` entries that name no shelf of an enabled source right now.

        Read the way Home reads them: ``movies:Name`` and ``shows:Name`` name one side, a bare
        name the shelf on either. Not scoped to any profile: this is the administrator's list.
        """
        from app.services.content_policy import active_policy
        from app.services.visibility import watch_scope

        if not entries:
            return []
        known: dict[str, set[str]] = {}
        reset = active_policy.set(None)
        try:
            with watch_scope(None):
                async with self._db.session() as session:
                    for section in ("movies", "shows"):
                        rows = await session.execute(
                            select(Title.group_name)
                            .join(Source, Source.id == Title.source_id)
                            .where(
                                Source.enabled.is_(True),
                                Title.kind.in_(BROWSE_KINDS[section]),
                                Title.group_name != "",
                            )
                            .distinct()
                        )
                        known[section] = {name for name in rows.scalars() if name}
        finally:
            active_policy.reset(reset)
        unknown = []
        for entry in entries:
            scope, _, name = entry.partition(":")
            sides = [(scope, name)] if scope in known and name else [("movies", entry), ("shows", entry)]
            if not any(shelf in known[side] for side, shelf in sides):
                unknown.append(entry)
        return unknown

    async def genre_facets(
        self,
        section: str,
        profile_id: str,
        *,
        source_id: str | None = None,
        language: str | None = None,
        category: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        kinds = BROWSE_KINDS.get(section)
        if kinds is None:
            raise NotFound("unknown section")
        ctx = await self.tile_context(profile_id)
        query = self._browse_query(kinds, self._enabled(ctx), source_id, None)
        if category is not None:
            query = query.where(Title.group_name == category)
        if q and q.strip():
            query = query.where(Title.norm_title.like("%" + _escape_like(ids.normalise_title(q)) + "%", escape="\\"))
        query = self._filter_title_languages(query, await self.preferred_languages(profile_id, language))
        scoped = query.with_only_columns(self._canonical_key().label("hub"), Title.genres).subquery()
        values = func.json_each(scoped.c.genres).table_valued("value", "type")
        name = func.lower(func.trim(values.c.value))
        statement = (
            select(name.label("name"), func.count(func.distinct(scoped.c.hub)))
            .select_from(scoped.join(values, true()))
            .where(values.c.type == "text", name != "")
            .group_by(name)
            .order_by(name)
        )
        async with self._db.session() as session:
            rows = (await session.execute(statement)).all()
        return [{"name": name, "count": count} for name, count in rows]

    async def genres(self, section: str) -> list[str]:
        # Legacy internal helper; full enabled catalog, without arbitrary truncation.
        values = func.json_each(Title.genres).table_valued("value", "type")
        statement = (
            select(values.c.value)
            .select_from(Title)
            .join(Source, Source.id == Title.source_id)
            .join(values, true())
            .where(Source.enabled.is_(True), Title.kind.in_(BROWSE_KINDS.get(section, ())), values.c.type == "text")
            .distinct()
            .order_by(values.c.value)
        )
        async with self._db.session() as session:
            return list((await session.execute(statement)).scalars())

    # ------------------------------------------------------------------ search
    async def search(
        self,
        q: str,
        profile_id: str,
        kinds: set[str] | None,
        limit: int,
        language: str | None = None,
        source_id: str | None = None,
        expand: frozenset[str] = frozenset(),
        discover: bool = True,
    ) -> dict[str, Any]:
        """Titles, channels and guide broadcasts for one query; each section capped and counted.

        ``expand`` names the sections to return in full (up to ``EXPANDED_CAP``): "Show more".
        Without ``discover`` the answer is what the hub holds, and no remote provider is asked:
        what a client shows at once, while the full answer is on its way.
        """
        from app.services.channels import ChannelService

        q = q.strip()
        # A series is found by its name; its episodes are listed only for a client that asks for them.
        want = kinds or {"movie", "show", "vod", "series", "channel", "programme"}
        limit = EXPANDED_CAP if "titles" in expand else max(1, min(limit, 100))
        chsvc = ChannelService(self._db, self.sources)
        chsvc.image_fn = self.image_fn

        # A search asks for something by name. The profile's languages come first, as in every listing;
        # a section that finds nothing in them looks in the others before it says there is nothing.
        # Nobody has to find a language switch to reach a channel they typed the name of.
        widen = language is None and bool(await self.preferred_languages(profile_id, None))

        async def channels() -> tuple[list[dict[str, Any]], int, int | None]:
            if "channel" not in want:
                return [], 0, None
            cap = EXPANDED_CAP if "channels" in expand else CHANNEL_CAP
            found = await search_channels(chsvc, profile_id, q, language=language, source_id=source_id, cap=cap)
            if widen and not found[0]:
                found = await search_channels(chsvc, profile_id, q, language="all", source_id=source_id, cap=cap)
            return found

        async def programmes() -> tuple[list[dict[str, Any]], int, bool]:
            if "programme" not in want:
                return [], 0, False
            cap = EXPANDED_CAP if "programmes" in expand else PROGRAMME_CAP
            aired = await search_programmes(chsvc, profile_id, q, language=language, source_id=source_id, cap=cap)
            if widen and not aired[0]:
                aired = await search_programmes(chsvc, profile_id, q, language="all", source_id=source_id, cap=cap)
            return aired

        # The sections read different tables; side by side, each under this request's scopes.
        (
            titles,
            (found_channels, channel_total, channel_rank),
            (broadcasts, broadcast_total, more),
        ) = await asyncio.gather(
            self._search_titles(q, profile_id, want, limit, language, source_id, discover), channels(), programmes()
        )
        count = titles.pop("count")
        return {
            **titles,
            "channels": found_channels,
            "programmes": broadcasts,
            "sections": {
                "titles": {"count": count, "more": count > limit},
                "channels": {"count": channel_total, "more": channel_total > len(found_channels)},
                "programmes": {"count": broadcast_total, "more": more},
            },
            "order": section_order(channel_rank),
            "attribution": ATTRIBUTION,
        }

    async def _provider_search(self, query: str) -> list[MetaTitle]:
        """The metadata provider's answer for a query: asked once, kept for ``DISCOVERY_TTL``.

        Concurrent callers share the one search in flight, and it is never cancelled with its
        caller: an answer that comes after the caller's budget is there for the next request.
        """
        key = " ".join(query.split()).casefold()
        kept = self._discovered.get(key)
        if kept is not None and kept[0] > time.monotonic():
            return kept[1]
        task = self._discovering.get(key)
        if task is None:

            async def ask() -> list[MetaTitle]:
                try:
                    metas = await self.meta.search(query, limit=40)
                finally:
                    self._discovering.pop(key, None)
                self._discovered.pop(key, None)
                while len(self._discovered) >= DISCOVERY_KEPT:
                    self._discovered.pop(next(iter(self._discovered)))
                self._discovered[key] = (time.monotonic() + DISCOVERY_TTL, metas)
                return metas

            task = self._discovering[key] = asyncio.create_task(ask())
            # A failure nobody is left waiting for is not an unhandled one.
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
        return await asyncio.shield(task)

    async def _in_time(self, work: dict[Any, Any], seconds: float) -> tuple[dict[Any, Any], bool]:
        """Run remote lookups side by side for at most ``seconds``: ``(what finished, all finished)``.

        A lookup still running is left to finish, so that its provider-side cache holds the
        answer next time; one that failed is simply absent.
        """
        if not work:
            return {}, True
        tasks = {key: asyncio.ensure_future(job) for key, job in work.items()}
        _, pending = await asyncio.wait(tasks.values(), timeout=max(seconds, 0.0))
        for task in pending:
            self._late.add(task)
            task.add_done_callback(self._finished_late)
        finished = {
            key: task.result()
            for key, task in tasks.items()
            if task not in pending and not task.cancelled() and task.exception() is None
        }
        return finished, not pending

    def _finished_late(self, task: asyncio.Future[Any]) -> None:
        self._late.discard(task)
        if not task.cancelled():
            task.exception()  # looked at, so a failure nobody waited for is not reported as unhandled

    async def _search_titles(
        self,
        q: str,
        profile_id: str,
        want: set[str],
        limit: int,
        language: str | None,
        source_id: str | None,
        discover: bool = True,
    ) -> dict[str, Any]:
        """The titles of a search, ranked: the library's own rows and what metadata discovery adds.

        The library half is one bounded SQL pass and never leaves the hub. Discovery asks the
        metadata provider, within ``DISCOVERY_BUDGET`` in all: past it the answer goes out with
        what has arrived, marked incomplete so that it is not cached as the whole of it.
        """
        ctx = await self.tile_context(profile_id)
        enabled = self._enabled(ctx)
        # "the mill 2023": the year narrows, and is not part of the name that is matched.
        named, year = split_year(q)
        norm = ids.normalise_title(named if year else q)
        languages = await self.preferred_languages(profile_id, language)
        if source_id:
            enabled = [sid for sid in enabled if sid == source_id]
        lib_kinds = set(want) & {"movie", "show", "vod", "series", "episode"}
        if lib_kinds & {"movie", "vod"}:
            lib_kinds.update(("movie", "vod"))
        if lib_kinds & {"show", "series"}:
            lib_kinds.update(("show", "series"))
        tokens = search_tokens(norm)
        words = anchors(tokens) or norm.split()[:8]
        rows: list[Title] = []
        unlisted = 0  # library matches counted but not built into tiles: beyond what a page can use
        if norm and lib_kinds and words and enabled:
            # FTS supplies candidate IDs only. The ORM scope below still enforces
            # enabled sources, profile classifications, kinds and language filters.
            match = " OR ".join('"' + word.replace('"', '""') + '"*' for word in words)
            matching_ids = text("SELECT rowid FROM title_search WHERE title_search MATCH :match").bindparams(
                match=match
            )
            if len(norm) >= 3:
                # FTS finds words by their start; the phrase inside a word ("mill" in "Windmill")
                # is one pass over the normalised-title index, and ranks last.
                matching_ids = text(
                    "SELECT rowid FROM title_search WHERE title_search MATCH :match "
                    "UNION SELECT id FROM title WHERE norm_title LIKE :contains ESCAPE '\\'"
                ).bindparams(match=match, contains="%" + _escape_like(norm) + "%")
            query = select(Title).where(
                Title.source_id.in_(enabled), Title.kind.in_(lib_kinds), Title.id.in_(matching_ids), general_audience()
            )
            if year:
                query = query.where(or_(Title.year == year, Title.title.like(f"%{year}%")))
            query = self._filter_title_languages(query, languages)
            score: ColumnElement[int] = literal(0)
            for word in words:
                score = score + case((Title.norm_title.like("%" + _escape_like(word) + "%", escape="\\"), 1), else_=0)
            work = case(
                ((Title.tmdb_id > 0) & Title.kind.in_(("movie", "vod", "show", "series")), self._canonical_key()),
                else_=literal("lib:") + Title.source_id + ":" + Title.ext_id,
            )
            # Ranked in SQL on the stored normalised title: exact, the whole title after a provider's
            # prefix (which the stored form keeps: it is the exact title once shown), title prefix,
            # word prefix, contains. Two letters can start a word in a fifth of a catalog, so the
            # work is bounded at every step: a plain top-N of the matches first, then one row per
            # work among those (duplicate editions cannot crowd others out), then only as many
            # full rows as a page can use reach Python, where the shown title decides the order.
            phrase = case(
                (Title.norm_title == norm, 0),
                (Title.norm_title.like("% " + _escape_like(norm), escape="\\"), 1),
                (Title.norm_title.like("% " + _escape_like(norm) + " " + cast(Title.year, String), escape="\\"), 1),
                (Title.norm_title.like(_escape_like(norm) + "%", escape="\\"), 2),
                (Title.norm_title.like("% " + _escape_like(norm) + "%", escape="\\"), 3),
                (Title.norm_title.like("%" + _escape_like(norm) + "%", escape="\\"), 4),
                else_=5,
            )
            length = func.length(Title.norm_title)
            best = (
                query.with_only_columns(
                    Title.id.label("id"),
                    phrase.label("phrase"),
                    score.label("score"),
                    length.label("length"),
                    work.label("work"),
                )
                .order_by(phrase, score.desc(), length, Title.id)
                .limit(CANDIDATE_LIMIT * 4)
                .subquery()
            )
            order = (best.c.phrase, best.c.score.desc(), best.c.length, best.c.id)
            ranked = select(
                best.c.id,
                best.c.phrase,
                best.c.score,
                best.c.length,
                func.row_number().over(partition_by=best.c.work, order_by=order).label("edition"),
            ).subquery()
            named_rows = (
                select(Title.id, Title.title)
                .join(ranked, ranked.c.id == Title.id)
                .where(ranked.c.edition == 1)
                .order_by(ranked.c.phrase, ranked.c.score.desc(), ranked.c.length, ranked.c.id)
                .limit(CANDIDATE_LIMIT)
            )
            async with self._db.session() as session:
                candidates = (await session.execute(named_rows)).all()
                shown = {pk: ids.normalise_title(ids.clean_display_title(name)) for pk, name in candidates}
                loose = {pk: token_match_rank(tokens, shown[pk]) for pk in shown if norm not in shown[pk]}
                found = [pk for pk in shown if norm in shown[pk] or loose[pk] in (0, 1)]
                # A word one typo away is a guess: offered only when little else was found, and
                # never for a query too short to tell a typo from another word.
                if len(found) < TYPO_BELOW and len(norm) >= TYPO_MIN_LENGTH:
                    found += [pk for pk in shown if loose.get(pk) == 2]
                # Three pages' worth is enough to choose one page from: the SQL order above and
                # the final one differ only in how a provider's decorations are read.
                page = found[: max(limit * 3, 120)]
                unlisted = len(found) - len(page)
                loaded = {
                    row.id: row for row in (await session.execute(select(Title).where(Title.id.in_(page)))).scalars()
                }
            rows = [loaded[pk] for pk in page if pk in loaded]
        local_tiles = ctx.merge(ctx.title_tile(r) for r in rows)
        combined = {tile["id"]: tile for tile in local_tiles}
        discoverable = self.meta.enabled and bool({"movie", "show", "vod", "series"} & want)
        metadata_available = discoverable and not discover
        metadata_incomplete = False
        discovered: list[str] = []
        popularity: dict[str, float] = {}
        note = "Metadata discovery is not configured; searching your sources only."
        if discoverable:
            note = "Metadata coverage varies. Language and source filters limit availability, not discovery."
        if discoverable and discover:
            clock = asyncio.get_running_loop().time
            deadline = clock() + DISCOVERY_BUDGET
            try:
                metas = await asyncio.wait_for(self._provider_search(named if year else q), DISCOVERY_BUDGET)
                metadata_available = True
                metas = [
                    m
                    for m in metas
                    if (
                        (m.media_type == "movie" and {"movie", "vod"} & want)
                        or (m.media_type == "tv" and {"show", "series"} & want)
                    )
                    and (not year or m.year == year or str(year) in m.title)
                ]
                by_id = {ids.tmdb_id(m.media_type, m.tmdb_id): m for m in metas}
                # Resolve IDs on the bounded local result page; translated provider
                # labels must not replace the metadata title/artwork.
                missing = [
                    (ids.tmdb_type_for_kind(r.kind), r.tmdb_id)
                    for r in rows
                    if r.tmdb_id and ids.tmdb_type_for_kind(r.kind) and hub_id_for_title(r) not in by_id
                ]
                details, whole = await self._in_time(
                    {
                        (kind, tid): self.meta.details(kind, tid)
                        for kind, tid in list(dict.fromkeys(missing))[:20]
                        if kind is not None
                    },
                    deadline - clock(),
                )
                metadata_incomplete = not whole
                for detail in details.values():
                    if detail is not None:
                        by_id[ids.tmdb_id(detail.media_type, detail.tmdb_id)] = detail
                matched = await self.availability.library_matches_many(
                    list(by_id.values()), candidates=rows, general=True
                )
                match_ids = {r.id for matches in matched.values() for r in matches if r.source_id in enabled}
                eligible_ids: set[int] = set()
                if match_ids:
                    eligible_query = self._filter_title_languages(
                        select(Title).where(Title.id.in_(match_ids)), languages
                    )
                    async with self._db.session() as session:
                        eligible_ids = {r.id for r in (await session.execute(eligible_query)).scalars()}
                scoped_sources = {sid: src for sid, src in ctx.sources.items() if sid in enabled}
                # Where else a title streams is a lookup per title, and only a household with a
                # streaming service among its sources has any use for it.
                handoffs: dict[str, list[dict[str, Any]]] = {}
                if any(src.type == SourceType.STREAMING_SERVICE.value for src in scoped_sources.values()):

                    async def elsewhere(meta: MetaTitle) -> list[dict[str, Any]]:
                        async with self._metadata_slots:
                            return await self.availability.handoff_badges(meta.media_type, meta.tmdb_id, scoped_sources)

                    handoffs, whole = await self._in_time(
                        {hub: elsewhere(meta) for hub, meta in by_id.items()}, deadline - clock()
                    )
                    metadata_incomplete = metadata_incomplete or not whole
                local_ids, local_rows = set(combined), {r.id for r in rows}
                for hub, meta in by_id.items():
                    popularity[hub] = meta.popularity
                    eligible = [r for r in matched[hub] if r.id in eligible_ids]
                    badges = [
                        badge_for_source(
                            ctx.sources[r.source_id],
                            in_app=ctx.is_in_app(ctx.sources[r.source_id].type),
                            quality=r.quality,
                        )
                        for r in eligible
                    ]
                    badges += handoffs.get(hub, [])
                    # What discovery adds, as opposed to a library title it only gave its proper name.
                    if hub not in local_ids and not any(r.id in local_rows for r in eligible):
                        discovered.append(hub)
                    for row in eligible:
                        combined.pop(hub_id_for_title(row), None)
                    combined[hub] = meta_tile(
                        meta, sort_badges(badges, ctx.sources), ctx.favorites, ctx.progress_pct(hub)
                    )
            except TimeoutError:
                # Still being asked (see ``_provider_search``): the library's own answer goes out now.
                note = "Metadata discovery is taking a while; showing source matches."
                metadata_available = True
                metadata_incomplete = True
            except Exception:
                log.warning("Metadata discovery unavailable", exc_info=False)
                note = "Metadata discovery is temporarily unavailable; showing source matches."
                metadata_available = False
                metadata_incomplete = True

        def relevance(tile: dict[str, Any]) -> tuple[Any, ...]:
            title_norm = ids.normalise_title(tile["title"])
            rank = title_rank(norm, tokens, title_norm)
            # At equal rank what can be played here leads, then what opens elsewhere, then metadata alone.
            owned = 0 if any(b["playable_in_app"] for b in tile["sources"]) else 1 if tile["sources"] else 2
            return (
                8 if rank is None else rank,
                owned,
                0 if tile["id"].startswith("tmdb:") else 1,
                -popularity.get(tile["id"], 0),
                len(title_norm),
                title_norm,
                tile["id"],
            )

        if not combined and language is None and languages:
            # Nothing at all in the profile's languages: the same search in every language.
            return await self._search_titles(q, profile_id, want, limit, "all", source_id, discover)
        ordered = sorted(combined.values(), key=relevance)[:limit]
        listed = {tile["id"] for tile in ordered}
        return {
            "results": ordered,
            "metadata_available": metadata_available,
            "metadata_incomplete": metadata_incomplete,
            "discovery_note": note,
            "play_here": [tile for tile in ordered if any(b["playable_in_app"] for b in tile["sources"])],
            "open_in_app": [
                tile for tile in ordered if tile["sources"] and not any(b["playable_in_app"] for b in tile["sources"])
            ],
            "discovered": [hub for hub in discovered if hub in listed],
            "count": len(combined) + unlisted,
        }

    # ------------------------------------------------------------------ lists used by home
    async def seasonal_movies(
        self, profile_id: str, ctx: TileContext, *, now: datetime | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """October collection from explicit Halloween metadata, never inferred viewing history."""
        if (now or datetime.now(UTC)).astimezone(ZoneInfo("America/New_York")).month != 10:
            return []
        async with self._db.session() as session:
            profile = await session.get(Profile, profile_id)
        # Ratings are not yet enforced; do not present these as safe children's picks.
        if profile is None or profile.kids:
            return []
        query = select(Title).where(
            Title.kind.in_(("movie", "vod")),
            Title.source_id.in_(self._enabled(ctx)),
            or_(Title.title.ilike("%halloween%"), Title.group_name.ilike("%halloween%")),
            general_audience(),
        )
        query = self._filter_title_languages(query, await self.preferred_languages(profile_id, None))
        async with self._db.session() as session:
            rows = list((await session.execute(query.order_by(Title.title, Title.id).limit(limit * 2))).scalars())
        return ctx.merge(ctx.title_tile(row) for row in rows)[:limit]

    async def recently_added(
        self,
        ctx: TileContext,
        limit: int = 30,
        source_id: str | None = None,
        kinds: tuple[str, ...] = ("movie", "show", "vod", "series"),
        languages: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        enabled = self._enabled(ctx)
        q = select(Title).where(
            Title.kind.in_(kinds), Title.source_id.in_(enabled), Title.added_at.is_not(None), general_audience()
        )
        if source_id:
            q = q.where(Title.source_id == source_id)
        q = self._filter_title_languages(q, languages or [])
        async with self._db.session() as s:
            rows = list(
                (await s.execute(q.order_by(Title.added_at.desc(), Title.id.desc()).limit(limit * 2))).scalars()
            )
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
