"""Per-profile state: favorites, watchlist, progress/history, home layout."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, func, select

from app.domain.entities import MediaKind
from app.domain.ports import ProgressReporter
from app.infra.db import Database
from app.infra.models import Favorite, Profile, Progress, Watchlist, utcnow
from app.services import ids
from app.services.errors import NotFound, ServiceError
from app.services.sources import SourceService
from app.services.tiles import row_to_item

log = logging.getLogger(__name__)

COMPLETE_RATIO = 0.95
HOME_ROWS: list[tuple[str, str]] = [
    ("continue_watching", "Continue Watching"),
    ("on_now", "On Now"),
    ("recently_added", "Recently Added"),
    ("watchlist", "Watchlist"),
    ("favorites", "Favorites"),
    ("trending", "Trending"),
]
KINDS = {k.value for k in MediaKind}


class FavoritesService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def ids(self, profile_id: str) -> list[str]:
        async with self._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(Favorite.ref_id)
                        .where(Favorite.profile_id == profile_id)
                        .order_by(Favorite.sort, Favorite.created_at)
                    )
                ).scalars()
            )

    async def put(self, profile_id: str, kind: str, ref_id: str, sort: int | None = None) -> None:
        if kind not in KINDS:
            raise ServiceError("invalid kind")
        try:
            ids.parse(ref_id)
        except ValueError as exc:
            raise ServiceError("invalid id") from exc
        async with self._db.session() as s:
            row = (
                await s.execute(select(Favorite).where(Favorite.profile_id == profile_id, Favorite.ref_id == ref_id))
            ).scalar_one_or_none()
            if row is None:
                if sort is None:
                    sort = (
                        int(
                            (
                                await s.execute(
                                    select(func.coalesce(func.max(Favorite.sort), 0)).where(
                                        Favorite.profile_id == profile_id
                                    )
                                )
                            ).scalar_one()
                        )
                        + 1
                    )
                s.add(Favorite(profile_id=profile_id, kind=kind, ref_id=ref_id, sort=sort))
            else:
                row.kind = kind
                if sort is not None:
                    row.sort = sort
            await s.commit()

    async def delete(self, profile_id: str, kind: str, ref_id: str) -> None:
        async with self._db.session() as s:
            await s.execute(
                delete(Favorite).where(
                    Favorite.profile_id == profile_id, Favorite.ref_id == ref_id, Favorite.kind == kind
                )
            )
            await s.commit()

    async def reorder(self, profile_id: str, items: list[dict[str, Any]]) -> None:
        async with self._db.session() as s:
            rows = {
                r.ref_id: r
                for r in (await s.execute(select(Favorite).where(Favorite.profile_id == profile_id))).scalars()
            }
            for i, it in enumerate(items):
                r = rows.get(str(it.get("id")))
                if r is not None:
                    r.sort = int(it["sort"]) if it.get("sort") is not None else i
            await s.commit()


class WatchlistService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def ids(self, profile_id: str) -> list[str]:
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

    async def put(self, profile_id: str, ref_id: str) -> None:
        try:
            ids.parse(ref_id)
        except ValueError as exc:
            raise ServiceError("invalid id") from exc
        async with self._db.session() as s:
            exists = (
                await s.execute(select(Watchlist).where(Watchlist.profile_id == profile_id, Watchlist.ref_id == ref_id))
            ).scalar_one_or_none()
            if exists is None:
                s.add(Watchlist(profile_id=profile_id, ref_id=ref_id))
                await s.commit()

    async def delete(self, profile_id: str, ref_id: str) -> None:
        async with self._db.session() as s:
            await s.execute(delete(Watchlist).where(Watchlist.profile_id == profile_id, Watchlist.ref_id == ref_id))
            await s.commit()


class ProgressService:
    def __init__(self, db: Database, sources: SourceService) -> None:
        self._db = db
        self._sources = sources

    async def get(self, profile_id: str, *ref_ids: str) -> Progress | None:
        async with self._db.session() as s:
            for rid in ref_ids:
                p = (
                    await s.execute(select(Progress).where(Progress.profile_id == profile_id, Progress.ref_id == rid))
                ).scalar_one_or_none()
                if p:
                    return p
        return None

    async def report(
        self,
        profile_id: str,
        ref_id: str,
        *,
        position_ms: int,
        duration_ms: int | None,
        state: str,
        source_id: str | None,
        forward_item: Any = None,
        aliases: list[str] | None = None,
    ) -> Progress:
        if state not in ("playing", "paused", "stopped"):
            raise ServiceError("invalid state")
        position_ms = max(0, int(position_ms))
        kind = None
        try:
            h = ids.parse(ref_id)
            kind = "channel" if h.scheme == "ch" else None
        except ValueError as exc:
            raise ServiceError("invalid id") from exc
        completed = False
        if duration_ms and duration_ms > 0:
            if state == "stopped" and position_ms >= duration_ms:
                completed = True  # explicit "watched" signal from the client
            elif position_ms >= duration_ms * COMPLETE_RATIO:
                completed = True
        async with self._db.session() as s:
            keys = [ref_id, *(aliases or [])]
            row = None
            for k in keys:
                row = (
                    await s.execute(select(Progress).where(Progress.profile_id == profile_id, Progress.ref_id == k))
                ).scalar_one_or_none()
                if row:
                    break
            if row is None:
                row = Progress(profile_id=profile_id, ref_id=ref_id)
                s.add(row)
            row.position_ms = 0 if completed else position_ms
            row.duration_ms = duration_ms or row.duration_ms
            row.completed = completed
            row.source_id = source_id or row.source_id
            row.kind = kind or row.kind
            row.updated_at = utcnow()
            await s.commit()
            await s.refresh(row)
        if forward_item is not None and source_id:
            await self._forward(source_id, forward_item, position_ms, duration_ms, state)
        return row

    async def _forward(self, source_id: str, item: Any, position_ms: int, duration_ms: int | None, state: str) -> None:
        try:
            _src, prov = await self._sources.provider_for(source_id)
            if isinstance(prov, ProgressReporter):
                await prov.report_progress(row_to_item(item), position_ms, duration_ms, state)
        except Exception as exc:  # never fail the client's report
            log.info("progress forward failed", extra={"source": source_id, "err": type(exc).__name__})

    async def history(self, profile_id: str, limit: int = 50) -> list[Progress]:
        async with self._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(Progress)
                        .where(Progress.profile_id == profile_id)
                        .order_by(Progress.updated_at.desc())
                        .limit(limit)
                    )
                ).scalars()
            )

    async def in_progress(self, profile_id: str, limit: int = 30) -> list[Progress]:
        async with self._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(Progress)
                        .where(
                            Progress.profile_id == profile_id,
                            Progress.completed.is_(False),
                            Progress.position_ms > 30_000,
                            Progress.kind.is_distinct_from("channel"),
                        )
                        .order_by(Progress.updated_at.desc())
                        .limit(limit)
                    )
                ).scalars()
            )


class HomeLayoutService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, profile_id: str, extra_row_ids: list[str] | None = None) -> list[dict[str, Any]]:
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
        if prof is None:
            raise NotFound("profile not found")
        stored = {r["id"]: r for r in (prof.prefs or {}).get("home_layout", []) if isinstance(r, dict) and "id" in r}
        all_ids = [rid for rid, _ in HOME_ROWS] + list(extra_row_ids or [])
        rows = []
        for i, rid in enumerate(all_ids):
            st = stored.get(rid, {})
            rows.append({"id": rid, "hidden": bool(st.get("hidden", False)), "sort": int(st.get("sort", (i + 1) * 10))})
        for rid, st in stored.items():
            if rid not in all_ids:
                rows.append({"id": rid, "hidden": bool(st.get("hidden", False)), "sort": int(st.get("sort", 999))})
        rows.sort(key=lambda r: (r["sort"], all_ids.index(r["id"]) if r["id"] in all_ids else 999))
        return rows

    async def put(self, profile_id: str, rows: list[dict[str, Any]]) -> None:
        clean = []
        for i, r in enumerate(rows):
            rid = str(r.get("id") or "").strip()
            if not rid:
                continue
            clean.append(
                {
                    "id": rid[:64],
                    "hidden": bool(r.get("hidden", False)),
                    "sort": int(r["sort"]) if r.get("sort") is not None else (i + 1) * 10,
                }
            )
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
            if prof is None:
                raise NotFound("profile not found")
            prefs = dict(prof.prefs or {})
            prefs["home_layout"] = clean
            prof.prefs = prefs
            await s.commit()
