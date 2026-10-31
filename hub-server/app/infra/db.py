"""Async engine/session + Alembic migration runner."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

log = logging.getLogger(__name__)

HUB_ROOT = Path(__file__).resolve().parents[2]


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, future=True)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine.sync_engine, "connect")
            def _pragmas(dbapi_conn: Any, _rec: Any) -> None:
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=10000")
                cur.close()

        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as s:
            yield s

    async def dispose(self) -> None:
        await self.engine.dispose()

    def sync_url(self) -> str:
        return self.url.replace("+aiosqlite", "")

    async def migrate(self) -> None:
        """Run ``alembic upgrade head`` (in a worker thread; Alembic is sync)."""
        await asyncio.to_thread(run_migrations, self.sync_url())


def run_migrations(sync_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(HUB_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(HUB_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
