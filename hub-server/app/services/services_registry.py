"""Service registry table (seeded from docs/DEEPLINKS.md) for hand-off services."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.adapters.deeplink.registry import ServiceRow, load_seed
from app.infra.db import Database
from app.infra.models import ServiceRegistry


def _to_row(r: ServiceRegistry) -> ServiceRow:
    return ServiceRow(
        key=r.key,
        name=r.name,
        tmdb_provider_ids=[int(x) for x in r.tmdb_provider_ids or []],
        android_package=r.android_package,
        android_component=r.android_component,
        android_title_uri=r.android_title_uri,
        web_title_url=r.web_title_url,
        web_search_url=r.web_search_url,
        supports_title_link=r.supports_title_link,
        notes=r.notes,
    )


class ServiceRegistryService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def seed(self) -> None:
        """Insert missing seed rows; never overwrite user edits."""
        async with self._db.session() as s:
            existing = set((await s.execute(select(ServiceRegistry.key))).scalars())
            for row in load_seed():
                if row.key in existing:
                    continue
                s.add(ServiceRegistry(**row.to_dict()))
            await s.commit()

    async def get(self, key: str) -> ServiceRow | None:
        async with self._db.session() as s:
            r = await s.get(ServiceRegistry, key)
        return _to_row(r) if r else None

    async def list(self) -> list[ServiceRow]:
        async with self._db.session() as s:
            return [_to_row(r) for r in (await s.execute(select(ServiceRegistry).order_by(ServiceRegistry.key))).scalars()]

    async def api_list(self) -> list[dict[str, Any]]:
        out = []
        for r in await self.list():
            web = r.web_search_url or r.web_title_url or ""
            if "{" in web:
                web = web.split("/{", 1)[0].split("?", 1)[0]
            if "/search" in web:
                web = web.split("/search", 1)[0]
            out.append(
                {
                    "service_key": r.key,
                    "name": r.name,
                    "android_package": r.android_package,
                    "web_url": web,
                    "supports_title_link": r.supports_title_link in ("true", "partial"),
                    "tmdb_provider_ids": r.tmdb_provider_ids,
                    "link_support": r.supports_title_link,
                    "notes": r.notes,
                }
            )
        return out
