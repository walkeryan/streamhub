"""Process-level configuration (environment), via pydantic-settings.

User-editable runtime settings (region, proxy_mode, …) live in the ``setting``
table instead; see ``app.services.settings_service``.
"""

from __future__ import annotations

import logging
import secrets
from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "1.0.0"
# Every token is signed, and every stored source credential encrypted, with keys derived from
# SECRET_KEY: one short enough to guess is everyone's session and every upstream password.
MIN_SECRET_KEY = 32


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore", case_sensitive=False)

    config_dir: Path = Field(default=Path("/config"))
    secret_key: str | None = None
    # An install whose stored credentials are already encrypted under a shorter key: keep it, loudly.
    secret_key_allow_short: bool = False
    admin_password: str | None = None
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "https://streamhub.ryan-walker.dev/api/v1/auth/google/callback"
    # The address that may claim the Owner user on its first Google sign-in.
    google_owner_email: str = ""
    # Retired allowlist. Read only as a fallback for the bootstrap address (its first entry).
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
    # Origins other than the server's own that may call the API with credentials. None by default:
    # the web app is served from the same origin, and the dev server proxies /api.
    cors_origins: str = ""
    # The proxies whose X-Forwarded-For names the real client. The same variable, with the
    # same default, that uvicorn reads for X-Forwarded-Proto.
    forwarded_allow_ips: str = "127.0.0.1"
    # /api/docs and /api/openapi.json describe every route to whoever asks: off unless enabled.
    api_docs_enabled: bool = False
    log_level: str = "INFO"
    log_format: str = "json"  # json | text
    enable_scheduler: bool = True
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    iptv_org_channels_url: str = "https://iptv-org.github.io/api/channels.json"
    plex_product: str = "StreamHub"
    stream_token_ttl_s: int = 6 * 3600
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600
    http_timeout_s: float = 20.0
    vaapi_device: str = "/dev/dri/renderD128"
    image_cache_max_mb: int = 512  # CONFIG_DIR/cache/images, oldest files evicted first; 0 disables
    recordings_dir: str = ""  # recording is off until this names an existing, writable directory
    recordings_min_free_gb: int = 50  # recordings stop, and none start, below this much free space
    # Football matchup cards (docs/SPORTS_MATCHUP_CARDS.md): team facts from ESPN's public API, and
    # generated helmet art from xAI's image API, which is off unless a key is set.
    sports_facts_enabled: bool = True
    xai_api_key: str | None = Field(default=None, repr=False)
    matchup_art_model: str = "grok-imagine-image-2.0"
    matchup_art_daily_limit: int = 100  # new images per UTC day, at about 6 cents each with the default model

    @cached_property
    def resolved_secret_key(self) -> str:
        """SECRET_KEY env, or a key persisted in CONFIG_DIR/secret.key (created once)."""
        if self.secret_key:
            if len(self.secret_key) < MIN_SECRET_KEY:
                if not self.secret_key_allow_short:
                    raise ValueError(
                        f"SECRET_KEY must be at least {MIN_SECRET_KEY} characters (openssl rand -hex 32). "
                        "A new install: set a longer one. An existing install: changing it makes the stored "
                        "source credentials unreadable, so either re-enter those after changing it, or set "
                        "SECRET_KEY_ALLOW_SHORT=true to keep this one."
                    )
                logging.getLogger(__name__).warning(
                    "SECRET_KEY is shorter than %d characters and SECRET_KEY_ALLOW_SHORT keeps it. "
                    "Tokens and stored credentials are only as strong as this key.",
                    MIN_SECRET_KEY,
                )
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
    def image_cache_dir(self) -> Path:
        return self.cache_dir / "images"

    @property
    def matchup_art_dir(self) -> Path:
        return self.config_dir / "art" / "matchups"

    @property
    def helmet_overrides_path(self) -> Path:
        return self.config_dir / "art" / "helmets.json"

    @property
    def playlist_dir(self) -> Path:
        return self.config_dir / "playlists"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
