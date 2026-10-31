"""User-editable runtime settings stored in the ``setting`` table."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.logging import secret_registry
from app.infra.models import Setting

DEFAULTS: dict[str, Any] = {
    "region": "US",
    "sleep_timer_min": None,
    "epg_refresh_hours": 12,
    "transcode_hw": "none",
    "proxy_mode": "auto",
}
ALLOWED = {
    "transcode_hw": {"none", "vaapi", "nvenc", "qsv"},
    "proxy_mode": {"auto", "always", "never"},
}
TMDB_KEY = "tmdb_api_key_enc"


class SettingsService:
    def __init__(self, db: Database, crypto: Crypto, env_tmdb_key: str | None = None) -> None:
        self._db = db
        self._crypto = crypto
        self._env_tmdb = env_tmdb_key
        self._cache: dict[str, Any] = {}
        self._tmdb_key: str | None = env_tmdb_key
        secret_registry.add(env_tmdb_key)

    async def load(self) -> None:
        async with self._db.session() as s:
            rows = (await s.execute(select(Setting))).scalars().all()
        self._cache = {r.key: r.value for r in rows}
        enc = self._cache.get(TMDB_KEY)
        if isinstance(enc, str):
            val = self._crypto.try_decrypt_json(enc)
            if isinstance(val, str) and val:
                self._tmdb_key = val
                secret_registry.add(val)

    def get(self, key: str) -> Any:
        return self._cache.get(key, DEFAULTS.get(key))

    def tmdb_key(self) -> str | None:
        return self._tmdb_key

    def public(self) -> dict[str, Any]:
        return {
            "tmdb_api_key_set": bool(self._tmdb_key),
            "region": self.get("region"),
            "sleep_timer_min": self.get("sleep_timer_min"),
            "epg_refresh_hours": int(self.get("epg_refresh_hours") or 12),
            "transcode_hw": self.get("transcode_hw"),
            "proxy_mode": self.get("proxy_mode"),
        }

    async def _put(self, key: str, value: Any) -> None:
        async with self._db.session() as s:
            row = await s.get(Setting, key)
            if row is None:
                s.add(Setting(key=key, value=value))
            else:
                row.value = value
            await s.commit()
        self._cache[key] = value

    async def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        for k, v in patch.items():
            if k not in DEFAULTS:
                continue
            if k in ALLOWED and v not in ALLOWED[k]:
                raise ValueError(f"invalid {k}")
            if k == "region" and v is not None:
                v = str(v).upper()[:2]
            if k == "epg_refresh_hours" and v is not None:
                v = max(1, min(int(v), 168))
            await self._put(k, v)
        return self.public()

    async def set_tmdb_key(self, key: str | None) -> None:
        key = (key or "").strip() or None
        await self._put(TMDB_KEY, self._crypto.encrypt_json(key) if key else None)
        self._tmdb_key = key or self._env_tmdb
        secret_registry.add(key)
