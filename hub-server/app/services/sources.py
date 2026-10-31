"""Source management: CRUD, encrypted config, provider construction, catalog sink."""

from __future__ import annotations

import builtins
import hashlib
import logging
import secrets
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert

from app.domain.entities import (
    IPTV_SOURCE_TYPES,
    CatalogItem,
    ChannelInfo,
    SourceRef,
    SourceStatus,
    SourceType,
)
from app.domain.ports import LanguageInferrer, SourceProvider
from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.logging import secret_registry
from app.infra.models import Channel, ChannelGroup, Source, Title
from app.services.disclaimer import DisclaimerService
from app.services.errors import NotFound, PreconditionFailed, ServiceError
from app.services.ids import normalise_title
from app.services.registry import SourceRegistry, UnknownSourceType

log = logging.getLogger(__name__)

LAZY_GEN = -1  # rows fetched lazily (seasons/episodes, details) — never swept by sync
CHANNEL_OFFSET_STEP = 1000


def new_id() -> str:
    return secrets.token_hex(8)


def group_id(source_id: str, name: str) -> str:
    return hashlib.sha1(f"{source_id}|{name}".encode(), usedforsecurity=False).hexdigest()[:20]


class SourceService:
    def __init__(
        self,
        db: Database,
        crypto: Crypto,
        registry: SourceRegistry,
        disclaimer: DisclaimerService,
        language: LanguageInferrer,
    ) -> None:
        self._db = db
        self._crypto = crypto
        self.registry = registry
        self._disclaimer = disclaimer
        self.language = language
        self._providers: dict[str, tuple[Any, SourceProvider]] = {}

    # ------------------------------------------------------------------ config
    def decrypt_config(self, row: Source) -> dict[str, Any]:
        cfg = self._crypto.try_decrypt_json(row.config_enc)
        return cfg if isinstance(cfg, dict) else {}

    def ref(self, row: Source) -> SourceRef:
        return SourceRef(
            id=row.id, type=SourceType(row.type), name=row.name, region=row.region, service_key=row.service_key
        )

    def provider(self, row: Source) -> SourceProvider:
        cache_key = (row.config_enc, repr(sorted((row.hints or {}).items())), row.name, row.region)
        hit = self._providers.get(row.id)
        if hit is not None and hit[0] == cache_key:
            return hit[1]
        cfg = self.decrypt_config(row)
        cfg["_hints"] = dict(row.hints or {})
        prov = self.registry.create(self.ref(row), cfg)
        secret_registry.add(*prov.secrets())
        self._providers[row.id] = (cache_key, prov)
        return prov

    async def provider_for(self, source_id: str) -> tuple[Source, SourceProvider]:
        row = await self.get(source_id)
        return row, self.provider(row)

    async def rows_by_id(self) -> dict[str, Source]:
        return {r.id: r for r in await self.list()}

    def proxy_policy(self, row: Source) -> str:
        cfg = self.decrypt_config(row)
        return str(cfg.get("proxy") or "auto")

    def max_connections(self, row: Source) -> int | None:
        cfg = self.decrypt_config(row)
        v = cfg.get("max_connections") or (row.hints or {}).get("max_connections")
        try:
            return int(v) if v else None
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ queries
    async def list(self) -> builtins.list[Source]:
        async with self._db.session() as s:
            return list((await s.execute(select(Source).order_by(Source.priority, Source.created_at))).scalars())

    async def get(self, source_id: str) -> Source:
        async with self._db.session() as s:
            row = await s.get(Source, source_id)
        if row is None:
            raise NotFound("source not found")
        return row

    async def to_api(self, row: Source) -> dict[str, Any]:
        try:
            summary = self.provider(row).config_summary()
        except Exception:
            summary = {}
        return {
            "id": row.id,
            "type": row.type,
            "name": row.name,
            "enabled": row.enabled,
            "priority": row.priority,
            "region": row.region,
            "status": row.status,
            "last_sync": row.last_sync,
            "counts": dict(row.counts or {}),
            "service_key": row.service_key,
            "config_summary": summary,
        }

    # ------------------------------------------------------------------ mutations
    async def create(
        self,
        *,
        profile_id: str,
        type_: str,
        name: str,
        config: dict[str, Any],
        region: str | None = None,
        service_key: str | None = None,
        enabled: bool = True,
        priority: int | None = None,
    ) -> Source:
        try:
            st = SourceType(type_)
            self.registry.info(st)
        except (ValueError, UnknownSourceType) as exc:
            raise ServiceError(f"unsupported source type {type_}") from exc
        if self.registry.requires_disclaimer(st) and not await self._disclaimer.has_accepted_current(profile_id):
            raise PreconditionFailed("Accept the legal disclaimer before adding IPTV/M3U sources")
        if st == SourceType.STREAMING_SERVICE and not service_key:
            raise ServiceError("service_key is required for streaming_service sources")
        self._validate(st, config)
        hints: dict[str, Any] = {"sync_gen": 0}
        async with self._db.session() as s:
            if st in IPTV_SOURCE_TYPES:
                rows = (
                    await s.execute(select(Source).where(Source.type.in_([t.value for t in IPTV_SOURCE_TYPES])))
                ).scalars()
                offsets = [int((r.hints or {}).get("channel_offset") or 0) for r in rows]
                hints["channel_offset"] = (max(offsets) + CHANNEL_OFFSET_STEP) if offsets else 0
            if priority is None:
                priority = int((await s.execute(select(func.count(Source.id)))).scalar_one()) * 10 + 10
            row = Source(
                id=new_id(),
                type=st.value,
                name=name.strip() or st.value,
                enabled=enabled,
                priority=priority,
                region=(region or "US").upper()[:2],
                status=SourceStatus.OK.value if st == SourceType.STREAMING_SERVICE else SourceStatus.SYNCING.value,
                service_key=service_key,
                config_enc=self._crypto.encrypt_json(config),
                hints=hints,
                counts={},
            )
            s.add(row)
            await s.commit()
        secret_registry.add(*self.provider(row).secrets())
        return row

    def _validate(self, st: SourceType, cfg: dict[str, Any]) -> None:
        if st == SourceType.XTREAM:
            missing = [k for k in ("server_url", "username", "password") if not cfg.get(k)]
            if missing:
                raise ServiceError(f"missing xtream config: {', '.join(missing)}")
            if cfg.get("output") and cfg["output"] not in ("ts", "m3u8"):
                raise ServiceError("output must be ts or m3u8")
        elif st == SourceType.M3U:
            if not cfg.get("url") and not cfg.get("file"):
                raise ServiceError("m3u config needs url or file")
        elif st == SourceType.JELLYFIN:
            if not cfg.get("server_url"):
                raise ServiceError("jellyfin config needs server_url")
        elif st == SourceType.PLEX:
            if not cfg.get("server_url") and not cfg.get("token"):
                raise ServiceError("plex sources are created via /sources/plex/pin")

    async def update(self, source_id: str, patch: dict[str, Any]) -> Source:
        async with self._db.session() as s:
            row = await s.get(Source, source_id)
            if row is None:
                raise NotFound("source not found")
            for key in ("name", "enabled", "priority", "service_key"):
                if key in patch and patch[key] is not None:
                    setattr(row, key, patch[key])
            if patch.get("region"):
                row.region = str(patch["region"]).upper()[:2]
            if isinstance(patch.get("config"), dict) and patch["config"]:
                cfg = self.decrypt_config(row)
                for k, v in patch["config"].items():
                    # secrets omitted by clients (they never receive them) are kept
                    if v is None and k in ("password", "token", "api_key"):
                        continue
                    cfg[k] = v
                self._validate(SourceType(row.type), cfg)
                row.config_enc = self._crypto.encrypt_json(cfg)
            await s.commit()
            await s.refresh(row)
        return row

    async def update_config(self, source_id: str, updates: dict[str, Any]) -> None:
        async with self._db.session() as s:
            row = await s.get(Source, source_id)
            if row is None:
                return
            cfg = self.decrypt_config(row)
            for k, v in updates.items():
                if v is None:
                    cfg.pop(k, None)
                else:
                    cfg[k] = v
            row.config_enc = self._crypto.encrypt_json(cfg)
            await s.commit()

    async def set_status(
        self,
        source_id: str,
        status: SourceStatus,
        *,
        error: str | None = None,
        last_sync: datetime | None = None,
        counts: dict[str, int] | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status.value, "last_error": error}
        if last_sync is not None:
            values["last_sync"] = last_sync
        if counts is not None:
            values["counts"] = counts
        async with self._db.session() as s:
            await s.execute(update(Source).where(Source.id == source_id).values(**values))
            await s.commit()

    async def delete(self, source_id: str) -> None:
        async with self._db.session() as s:
            row = await s.get(Source, source_id)
            if row is None:
                raise NotFound("source not found")
            # explicit deletes (SQLite FK cascade is on, but be explicit for large tables)
            await s.execute(delete(Channel).where(Channel.source_id == source_id))
            await s.execute(delete(ChannelGroup).where(ChannelGroup.source_id == source_id))
            await s.execute(delete(Title).where(Title.source_id == source_id))
            await s.delete(row)
            await s.commit()

    async def create_drafts(self, profile_id: str, drafts: Iterable[Any]) -> builtins.list[Source]:
        out: list[Source] = []
        existing = await self.list()
        for d in drafts:
            machine = d.config.get("machine_id")
            dup = next(
                (
                    r
                    for r in existing
                    if r.type == d.type.value and machine and self.decrypt_config(r).get("machine_id") == machine
                ),
                None,
            )
            if dup is not None:
                await self.update_config(dup.id, d.config)
                await self.set_status(dup.id, SourceStatus.SYNCING)
                out.append(await self.get(dup.id))
                continue
            out.append(await self.create(profile_id=profile_id, type_=d.type.value, name=d.name, config=d.config))
        return out


