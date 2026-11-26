"""Per-profile state: favorites, watchlist, progress/history, home layout."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.sqlite import insert

from app.domain.entities import MediaKind
from app.domain.ports import ProgressReporter, WatchStateProvider
from app.infra.db import Database
from app.infra.models import Favorite, PlaybackPreference, Profile, Progress, Title, User, Watchlist, utcnow
from app.services import ids
from app.services.adult import adult_included, require_adult
from app.services.errors import NotFound, PayloadTooLarge, ServiceError
from app.services.listing_cache import listing_cached
from app.services.sources import SourceService
from app.services.tiles import hub_id_for_title, row_to_item

log = logging.getLogger(__name__)

COMPLETE_RATIO = 0.95
# The library account's state is asked for at most this often per profile and title, and never
# for longer than this: a slow library server must not hold up a title page or playback.
ACCOUNT_STATE_EVERY_S = 10.0
ACCOUNT_STATE_TIMEOUT_S = 5.0
HOME_ROWS: list[tuple[str, str]] = [
    ("continue_watching", "Continue Watching"),
    ("on_now", "On Now"),
    ("recommendations", "Suggestions from your library"),
    ("seasonal_halloween", "Halloween · Seasonal movies"),
    ("recently_added", "Recently Added"),
    ("watchlist", "Watchlist"),
    ("favorites", "Favorites"),
    ("trending", "Trending"),
    ("ready_to_watch", "Ready to watch"),
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
        self._scan_after: OrderedDict[str, str] = OrderedDict()

    async def availability_candidates(self, profile_id: str, saved: list[str], limit: int) -> list[str]:
        """Recheck the visible arrivals and walk older watchlist entries in bounded windows."""
        if not saved:
            return []
        last = self._scan_after.get(profile_id)
        start = (saved.index(last) + 1) % len(saved) if last in saved else 0
        window = (saved[start:] + saved[:start])[:limit]
        self._scan_after[profile_id] = window[-1]
        self._scan_after.move_to_end(profile_id)
        if len(self._scan_after) > 256:
            self._scan_after.popitem(last=False)
        async with self._db.session() as s:
            arrivals = list(
                await s.scalars(
                    select(Watchlist.ref_id)
                    .where(
                        Watchlist.profile_id == profile_id, Watchlist.playable_since >= utcnow() - timedelta(days=30)
                    )
                    .order_by(Watchlist.playable_since.desc(), Watchlist.id)
                    .limit(limit)
                )
            )
        return list(dict.fromkeys([*arrivals, *window]))

    async def cached_audio(self, profile_id: str, ref_id: str, load: Callable[[], Awaitable[str | None]]) -> str | None:
        async def permitted() -> str | None:
            if not adult_included():
                try:
                    await require_adult(self._db, None, ref_id)
                except ServiceError as error:
                    if error.status != 403:
                        raise
                    return None
            return await load()

        return await listing_cached(self._db, profile_id, "watchlist-availability", (ref_id,), permitted, ttl=60)

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

    async def put(self, profile_id: str, ref_id: str, playable: bool | None = None) -> None:
        try:
            ids.parse(ref_id)
        except ValueError as exc:
            raise ServiceError("invalid id") from exc
        async with self._db.session() as s:
            exists = (
                await s.execute(select(Watchlist).where(Watchlist.profile_id == profile_id, Watchlist.ref_id == ref_id))
            ).scalar_one_or_none()
            if exists is None:
                s.add(Watchlist(profile_id=profile_id, ref_id=ref_id, last_playable=playable))
                await s.commit()

    async def newly_available(self, profile_id: str, availability: dict[str, bool]) -> list[str]:
        now = utcnow()
        cutoff = now - timedelta(days=30)
        async with self._db.session() as s:
            rows = list(
                await s.scalars(
                    select(Watchlist).where(Watchlist.profile_id == profile_id, Watchlist.ref_id.in_(availability))
                )
            )
            for row in rows:
                available = availability[row.ref_id]
                if available == row.last_playable:
                    continue
                if row.last_playable is False and available:
                    row.playable_since = now
                elif not available:
                    row.playable_since = None
                row.last_playable = available
            ready = sorted(
                (
                    row
                    for row in rows
                    if availability.get(row.ref_id) and row.playable_since and row.playable_since >= cutoff
                ),
                key=lambda row: row.playable_since or cutoff,
                reverse=True,
            )
            ids = [row.ref_id for row in ready]
            if s.dirty:
                await s.commit()
            return ids

    async def delete(self, profile_id: str, ref_id: str) -> None:
        async with self._db.session() as s:
            await s.execute(delete(Watchlist).where(Watchlist.profile_id == profile_id, Watchlist.ref_id == ref_id))
            await s.commit()


class ProgressService:
    def __init__(self, db: Database, sources: SourceService) -> None:
        self._db = db
        self._sources = sources
        self._merged: dict[tuple[str, str, str], float] = {}

    # ------------------------------------------------------------------ the library account's own state
    async def account_linked(self, profile_id: str) -> bool:
        """Is this the user the library server's account belongs to (``user.plex_account_linked``)?"""
        async with self._db.session() as s:
            linked = await s.execute(
                select(User.plex_account_linked)
                .join(Profile, Profile.user_id == User.id)
                .where(Profile.id == profile_id)
            )
            return bool(linked.scalar_one_or_none())

    async def merge_account_state(self, profile_id: str, rows: list[Title]) -> None:
        """Take over the library account's resume points where they are newer than this profile's.

        A server such as Plex keeps one watch state, its account's. For the user linked to that
        account both sides are the same person's history: whichever was touched last wins, and a
        title the account watched to the end is completed here. Nobody else's request reads the
        account's state. Failures are logged and never reach the caller.
        """
        for row in rows:
            try:
                _src, prov = await self._sources.provider_for(row.source_id)
                if not isinstance(prov, WatchStateProvider) or not await self.account_linked(profile_id):
                    continue
                key, now = (profile_id, row.source_id, row.ext_id), time.monotonic()
                if now - self._merged.get(key, -ACCOUNT_STATE_EVERY_S) < ACCOUNT_STATE_EVERY_S:
                    continue
                if len(self._merged) > 2048:
                    self._merged.clear()
                self._merged[key] = now
                states = await asyncio.wait_for(prov.watch_states(row_to_item(row)), ACCOUNT_STATE_TIMEOUT_S)
                await self._take_over(profile_id, row, states)
            except Exception as exc:
                log.info("account watch state not merged", extra={"source": row.source_id, "err": type(exc).__name__})

    async def _take_over(self, profile_id: str, row: Title, states: dict[str, Any]) -> None:
        async with self._db.session() as s:
            for ext_id, state in states.items():
                lib = ids.lib_id(row.source_id, ext_id)
                # The title itself may be known by its merged id; its episodes never are.
                keys = [hub_id_for_title(row), lib] if ext_id == row.ext_id else [lib]
                found = (
                    await s.execute(
                        select(Progress).where(Progress.profile_id == profile_id, Progress.ref_id.in_(keys))
                    )
                ).scalars()
                local = min(found, key=lambda p: keys.index(p.ref_id), default=None)
                if local is not None and local.updated_at >= state.viewed_at:
                    continue  # this profile's own state is the newer one
                if local is None:
                    if not state.watched and state.position_ms <= 0:
                        continue
                    local = Progress(profile_id=profile_id, ref_id=keys[0])
                    s.add(local)
                local.completed = bool(state.watched)
                local.position_ms = 0 if state.watched else max(0, int(state.position_ms))
                local.duration_ms = state.duration_ms or local.duration_ms
                local.source_id = row.source_id
                local.updated_at = state.viewed_at
            await s.commit()

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
        watched: bool | None = None,
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
        if watched is not None:
            completed = watched
            position_ms = (duration_ms or 0) if watched else 0
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
        if forward_item is not None and source_id and (not watched or duration_ms):
            await self._forward(profile_id, source_id, forward_item, position_ms, duration_ms, state)
        return row

    async def _forward(
        self, profile_id: str, source_id: str, item: Any, position_ms: int, duration_ms: int | None, state: str
    ) -> None:
        try:
            _src, prov = await self._sources.provider_for(source_id)
            if isinstance(prov, ProgressReporter):
                # A server whose watch state is one account's hears only from the user linked to
                # that account; anyone else's viewing would be written into that person's history.
                if isinstance(prov, WatchStateProvider) and not await self.account_linked(profile_id):
                    return
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

    async def source_preference(self, profile_id: str, ref_id: str) -> PlaybackPreference | None:
        async with self._db.session() as s:
            return await s.get(PlaybackPreference, (profile_id, ref_id))

    async def save_source_preference(
        self, profile_id: str, ref_id: str, variant_id: str | None, audio: dict[str, Any]
    ) -> None:
        async with self._db.session() as s:
            if variant_id is None:
                await s.execute(
                    delete(PlaybackPreference).where(
                        PlaybackPreference.profile_id == profile_id, PlaybackPreference.ref_id == ref_id
                    )
                )
            else:
                stmt = insert(PlaybackPreference).values(
                    profile_id=profile_id, ref_id=ref_id, variant_id=variant_id, audio=audio
                )
                await s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["profile_id", "ref_id"], set_={"variant_id": variant_id, "audio": audio}
                    )
                )
            await s.commit()

    async def hide_from_continue(self, profile_id: str, ref_id: str, hidden: bool) -> None:
        async with self._db.session() as s:
            row = (
                await s.execute(select(Progress).where(Progress.profile_id == profile_id, Progress.ref_id == ref_id))
            ).scalar_one_or_none()
            if row is None:
                raise NotFound("No saved position for this title.")
            row.hidden_from_continue = hidden
            await s.commit()

    async def in_progress(self, profile_id: str, limit: int = 30) -> list[Progress]:
        async with self._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(Progress)
                        .where(
                            Progress.profile_id == profile_id,
                            Progress.completed.is_(False),
                            Progress.hidden_from_continue.is_(False),
                            Progress.position_ms > 30_000,
                            Progress.kind.is_distinct_from("channel"),
                        )
                        .order_by(Progress.updated_at.desc())
                        .limit(limit)
                    )
                ).scalars()
            )


