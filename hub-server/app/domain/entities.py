"""Pure domain entities. No framework, no I/O.

These dataclasses are what adapters produce and services consume. API schemas
(pydantic) and persistence models (SQLAlchemy) are mapped from/to these at the
edges.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class SourceType(enum.StrEnum):
    PLEX = "plex"
    JELLYFIN = "jellyfin"
    XTREAM = "xtream"
    M3U = "m3u"
    STREAMING_SERVICE = "streaming_service"


IPTV_SOURCE_TYPES = frozenset({SourceType.XTREAM, SourceType.M3U})
LIBRARY_SOURCE_TYPES = frozenset({SourceType.PLEX, SourceType.JELLYFIN})


class MediaKind(enum.StrEnum):
    MOVIE = "movie"
    SHOW = "show"
    SEASON = "season"
    EPISODE = "episode"
    CHANNEL = "channel"
    VOD = "vod"
    SERIES = "series"


class SourceStatus(enum.StrEnum):
    OK = "ok"
    SYNCING = "syncing"
    ERROR = "error"
    UNAUTHENTICATED = "unauthenticated"


class PlaybackMode(enum.StrEnum):
    DIRECT = "direct"
    PROXY = "proxy"
    HLS = "hls"
    HANDOFF = "handoff"


class LanguageSource(enum.StrEnum):
    TAG = "tag"
    DB = "db"
    COUNTRY = "country"
    GROUP = "group"
    FLAG = "flag"
    NAME = "name"
    USER = "user"


@dataclass(slots=True)
class SourceRef:
    """What an adapter knows about the Source it serves."""

    id: str
    type: SourceType
    name: str
    region: str = "US"
    service_key: str | None = None


@dataclass(slots=True)
class ChannelInfo:
    """A live channel as parsed from a provider (before language inference)."""

    ext_id: str
    name: str
    stream_url: str | None = None
    logo: str | None = None
    group: str = "Uncategorized"
    tvg_id: str | None = None
    tvg_name: str | None = None
    tvg_language: str | None = None
    tvg_country: str | None = None
    number: int | None = None
    catchup_days: int = 0
    catchup_type: str | None = None
    catchup_source: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CatalogItem:
    """A movie / show / VOD / series (or child) from a library or IPTV panel."""

    ext_id: str
    kind: MediaKind
    title: str
    year: int | None = None
    overview: str | None = None
    poster: str | None = None
    backdrop: str | None = None
    rating: float | None = None
    genres: list[str] = field(default_factory=list)
    tmdb_id: int | None = None
    imdb_id: str | None = None
    group: str | None = None
    parent_ext_id: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    duration_ms: int | None = None
    added_at: datetime | None = None
    stream_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProgrammeInfo:
    channel: str  # EPG channel id (tvg-id / epg_channel_id)
    start: datetime  # aware UTC
    stop: datetime  # aware UTC
    title: str
    description: str | None = None
    category: str | None = None
    icon: str | None = None

    age_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EpgSourceSpec:
    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class StreamTarget:
    """What an adapter returns when asked how to play something."""

    url: str
    mime: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    live: bool = False
    duration_ms: int | None = None
    resume_position_ms: int = 0
    title: str | None = None
    subtitle: str | None = None
    poster: str | None = None


@dataclass(slots=True)
class TestResult:
    ok: bool
    message: str = ""


@dataclass(slots=True)
class SourceDraft:
    """A source that an auth flow (e.g. Plex PIN) wants created."""

    type: SourceType
    name: str
    config: dict[str, Any]


@dataclass(slots=True)
class ProviderOffer:
    """A TMDB/JustWatch watch-provider offer for a title in a region."""

    provider_id: int
    provider_name: str
    kind: str  # flatrate|free|ads|rent|buy
    logo_path: str | None = None


@dataclass(slots=True)
class MetaTitle:
    """Normalised TMDB title."""

    tmdb_id: int
    media_type: str  # movie|tv
    title: str
    year: int | None = None
    overview: str | None = None
    poster: str | None = None
    backdrop: str | None = None
    rating: float | None = None
    genres: list[str] = field(default_factory=list)
    runtime_min: int | None = None
    imdb_id: str | None = None
    cast: list[dict[str, Any]] = field(default_factory=list)
    logo: str | None = None
    popularity: float = 0.0

    age_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ServiceLink:
    service_key: str
    name: str
    android_package: str | None
    android_uri: str | None
    web_url: str
    supports_title_link: bool
    android_extras: dict[str, str] = field(default_factory=dict)
    android_component: str | None = None


@dataclass(slots=True)
class LanguageGuess:
    code: str  # ISO 639-3 or "und"
    source: LanguageSource | None