class DbCatalogSink:
    """CatalogSink implementation used during a sync run."""

    def __init__(self, db: Database, source: Source, gen: int, language: LanguageInferrer) -> None:
        self._db = db
        self._source = source
        self._gen = gen
        self._lang = language
        self._offset = int((source.hints or {}).get("channel_offset") or 0)
        self._groups: dict[str, str] = {}
        self._group_overrides: dict[str, str | None] = {}
        self._group_sort = 0
        self.hints: dict[str, Any] = {}
        self.channel_count = 0
        self.item_counts: dict[str, int] = {}
        self._loaded = False

    async def _load_groups(self) -> None:
        if self._loaded:
            return
        async with self._db.session() as s:
            rows = (await s.execute(select(ChannelGroup).where(ChannelGroup.source_id == self._source.id))).scalars()
            for g in rows:
                self._group_overrides[g.name] = g.language_override
                self._group_sort = max(self._group_sort, g.sort)
        self._loaded = True

    async def add_channels(self, channels: Iterable[ChannelInfo]) -> None:
        await self._load_groups()
        chans = list(channels)
        if not chans:
            return
        sid = self._source.id
        group_rows: list[dict[str, Any]] = []
        chan_rows: list[dict[str, Any]] = []
        for ch in chans:
            gname = ch.group or "Uncategorized"
            gid = self._groups.get(gname)
            if gid is None:
                gid = group_id(sid, gname)
                self._groups[gname] = gid
                self._group_sort += 1
                group_rows.append(
                    {"id": gid, "source_id": sid, "name": gname, "sort": self._group_sort, "sync_gen": self._gen}
                )
            self.channel_count += 1
            guess = self._lang.infer(ch)
            index = int(ch.extra.get("index") or self.channel_count)
            number_auto = self._offset + (ch.number if ch.number else index)
            data = {
                "headers": ch.headers,
                "catchup_type": ch.catchup_type,
                "catchup_source": ch.catchup_source,
                "tvg_name": ch.tvg_name,
                "tvg_language": ch.tvg_language,
                "inferred_lang": guess.code,
                "inferred_src": guess.source.value if guess.source else None,
                **{k: v for k, v in ch.extra.items() if k not in ("index",)},
            }
            chan_rows.append(
                {
                    "source_id": sid,
                    "ext_id": ch.ext_id,
                    "name": ch.name,
                    "norm_name": normalise_title(ch.name),
                    "logo": ch.logo,
                    "group_id": gid,
                    "group_name": gname,
                    "tvg_id": ch.tvg_id,
                    "epg_id": ch.tvg_id,
                    "country": (ch.tvg_country or None),
                    "number_auto": number_auto,
                    "number": number_auto,
                    "language": guess.code,
                    "language_source": guess.source.value if guess.source else None,
                    "catchup_days": ch.catchup_days,
                    "stream_url": ch.stream_url,
                    "data": data,
                    "sync_gen": self._gen,
                }
            )
        async with self._db.session() as s:
            if group_rows:
                gstmt = insert(ChannelGroup).values(group_rows)
                gstmt = gstmt.on_conflict_do_update(
                    index_elements=[ChannelGroup.id], set_={"sync_gen": gstmt.excluded.sync_gen}
                )
                await s.execute(gstmt)
            stmt = insert(Channel).values(chan_rows)
            ex = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=[Channel.source_id, Channel.ext_id],
                set_={
                    "name": ex.name,
                    "norm_name": ex.norm_name,
                    "logo": ex.logo,
                    "group_id": ex.group_id,
                    "group_name": ex.group_name,
                    "tvg_id": ex.tvg_id,
                    "epg_id": ex.epg_id,
                    "country": ex.country,
                    "number_auto": ex.number_auto,
                    "language": ex.language,
                    "language_source": ex.language_source,
                    "catchup_days": ex.catchup_days,
                    "stream_url": ex.stream_url,
                    "data": ex.data,
                    "sync_gen": ex.sync_gen,
                },
            )
            await s.execute(stmt)
            await s.commit()

    async def add_items(self, items: Iterable[CatalogItem]) -> None:
        rows = []
        for it in items:
            self.item_counts[it.kind.value] = self.item_counts.get(it.kind.value, 0) + 1
            rows.append(item_row(self._source.id, it, self._gen))
        if rows:
            await upsert_titles(self._db, rows)

    async def set_config_hint(self, key: str, value: Any) -> None:
        self.hints[key] = value

    async def finalize(self) -> dict[str, int]:
        """Sweep rows not seen in this run; apply overrides; return counts."""
        sid = self._source.id
        async with self._db.session() as s:
            await s.execute(delete(Channel).where(Channel.source_id == sid, Channel.sync_gen != self._gen))
            await s.execute(
                delete(ChannelGroup).where(ChannelGroup.source_id == sid, ChannelGroup.sync_gen != self._gen)
            )
            await s.execute(
                delete(Title).where(Title.source_id == sid, Title.sync_gen != self._gen, Title.sync_gen != LAZY_GEN)
            )
            await s.commit()
        await apply_overrides(self._db, sid)
        counts = {"channels": self.channel_count, **self.item_counts}
        return counts


