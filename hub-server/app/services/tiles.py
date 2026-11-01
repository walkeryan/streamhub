"""Mapping DB rows → API Tile / SourceBadge dicts, and row ↔ domain entity conversions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.domain.entities import CatalogItem, ChannelInfo, MediaKind, SourceType
from app.infra.models import Channel, Progress, Source, Title
from app.services import ids

ImageFn = Callable[[Source | None, str | None, str | None], str | None]
TOP_LEVEL_KINDS = {"movie", "show", "vod", "series"}
LIBRARY_TYPES = {SourceType.PLEX.value, SourceType.JELLYFIN.value}
IPTV_TYPES = {SourceType.XTREAM.value, SourceType.M3U.value}
HANDOFF_KIND_ORDER = {"flatrate": 0, "free": 1, "ads": 2, "rent": 3, "buy": 4}


def row_to_item(row: Title) -> CatalogItem:
    extra = dict(row.extra or {})
    return CatalogItem(
        ext_id=row.ext_id,
        kind=MediaKind(row.kind),
        title=row.title,
        year=row.year,
        overview=row.overview,
        poster=row.poster,
        backdrop=row.backdrop,
        rating=row.rating,
        genres=list(row.genres or []),
        tmdb_id=row.tmdb_id,
        imdb_id=row.imdb_id,
        group=row.group_name,
        parent_ext_id=row.parent_ext_id,
        season_number=row.season_number,
        episode_number=row.episode_number,
        duration_ms=row.duration_ms,
        added_at=row.added_at,
        stream_url=extra.pop("stream_url", None),
        headers=dict(extra.pop("headers", {}) or {}),
        extra=extra,
    )


def row_to_channel(row: Channel) -> ChannelInfo:
    data = dict(row.data or {})
    return ChannelInfo(
        ext_id=row.ext_id,
        name=row.name,
        stream_url=row.stream_url,
        logo=row.logo,
        group=row.group_name,
        tvg_id=row.tvg_id,
        tvg_name=data.get("tvg_name"),
        tvg_language=data.get("tvg_language"),
        tvg_country=row.country,
        number=row.number,
        catchup_days=row.catchup_days,
        catchup_type=data.get("catchup_type"),
        catchup_source=data.get("catchup_source"),
        headers=dict(data.get("headers") or {}),
        extra={k: v for k, v in data.items() if k not in ("headers",)},
    )


def hub_id_for_title(row: Title) -> str:
    if row.tmdb_id and row.kind in TOP_LEVEL_KINDS:
        return ids.tmdb_id(ids.tmdb_type_for_kind(row.kind) or "movie", row.tmdb_id)
    return ids.lib_id(row.source_id, row.ext_id)


def badge_for_source(src: Source, *, in_app: bool, kind: str | None = None) -> dict[str, Any]:
    if kind is None:
        kind = "library" if src.type in LIBRARY_TYPES else "vod" if src.type in IPTV_TYPES else "flatrate"
    return {
        "source_id": src.id,
        "source_type": src.type,
        "name": src.name,
        "playable_in_app": in_app,
        "kind": kind,
        "quality": None,
    }


def sort_badges(badges: list[dict[str, Any]], sources: dict[str, Source]) -> list[dict[str, Any]]:
    def key(b: dict[str, Any]) -> tuple[int, int, int]:
        src = sources.get(b["source_id"])
        return (
            0 if b["playable_in_app"] else 1,
            HANDOFF_KIND_ORDER.get(str(b.get("kind")), 0),
            src.priority if src else 999,
        )

    seen: set[str] = set()
    out = []
    for b in sorted(badges, key=key):
        if b["source_id"] in seen:
            continue
        seen.add(b["source_id"])
        out.append(b)
    return out


def subtitle_for(row: Title) -> str | None:
    if row.kind == "episode":
        s = f"S{row.season_number or 0:02d}E{row.episode_number or 0:02d}"
        gp = (row.extra or {}).get("grandparent_title") or (row.extra or {}).get("series_name")
        return f"{gp} · {s}" if gp else s
    return None


@dataclass
class TileContext:
    sources: dict[str, Source]
    image: ImageFn
    is_in_app: Callable[[str], bool]
    favorites: set[str] = field(default_factory=set)
    progress: dict[str, Progress] = field(default_factory=dict)

    def progress_pct(self, *keys: str) -> float | None:
        for k in keys:
            p = self.progress.get(k)
            if p and p.duration_ms and not p.completed and p.position_ms > 0:
                return round(min(100.0, 100.0 * p.position_ms / p.duration_ms), 1)
            if p and p.completed:
                return 100.0
        return None

    def title_tile(self, row: Title) -> dict[str, Any]:
        src = self.sources.get(row.source_id)
        hub = hub_id_for_title(row)
        lib = ids.lib_id(row.source_id, row.ext_id)
        kind = row.kind
        if hub.startswith("tmdb:"):
            kind = "movie" if ids.tmdb_type_for_kind(row.kind) == "movie" else "show"
        return {
            "id": hub,
            "kind": kind,
            "title": ids.clean_display_title(row.title) if ids.tmdb_type_for_kind(row.kind) else row.title,
            "subtitle": subtitle_for(row),
            "year": row.year,
            "poster": self.image(src, row.poster, lib),
            "backdrop": self.image(src, row.backdrop, lib),
            "logo": None,
            "rating": row.rating,
            "progress_pct": self.progress_pct(hub, lib),
            "is_favorite": hub in self.favorites or lib in self.favorites,
            "sources": [badge_for_source(src, in_app=self.is_in_app(src.type))] if src else [],
            "now_playing": None,
        }

    def merge(self, tiles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for t in tiles:
            cur = out.get(t["id"])
            if cur is None:
                out[t["id"]] = t
                continue
            cur["sources"] = cur["sources"] + t["sources"]
            for k in ("poster", "backdrop", "year", "rating", "logo", "progress_pct"):
                if not cur.get(k) and t.get(k):
                    cur[k] = t[k]
        for t in out.values():
            t["sources"] = sort_badges(t["sources"], self.sources)
        return list(out.values())

    def channel_tile(self, ch: Channel, now: dict[str, Any] | None = None) -> dict[str, Any]:
        src = self.sources.get(ch.source_id)
        cid = ids.channel_id(ch.source_id, ch.ext_id)
        return {
            "id": cid,
            "kind": "channel",
            "title": ch.name,
            "subtitle": f"{ch.number}" if ch.number else None,
            "year": None,
            "poster": ch.logo,
            "backdrop": None,
            "logo": ch.logo,
            "rating": None,
            "progress_pct": None,
            "is_favorite": cid in self.favorites,
            "sources": [badge_for_source(src, in_app=True, kind="live")] if src else [],
            "now_playing": now,
        }


def meta_tile(meta: Any, badges: list[dict[str, Any]], favorites: set[str], pct: float | None) -> dict[str, Any]:
    hub = ids.tmdb_id(meta.media_type, meta.tmdb_id)
    return {
        "id": hub,
        "kind": "movie" if meta.media_type == "movie" else "show",
        "title": meta.title,
        "subtitle": None,
        "year": meta.year,
        "poster": meta.poster,
        "backdrop": meta.backdrop,
        "logo": meta.logo,
        "rating": meta.rating,
        "progress_pct": pct,
        "is_favorite": hub in favorites,
        "sources": badges,
        "now_playing": None,
    }
