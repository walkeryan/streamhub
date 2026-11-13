"""Keeps the catalog classified in the background, so a restricted user's browsing never
waits on (or silently misses) a title nobody happened to open first.

Evidence for a title counts for ``EVIDENCE_FRESH``; this job looks every title with a TMDB
id up again well before that, a small batch at a time, walking the catalog in key order
with its place kept in the ``setting`` table.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, exists, func, or_, select

from app.infra.db import Database
from app.infra.models import AgeClassification, Title
from app.services.content_policy import EVIDENCE_FRESH, TOP_KINDS, ContentPolicy

log = logging.getLogger(__name__)

CLASSIFY_BATCH = 60  # titles per run; with the two-minute schedule, about 43,000 a day
CLASSIFY_PAUSE_S = 0.2  # between lookups: a handful a second, far inside TMDB's allowance
CLASSIFY_BUDGET_S = 90.0  # a slow upstream ends the run instead of overlapping the next one
REFRESH_AFTER = EVIDENCE_FRESH - timedelta(days=3)  # renewed with three days of proof still in hand
FAILURE_STREAK = 5  # this many lookups failing in a row is an outage, not five bad ids
CURSOR_KEY = "_classification_cursor"


class ClassificationJob:
    def __init__(
        self,
        db: Database,
        meta: Any,
        settings: Any,
        *,
        batch: int = CLASSIFY_BATCH,
        pause_s: float = CLASSIFY_PAUSE_S,
    ) -> None:
        self._db = db
        self._meta = meta
        self._settings = settings
        self._batch = batch
        self._pause_s = pause_s

    def _lacking(self) -> tuple[Any, Any]:
        """(media type, the predicate "a movie or show row without evidence young enough")."""
        media_type = case((Title.kind.in_(("movie", "vod")), "movie"), else_="tv")
        young = exists(
            select(1).where(
                AgeClassification.subject_key == ContentPolicy._subject(Title),
                AgeClassification.provider == "tmdb",
                AgeClassification.observed_at >= datetime.now(UTC) - REFRESH_AFTER,
            )
        )
        return media_type, and_(Title.kind.in_(TOP_KINDS), Title.tmdb_id > 0, ~young)

    async def remaining(self) -> int:
        media_type, lacking = self._lacking()
        keys = select(media_type, Title.tmdb_id).where(lacking).distinct().subquery()
        async with self._db.session() as s:
            return int((await s.execute(select(func.count()).select_from(keys))).scalar_one())

    async def _next(self, after: tuple[str, int] | None, limit: int) -> list[tuple[str, int]]:
        media_type, lacking = self._lacking()
        query = select(media_type.label("media_type"), Title.tmdb_id).where(lacking).distinct()
        if after is not None:
            query = query.where(or_(media_type > after[0], and_(media_type == after[0], Title.tmdb_id > after[1])))
        async with self._db.session() as s:
            rows = await s.execute(query.order_by("media_type", Title.tmdb_id).limit(limit))
            return [(str(kind), int(tmdb_id)) for kind, tmdb_id in rows.all()]

    def _cursor(self) -> tuple[str, int] | None:
        kind, _, number = str(self._settings.get(CURSOR_KEY) or "").partition(":")
        return (kind, int(number)) if kind in ("movie", "tv") and number.isdigit() else None

    async def run_batch(self) -> dict[str, int]:
        """Look up the next batch. Safe to call at any time; a second call simply does the next batch."""
        done = {"classified": 0, "failed": 0, "remaining": 0}
        if not self._meta.enabled:
            return done  # no TMDB key, no evidence to fetch
        cursor = self._cursor()
        todo = await self._next(cursor, self._batch)
        if not todo and cursor is not None:
            # End of a lap: start over, which is also when earlier failures get another try.
            cursor = None
            todo = await self._next(None, self._batch)
        streak, started = 0, time.monotonic()
        for media_type, tmdb_id in todo:
            # ``details`` records the evidence it fetched (or that the fetch failed) itself.
            try:
                found = await self._meta.details(media_type, tmdb_id)
            except Exception as exc:
                log.warning("classification lookup failed", extra={"error_type": type(exc).__name__})
                found = None
            cursor = (media_type, tmdb_id)
            done["classified" if found is not None else "failed"] += 1
            streak = 0 if found is not None else streak + 1
            if streak >= FAILURE_STREAK or time.monotonic() - started > CLASSIFY_BUDGET_S:
                break
            await asyncio.sleep(self._pause_s)
        done["remaining"] = await self.remaining()
        if todo:
            lap_over = len(todo) < self._batch and cursor == todo[-1]
            await self._settings._put(CURSOR_KEY, "" if lap_over or cursor is None else f"{cursor[0]}:{cursor[1]}")
        return done
