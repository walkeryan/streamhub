"""CatalogService: titles, browse, search, tile resolution for arbitrary hub ids."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Select, case, func, literal, or_, select, true

from app.adapters.language.inferrer import lang_from_flag, lang_from_group, normalise_code
from app.domain.entities import CatalogItem, MediaKind
from app.domain.ports import DetailProvider, ImageAuthProvider, MetadataProvider
from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.models import Channel, Favorite, Profile, Progress, Source, Title, Watchlist
from app.services import ids
from app.services.availability import ATTRIBUTION, AvailabilityService
from app.services.context import absolute
from app.services.errors import NotFound
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
                (await s.execute(select(Title).where(Title.tmdb_id == tmdb_id, Title.kind.in_(kinds)))).scalars()
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
                now = nn.get((ch.source_id, ch.epg_id or ""), (None, None))[0]
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
                    media_type,
                    tmdb_id=tid,
                    imdb_id=meta.imdb_id,
                    title=meta.title,
                    year=meta.year,
                    sources=ctx.sources,
                    is_in_app=ctx.is_in_app,
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
    def _browse_query(
        self, kinds: tuple[str, ...], source_ids: list[str], source_id: str | None, genre: str | None
    ) -> Select[Any]:
        q = select(Title).where(Title.kind.in_(kinds), Title.source_id.in_(source_ids))
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
    def category_language(group: str | None) -> str:
        # Categories are a provider hint, not verified audio metadata. Never guess English
        # from a Latin-script title or treat missing metadata as English.
        return lang_from_group(group) or lang_from_flag(group) or "und"

    async def _filter_title_languages(self, query: Select[Any], languages: list[str]) -> Select[Any]:
        if not languages:
            return query
        async with self._db.session() as s:
            groups = list((await s.execute(query.with_only_columns(Title.group_name).distinct())).scalars())
        matching = [g for g in groups if g is not None and self.category_language(g) in languages]
        terms = [Title.group_name.in_(matching)]
        if "und" in languages:
            terms.append(Title.group_name.is_(None))
        return query.where(or_(*terms))

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
        query = await self._filter_title_languages(query, await self.preferred_languages(profile_id, language))
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    query.with_only_columns(Title.group_name, func.count(Title.id))
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
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    query.with_only_columns(Title.group_name, func.count(Title.id)).group_by(Title.group_name)
                )
            ).all()
        counts: dict[str, int] = {}
        for group, count in rows:
            code = self.category_language(group)
            counts[code] = counts.get(code, 0) + int(count)
        return [
            {"code": code, "name": self.sources.language.language_name(code), "count": count}
            for code, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ]

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
        query = await self._filter_title_languages(query, await self.preferred_languages(profile_id, language))
        orders: dict[str, list[Any]] = {
            "title": [Title.norm_title.asc()],
            "year": [Title.year.desc().nulls_last(), Title.norm_title],
            "rating": [Title.rating.desc().nulls_last(), Title.norm_title],
        }
        order = orders.get(sort or "added", [Title.added_at.desc().nulls_last(), Title.id.desc()])
        async with self._db.session() as s:
            total = (await s.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
            rows = list(
                (
                    await s.execute(query.order_by(*order, Title.id).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
                ).scalars()
            )
        tiles = ctx.merge(ctx.title_tile(r) for r in rows)
        return {"tiles": tiles, "page": page, "total": int(total), "has_more": page * PAGE_SIZE < int(total)}

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
        query = await self._filter_title_languages(query, await self.preferred_languages(profile_id, language))
        scoped = query.with_only_columns(Title.id, Title.genres).subquery()
        values = func.json_each(scoped.c.genres).table_valued("value", "type")
        name = func.lower(func.trim(values.c.value))
        statement = (
            select(name.label("name"), func.count(func.distinct(scoped.c.id)))
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
    ) -> dict[str, Any]:
        from app.services.channels import ChannelService

        ctx = await self.tile_context(profile_id)
        enabled = self._enabled(ctx)
        q = q.strip()
        limit = max(1, min(limit, 100))
        norm = ids.normalise_title(q)
        languages = await self.preferred_languages(profile_id, language)
        if source_id:
            enabled = [sid for sid in enabled if sid == source_id]
        want = kinds or {"movie", "show", "vod", "series", "episode", "channel"}
        lib_kinds = [k for k in ("movie", "show", "vod", "series", "episode") if k in want]
        rows: list[Title] = []
        if norm and lib_kinds:
            like = f"%{_escape_like(norm)}%"
            query = select(Title).where(
                Title.source_id.in_(enabled), Title.kind.in_(lib_kinds), Title.norm_title.like(like, escape="\\")
            )
            query = await self._filter_title_languages(query, languages)
            async with self._db.session() as s:
                rows = list(
                    (
                        await s.execute(
                            query.order_by(
                                (Title.norm_title == norm).desc(),
                                Title.norm_title.like(f"{_escape_like(norm)}%", escape="\\").desc(),
                                Title.added_at.desc().nulls_last(),
                                Title.id,
                            ).limit(limit * 3)
                        )
                    ).scalars()
                )
        # Keep exact phrase results first. Only inspect a bounded SQL-ranked candidate
        # set for omitted/reordered terms or one misspelled word, in the same scope.
        tokens = search_tokens(norm)
        anchor_words = anchors(tokens)
        if len(rows) < limit and anchor_words and lib_kinds:
            terms = [Title.norm_title.like(f"%{_escape_like(word)}%", escape="\\") for word in anchor_words]
            candidates_query = select(Title).where(
                Title.source_id.in_(enabled),
                Title.kind.in_(lib_kinds),
                or_(*terms),
            )
            candidates_query = await self._filter_title_languages(candidates_query, languages)
            score: ColumnElement[int] = literal(0)
            for term in terms:
                score = score + case((term, 1), else_=0)
            async with self._db.session() as session:
                candidates = list(
                    (
                        await session.execute(candidates_query.order_by(score.desc(), Title.id).limit(CANDIDATE_LIMIT))
                    ).scalars()
                )
            existing = {row.id for row in rows}
            ranked = [
                (rank, row)
                for row in candidates
                if row.id not in existing
                if (rank := token_match_rank(tokens, row.norm_title)) is not None
            ]
            rows.extend(row for _, row in sorted(ranked, key=lambda pair: (pair[0], pair[1].norm_title, pair[1].id)))
        local_tiles = ctx.merge(ctx.title_tile(r) for r in rows)
        combined = {tile["id"]: tile for tile in local_tiles}
        metadata_available = False
        popularity: dict[str, float] = {}
        note = "Metadata discovery is not configured; searching your sources only."
        if self.meta.enabled and ({"movie", "show", "vod", "series"} & want):
            try:
                metas = await self.meta.search(q, limit=40)
                metadata_available = True
                note = "Metadata coverage varies. Language and source filters limit availability, not discovery."
                metas = [
                    m
                    for m in metas
                    if (m.media_type == "movie" and {"movie", "vod"} & want)
                    or (m.media_type == "tv" and {"show", "series"} & want)
                ]
                by_id = {ids.tmdb_id(m.media_type, m.tmdb_id): m for m in metas}
                # Resolve IDs on the bounded local result page; translated provider
                # labels must not replace the metadata title/artwork.
                missing = [
                    (ids.tmdb_type_for_kind(r.kind), r.tmdb_id)
                    for r in rows
                    if r.tmdb_id and ids.tmdb_type_for_kind(r.kind) and hub_id_for_title(r) not in by_id
                ]
                missing = list(dict.fromkeys(missing))[:20]
                details = await asyncio.gather(
                    *(self.meta.details(kind, tid) for kind, tid in missing if kind is not None), return_exceptions=True
                )
                for detail in details:
                    if detail is not None and not isinstance(detail, BaseException):
                        by_id[ids.tmdb_id(detail.media_type, detail.tmdb_id)] = detail
                matched = await self.availability.library_matches_many(list(by_id.values()), candidates=rows)
                match_ids = {r.id for matches in matched.values() for r in matches if r.source_id in enabled}
                eligible_ids: set[int] = set()
                if match_ids:
                    eligible_query = await self._filter_title_languages(
                        select(Title).where(Title.id.in_(match_ids)), languages
                    )
                    async with self._db.session() as session:
                        eligible_ids = {r.id for r in (await session.execute(eligible_query)).scalars()}
                for hub, meta in by_id.items():
                    popularity[hub] = meta.popularity
                    eligible = [r for r in matched[hub] if r.id in eligible_ids]
                    badges = [
                        badge_for_source(ctx.sources[r.source_id], in_app=ctx.is_in_app(ctx.sources[r.source_id].type))
                        for r in eligible
                    ]
                    scoped_sources = {sid: src for sid, src in ctx.sources.items() if sid in enabled}
                    badges += await self.availability.handoff_badges(meta.media_type, meta.tmdb_id, scoped_sources)
                    for row in eligible:
                        combined.pop(hub_id_for_title(row), None)
                    combined[hub] = meta_tile(
                        meta, sort_badges(badges, ctx.sources), ctx.favorites, ctx.progress_pct(hub)
                    )
            except Exception:
                log.warning("Metadata discovery unavailable", exc_info=False)
                note = "Metadata discovery is temporarily unavailable; showing source matches."
                metadata_available = False

        def relevance(tile: dict[str, Any]) -> tuple[Any, ...]:
            title_norm = ids.normalise_title(tile["title"])
            token_rank = token_match_rank(tokens, title_norm) if tokens else None
            rank = (
                0
                if title_norm == norm
                else 1
                if title_norm.startswith(norm + " ")
                else 2
                if norm in title_norm
                else 3 + token_rank
                if token_rank is not None
                else 7
            )
            return (
                rank,
                0 if tile["id"].startswith("tmdb:") else 1,
                -popularity.get(tile["id"], 0),
                len(title_norm),
                title_norm,
                tile["id"],
            )

        ordered = sorted(combined.values(), key=relevance)[:limit]
        play_here = [tile for tile in ordered if any(b["playable_in_app"] for b in tile["sources"])]
        open_in_app = [
            tile for tile in ordered if tile["sources"] and not any(b["playable_in_app"] for b in tile["sources"])
        ]
        channels: list[dict[str, Any]] = []
        if "channel" in want:
            chsvc = ChannelService(self._db, self.sources)
            res = await chsvc.list_channels(profile_id, q=q, language=language, source_id=source_id, limit=limit)
            channels = res["channels"]
        return {
            "results": ordered,
            "metadata_available": metadata_available,
            "discovery_note": note,
            "play_here": play_here[:limit],
            "open_in_app": open_in_app[:limit],
            "channels": channels[:limit],
            "attribution": ATTRIBUTION,
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
        )
        query = await self._filter_title_languages(query, await self.preferred_languages(profile_id, None))
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
        q = select(Title).where(Title.kind.in_(kinds), Title.source_id.in_(enabled), Title.added_at.is_not(None))
        if source_id:
            q = q.where(Title.source_id == source_id)
        q = await self._filter_title_languages(q, languages or [])
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