def item_row(source_id: str, it: CatalogItem, gen: int) -> dict[str, Any]:
    extra = dict(it.extra)
    if it.stream_url:
        extra["stream_url"] = it.stream_url
    if it.headers:
        extra["headers"] = it.headers
    return {
        "source_id": source_id,
        "ext_id": it.ext_id,
        "kind": it.kind.value,
        "title": it.title[:512],
        "norm_title": normalise_title(it.title),
        "year": it.year,
        "overview": it.overview,
        "poster": it.poster,
        "backdrop": it.backdrop,
        "rating": it.rating,
        "genres": it.genres,
        "tmdb_id": it.tmdb_id,
        "imdb_id": it.imdb_id,
        "group_name": it.group,
        "parent_ext_id": it.parent_ext_id,
        "season_number": it.season_number,
        "episode_number": it.episode_number,
        "duration_ms": it.duration_ms,
        "added_at": it.added_at,
        "extra": extra,
        "sync_gen": gen,
    }


async def upsert_titles(db: Database, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    async with db.session() as s:
        for i in range(0, len(rows), 400):
            chunk = rows[i : i + 400]
            stmt = insert(Title).values(chunk)
            ex = stmt.excluded
            cols = [c for c in chunk[0] if c not in ("source_id", "ext_id")]
            set_ = {c: getattr(ex, c) for c in cols}
            if chunk[0]["sync_gen"] == LAZY_GEN:
                # lazy refresh must not demote a synced row to LAZY or drop sync-only data
                set_.pop("sync_gen", None)
                set_.pop("added_at", None)
            stmt = stmt.on_conflict_do_update(index_elements=[Title.source_id, Title.ext_id], set_=set_)
            await s.execute(stmt)
        await s.commit()


async def apply_overrides(db: Database, source_id: str | None = None) -> None:
    """Recompute effective number/language/epg for channels from overrides."""
    async with db.session() as s:
        q = select(ChannelGroup).where(ChannelGroup.language_override.is_not(None))
        if source_id:
            q = q.where(ChannelGroup.source_id == source_id)
        groups = list((await s.execute(q)).scalars())
        cond = [Channel.source_id == source_id] if source_id else []
        # effective number
        await s.execute(
            update(Channel).where(*cond).values(number=func.coalesce(Channel.number_override, Channel.number_auto))
        )
        # epg override
        await s.execute(
            update(Channel).where(*cond, Channel.epg_override.is_not(None)).values(epg_id=Channel.epg_override)
        )
        # group language overrides (unless the channel has its own override)
        for g in groups:
            await s.execute(
                update(Channel)
                .where(Channel.group_id == g.id, Channel.language_override.is_(None))
                .values(language=g.language_override, language_source="user")
            )
        # channel language overrides
        await s.execute(
            update(Channel)
            .where(*cond, Channel.language_override.is_not(None))
            .values(language=Channel.language_override, language_source="user")
        )
        await s.commit()
