"""Pydantic v2 models mirroring contracts/openapi.yaml components."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SourceTypeL = Literal["plex", "jellyfin", "xtream", "m3u", "streaming_service"]
MediaKindL = Literal["movie", "show", "season", "episode", "channel", "vod", "series"]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Error(_M):
    code: str
    message: str


class Profile(_M):
    id: str
    name: str
    avatar: str | None = None
    is_admin: bool
    theme_id: str
    languages: list[str]
    has_pin: bool
    kids: bool
    disclaimer_accepted_version: str | None = None


class ProfileWrite(_M):
    """Profile body for POST/PATCH (all fields optional; server-owned fields ignored)."""

    id: str | None = None
    name: str | None = None
    avatar: str | None = None
    is_admin: bool | None = None
    theme_id: str | None = None
    languages: list[str] | None = None
    has_pin: bool | None = None
    kids: bool | None = None
    pin: str | None = Field(default=None, description="optional 4-digit PIN on create")
    password: str | None = Field(default=None, description="admin only: change admin password")


class AuthTokens(_M):
    access_token: str
    refresh_token: str
    profile: Profile


class LoginRequest(_M):
    profile_id: str
    pin: str | None = None
    password: str | None = None


class RefreshRequest(_M):
    refresh_token: str = ""


class DeviceCode(_M):
    device_code: str
    user_code: str
    expires_in: int
    interval: int


class DevicePoll(_M):
    device_code: str = ""


class DeviceApprove(_M):
    user_code: str = ""
    profile_id: str | None = None


class PinBody(_M):
    pin: str | None = None


class Disclaimer(_M):
    version: str
    text: str


class DisclaimerAccept(_M):
    version: str | None = None


class Source(_M):
    id: str
    type: SourceTypeL
    name: str
    enabled: bool
    priority: int
    region: str = "US"
    status: Literal["ok", "syncing", "error", "unauthenticated"]
    last_sync: datetime | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    service_key: str | None = None
    config_summary: dict[str, Any] = Field(default_factory=dict)


class SourceCreate(_M):
    type: SourceTypeL
    name: str
    region: str | None = None
    service_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class SourcePatch(_M):
    type: SourceTypeL | None = None
    name: str | None = None
    region: str | None = None
    service_key: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    config: dict[str, Any] | None = None


class TestResult(_M):
    ok: bool
    message: str


class PlexPin(_M):
    pin_id: int
    code: str
    link_url: str


class ServiceInfo(_M):
    service_key: str
    name: str
    android_package: str | None = None
    android_component: str | None = None
    android_title_uri_template: str | None = None
    web_url: str
    supports_title_link: bool
    tmdb_provider_ids: list[int]


class SourceBadge(_M):
    source_id: str
    source_type: SourceTypeL
    name: str
    playable_in_app: bool
    kind: Literal["library", "vod", "live", "flatrate", "free", "ads", "rent", "buy"] | None = None
    quality: str | None = None


class Programme(_M):
    id: str
    channel_id: str
    start: datetime
    stop: datetime
    title: str
    description: str | None = None
    category: str | None = None
    icon: str | None = None
    catchup_available: bool = False


class Tile(_M):
    id: str
    kind: MediaKindL
    title: str
    subtitle: str | None = None
    year: int | None = None
    poster: str | None = None
    backdrop: str | None = None
    logo: str | None = None
    rating: float | None = None
    progress_pct: float | None = None
    is_favorite: bool = False
    sources: list[SourceBadge]
    now_playing: Programme | None = None


class Row(_M):
    id: str
    title: str
    layout: Literal["poster", "landscape", "hero", "channel"] = "poster"
    tiles: list[Tile]


class Home(_M):
    hero: list[Tile]
    rows: list[Row]


class CastMember(_M):
    name: str
    character: str
    photo: str | None = None


class TitleDetail(Tile):
    overview: str = ""
    genres: list[str] = Field(default_factory=list)
    runtime_min: int | None = None
    cast: list[CastMember] = Field(default_factory=list)
    seasons: list[Tile] = Field(default_factory=list)
    episodes: list[Tile] = Field(default_factory=list)
    attribution: str = ""


class BrowsePage(_M):
    tiles: list[Tile]
    page: int
    total: int
    has_more: bool = False
    page_size: int = 60


class Channel(_M):
    id: str
    source_id: str
    ext_id: str
    number: int
    name: str
    logo: str | None = None
    group: str
    language: str
    language_source: Literal["tag", "db", "country", "group", "flag", "name", "user"] | None = None
    country: str | None = None
    epg_id: str | None = None
    catchup_days: int = 0
    is_favorite: bool
    now: Programme | None = None
    next: Programme | None = None


class ChannelList(_M):
    list_id: str
    channels: list[Channel]
    total: int | None = None
    offset: int = 0
    limit: int | None = None


class ChannelPatch(_M):
    number: int | None = None
    language: str | None = None
    group: str | None = None
    epg_id: str | None = None
    name: str | None = None


class ChannelGroup(_M):
    id: str
    source_id: str
    name: str
    channel_count: int
    hidden: bool
    language_override: str | None = None


class GroupPatch(_M):
    hidden: bool | None = None
    language_override: str | None = None
    sort: int | None = None


class LanguageCount(_M):
    code: str
    name: str
    count: int


class SearchResults(_M):
    play_here: list[Tile]
    open_in_app: list[Tile]
    channels: list[Channel]
    attribution: str


class PlaybackRequest(_M):
    id: str
    source_id: str | None = None
    client: Literal["android_tv", "web"] = "web"
    prefer_hls: bool = False
    catchup_programme_id: str | None = None


class ChannelContext(_M):
    list_id: str
    index: int
    count: int


class Handoff(_M):
    service_key: str
    android_package: str
    android_uri: str | None = None
    android_component: str | None = None
    android_extras: dict[str, str] = Field(default_factory=dict)
    web_url: str
    supports_title_link: bool


class PlaybackDescriptor(_M):
    id: str
    mode: Literal["direct", "proxy", "hls", "handoff"]
    url: str | None = None
    mime: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    live: bool = False
    resume_position_ms: int = 0
    duration_ms: int | None = None
    title: str
    subtitle: str | None = None
    poster: str | None = None
    channel_context: ChannelContext | None = None
    handoff: Handoff | None = None
    drm: None = None


class ProgressReport(_M):
    id: str
    source_id: str | None = None
    position_ms: int
    duration_ms: int | None = None
    state: Literal["playing", "paused", "stopped"]


class Favorite(_M):
    kind: MediaKindL
    id: str
    sort: int | None = None


class WatchlistPut(_M):
    id: str


class Theme(_M):
    id: str = ""
    name: str
    builtin: bool = False
    tokens: dict[str, Any]


class Settings(_M):
    tmdb_api_key_set: bool = False
    region: str = "US"
    sleep_timer_min: int | None = None
    epg_refresh_hours: int = 12
    transcode_hw: Literal["none", "vaapi", "nvenc", "qsv"] = "none"
    proxy_mode: Literal["auto", "always", "never"] = "auto"


class SettingsPatch(_M):
    region: str | None = None
    sleep_timer_min: int | None = None
    epg_refresh_hours: int | None = None
    transcode_hw: Literal["none", "vaapi", "nvenc", "qsv"] | None = None
    proxy_mode: Literal["auto", "always", "never"] | None = None


class TmdbKey(_M):
    api_key: str = ""


class HomeLayoutRow(_M):
    id: str
    hidden: bool = False
    sort: int | None = None


class HomeLayout(_M):
    rows: list[HomeLayoutRow]


class Health(_M):
    status: str
    version: str
    db: bool