MAX_PREFS_BYTES = 16 * 1024
CLIENT_PREFS_KEY = "client_prefs"


class ClientPrefsService:
    """Free-form per-profile client prefs (recent searches, reduced motion…), max 16 KB."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, profile_id: str) -> dict[str, Any]:
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
        if prof is None:
            raise NotFound("profile not found")
        v = (prof.prefs or {}).get(CLIENT_PREFS_KEY)
        return dict(v) if isinstance(v, dict) else {}

    async def put(
        self, profile_id: str, data: dict[str, Any], *, merge: bool = False, only_missing: bool = False
    ) -> dict[str, Any]:
        async with self._db.session() as s:
            # Serialize read-modify-write across devices sharing the profile.
            await s.execute(text("BEGIN IMMEDIATE"))
            prof = await s.get(Profile, profile_id)
            if prof is None:
                raise NotFound("profile not found")
            prefs = dict(prof.prefs or {})
            if merge:
                stored = prefs.get(CLIENT_PREFS_KEY)
                previous = stored if isinstance(stored, dict) else {}
                data = {**data, **previous} if only_missing else {**previous, **data}
            if len(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()) > MAX_PREFS_BYTES:
                raise PayloadTooLarge("prefs are limited to 16 KB")
            prefs[CLIENT_PREFS_KEY] = data
            prof.prefs = prefs
            await s.commit()
        return data


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
