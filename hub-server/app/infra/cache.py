"""SQLite-backed TTL cache (implements domain.ports.Cache)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from app.infra.db import Database
from app.infra.models import CacheEntry, utcnow


class SqlCache:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> Any | None:
        async with self._db.session() as s:
            row = (await s.execute(select(CacheEntry).where(CacheEntry.key == key))).scalar_one_or_none()
            if row is None or row.expires_at < utcnow():
                return None
            return row.value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expires = utcnow() + timedelta(seconds=ttl_seconds)
        stmt = insert(CacheEntry).values(key=key, value=value, expires_at=expires)
        stmt = stmt.on_conflict_do_update(index_elements=[CacheEntry.key], set_={"value": value, "expires_at": expires})
        async with self._db.session() as s:
            await s.execute(stmt)
            await s.commit()

    async def purge_expired(self) -> int:
        async with self._db.session() as s:
            res = await s.execute(delete(CacheEntry).where(CacheEntry.expires_at < utcnow()))
            await s.commit()
            return int(getattr(res, "rowcount", 0) or 0)


class MemoryCache:
    """In-process cache, used in tests and as a fallback."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, Any]] = {}

    async def get(self, key: str) -> Any | None:
        import time

        item = self._d.get(key)
        if item is None or item[0] < time.monotonic():
            return None
        return item[1]

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        import time

        self._d[key] = (time.monotonic() + ttl_seconds, value)
