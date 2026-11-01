"""Process-level configuration (environment), via pydantic-settings.

User-editable runtime settings (region, proxy_mode, …) live in the ``setting``
table instead; see ``app.services.settings_service``.
"""

from __future__ import annotations

import secrets
from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "1.0.0"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore", case_sensitive=False)

    config_dir: Path = Field(default=Path("/config"))
    secret_key: str | None = None
    admin_password: str | None = None
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "https://streamhub.ryan-walker.dev/api/v1/auth/google/callback"
    google_allowed_emails: str = ""
    google_owner_profile_id: str = ""
    google_auth_required: bool = False
    content_policy_enabled: bool = False
    tmdb_api_key: str | None = None
    web_dist: Path = Field(default=Path("/app/web-dist"))
    transcode_dir: Path | None = None
    dvr_enabled: bool = False
    dvr_dir: Path | None = None
    dvr_reserve_dir: Path | None = None
    database_url: str | None = None
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"
    log_format: str = "json"  # json | text
    enable_scheduler: bool = True
    ffmpeg_path: str = "ffmpeg"
    iptv_org_channels_url: str = "https://iptv-org.github.io/api/channels.json"
    plex_product: str = "StreamHub"
    stream_token_ttl_s: int = 6 * 3600
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600
    http_timeout_s: float = 20.0
    vaapi_device: str = "/dev/dri/renderD128"

    @cached_property
    def resolved_secret_key(self) -> str:
        """SECRET_KEY env, or a key persisted in CONFIG_DIR/secret.key (created once)."""
        if self.secret_key:
            return self.secret_key
        path = self.config_dir / "secret.key"
        if path.exists():
            return path.read_text().strip()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(48)
        path.write_text(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return key

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.config_dir / 'streamhub.db'}"

    @property
    def transcode_path(self) -> Path:
        if self.transcode_dir:
            return self.transcode_dir
        default = Path("/transcode")
        if default.is_dir():
            return default
        return self.config_dir / "transcode"

    @property
    def cache_dir(self) -> Path:
        return self.config_dir / "cache"

    @property
    def playlist_dir(self) -> Path:
        return self.config_dir / "playlists"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
