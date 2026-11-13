"""User-editable runtime settings stored in the ``setting`` table."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.logging import secret_registry
from app.infra.models import Setting
from app.services.network import DEFAULT_HOME_NETWORKS, clean_networks

DEFAULTS: dict[str, Any] = {
    "region": "US",
    "sleep_timer_min": None,
    "epg_refresh_hours": 12,
    "transcode_hw": "none",
    "proxy_mode": "auto",
    # Who may sign in with Google unasked: anyone (then waits for approval) or invitees only.
    "signup_mode": "approval",
    "notify_webhook": None,
    # Where a user without remote.access may use the server from.
    "home_networks": DEFAULT_HOME_NETWORKS,
    # The shelves Home shows, in order ("Name", "movies:Name", "shows:Name"); empty: Home chooses.
    "home_shelves": [],
}
ALLOWED = {
    "transcode_hw": {"none", "vaapi", "nvenc", "qsv"},
    "proxy_mode": {"auto", "always", "never"},
    "signup_mode": {"approval", "invite_only"},
}
MAX_HOME_SHELVES = 24
TMDB_KEY = "tmdb_api_key_enc"
REQUEST_MANAGER_URL = "request_manager_url"
REQUEST_MANAGER_KEY = "request_manager_api_key_enc"


def clean_request_manager_url(url: str | None) -> str | None:
    """Base URL of the request manager, without credentials or the API path."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return None
    if url.lower().endswith("/api/v1"):
        url = url[:-7].rstrip("/")
    try:
        parts = urlsplit(url)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname) and parts.port != 0
    except ValueError:
        valid = False
    if not valid or parts.username or parts.password or parts.query or parts.fragment or len(url) > 512:
        raise ValueError("invalid request manager url")
    return url


def clean_webhook_url(url: Any) -> str | None:
    """An http(s) address to notify, or None to clear it."""
    url = str(url or "").strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname) and parts.port != 0
    except ValueError:
        valid = False
    if not valid or parts.fragment or len(url) > 1024:
        raise ValueError("invalid notify_webhook")
    return url


def clean_shelves(value: Any) -> list[str]:
    """A ``home_shelves`` list as it is stored: trimmed, in order, without blanks or repeats."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise ValueError("invalid home_shelves")
    names = list(dict.fromkeys(name.strip() for name in value if name.strip()))
    if len(names) > MAX_HOME_SHELVES or any(len(name) > 200 for name in names):
        raise ValueError("invalid home_shelves")
    return names


def _origin(url: str | None) -> tuple[str, str, int | None] | None:
    if not url:
        return None
    parts = urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port


class SettingsService:
    def __init__(self, db: Database, crypto: Crypto, env_tmdb_key: str | None = None) -> None:
        self._db = db
        self._crypto = crypto
        self._env_tmdb = env_tmdb_key
        self._cache: dict[str, Any] = {}
        self._tmdb_key: str | None = env_tmdb_key
        self._request_manager_key: str | None = None
        secret_registry.add(env_tmdb_key)

    async def load(self) -> None:
        async with self._db.session() as s:
            rows = (await s.execute(select(Setting))).scalars().all()
        self._cache = {r.key: r.value for r in rows}
        secret_registry.add(self._cache.get("notify_webhook"))
        enc = self._cache.get(TMDB_KEY)
        if isinstance(enc, str):
            val = self._crypto.try_decrypt_json(enc)
            if isinstance(val, str) and val:
                self._tmdb_key = val
                secret_registry.add(val)
        enc = self._cache.get(REQUEST_MANAGER_KEY)
        val = self._crypto.try_decrypt_json(enc) if isinstance(enc, str) else None
        self._request_manager_key = val if isinstance(val, str) and val else None
        secret_registry.add(self._request_manager_key)

    def get(self, key: str) -> Any:
        return self._cache.get(key, DEFAULTS.get(key))

    def tmdb_key(self) -> str | None:
        return self._tmdb_key

    def public(self) -> dict[str, Any]:
        return {
            "tmdb_api_key_set": bool(self._tmdb_key),
            "request_manager_configured": self.request_manager() is not None,
            "region": self.get("region"),
            "sleep_timer_min": self.get("sleep_timer_min"),
            "epg_refresh_hours": int(self.get("epg_refresh_hours") or 12),
            "transcode_hw": self.get("transcode_hw"),
            "proxy_mode": self.get("proxy_mode"),
            "signup_mode": self.get("signup_mode"),
            # The address may embed a secret path; only whether one is set is public.
            "notify_webhook_set": bool(self.get("notify_webhook")),
            "home_networks": self.get("home_networks") or DEFAULT_HOME_NETWORKS,
            "home_shelves": list(self.get("home_shelves") or []),
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
            if k == "signup_mode" and v is None:
                continue
            if k == "notify_webhook":
                v = clean_webhook_url(v)
                secret_registry.add(v)
            if k == "home_networks":
                # Null goes back to the defaults; anything else must be a list of networks.
                v = DEFAULT_HOME_NETWORKS if v is None else clean_networks(v)
            if k == "home_shelves":
                v = clean_shelves(v)
            await self._put(k, v)
        return self.public()

    async def set_tmdb_key(self, key: str | None) -> None:
        key = (key or "").strip() or None
        await self._put(TMDB_KEY, self._crypto.encrypt_json(key) if key else None)
        self._tmdb_key = key or self._env_tmdb
        secret_registry.add(key)

    def request_manager_url(self) -> str | None:
        url = self._cache.get(REQUEST_MANAGER_URL)
        return url if isinstance(url, str) and url else None

    def request_manager(self) -> tuple[str, str] | None:
        """(base url, api key) once both are stored. The key never leaves the server."""
        url = self.request_manager_url()
        return (url, self._request_manager_key) if url and self._request_manager_key else None

    def request_manager_public(self) -> dict[str, Any]:
        return {"url": self.request_manager_url(), "api_key_set": bool(self._request_manager_key)}

    async def set_request_manager(self, url: str | None, api_key: str | None) -> dict[str, Any]:
        """``api_key=None`` keeps the stored key, ``""`` clears it."""
        url = clean_request_manager_url(url)
        key = self._request_manager_key if api_key is None else (api_key.strip() or None)
        # A stored key belongs to the server it was entered for: never carry it to another
        # origin, and drop it when the server is removed.
        if url is None or (api_key is None and _origin(url) != _origin(self.request_manager_url())):
            key = None
        await self._put(REQUEST_MANAGER_URL, url)
        await self._put(REQUEST_MANAGER_KEY, self._crypto.encrypt_json(key) if key else None)
        self._request_manager_key = key
        secret_registry.add(key)
        return self.request_manager_public()

    async def set_recovery_paused(self, paused: bool) -> None:
        await self._put("playback_recovery_paused", paused)
