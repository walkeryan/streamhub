"""Ports: the interfaces services depend on.

Adapters implement these structurally (typing.Protocol); services never import
adapters. Optional capabilities are separate protocols so a service can check
``isinstance(provider, LiveProvider)`` (they are ``runtime_checkable``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.domain.entities import (
    CatalogItem,
    ChannelInfo,
    EpgSourceSpec,
    LanguageGuess,
    MetaTitle,
    ProgrammeInfo,
    ProviderOffer,
    ServiceLink,
    SourceDraft,
    SourceRef,
    StreamTarget,
    TestResult,
)

# --------------------------------------------------------------------------- sinks


class CatalogSink(Protocol):
    """Where an adapter pushes what it discovers during a sync."""

    async def add_channels(self, channels: Iterable[ChannelInfo]) -> None: ...

    async def add_items(self, items: Iterable[CatalogItem]) -> None: ...

    async def set_config_hint(self, key: str, value: Any) -> None:
        """Persist a non-secret hint discovered while syncing (e.g. url-tvg)."""
        ...


# ------------------------------------------------------------------ source providers


@runtime_checkable
class SourceProvider(Protocol):
    """Every source adapter implements this."""

    ref: SourceRef

    async def test(self) -> TestResult: ...

    async def sync(self, sink: CatalogSink) -> None: ...

    def config_summary(self) -> dict[str, Any]:
        """Non-secret view of the configuration for clients."""
        ...

    def secrets(self) -> list[str]:
        """Secret strings to register with the log redaction filter."""
        ...


@runtime_checkable
class PlaybackProvider(Protocol):
    async def stream_for_item(self, item: CatalogItem) -> StreamTarget: ...


@runtime_checkable
class LiveProvider(Protocol):
    async def stream_for_channel(self, channel: ChannelInfo) -> StreamTarget: ...

    async def catchup_for_channel(self, channel: ChannelInfo, programme: ProgrammeInfo) -> StreamTarget | None: ...


@runtime_checkable
class EpgSourceProvider(Protocol):
    async def epg_sources(self) -> list[EpgSourceSpec]: ...


@runtime_checkable
class ShortEpgProvider(Protocol):
    async def short_epg(self, channel: ChannelInfo, limit: int = 4) -> list[ProgrammeInfo]: ...


@runtime_checkable
class DetailProvider(Protocol):
    """Fetch richer detail / children lazily (seasons, episodes, VOD info)."""

    async def item_detail(self, item: CatalogItem) -> CatalogItem: ...

    async def children(self, item: CatalogItem) -> list[CatalogItem]: ...


@runtime_checkable
class ProgressReporter(Protocol):
    async def report_progress(
        self, item: CatalogItem, position_ms: int, duration_ms: int | None, state: str
    ) -> None: ...


@runtime_checkable
class ContinueWatchingProvider(Protocol):
    async def continue_watching(self, limit: int = 20) -> list[tuple[CatalogItem, int]]:
        """Items with their resume position in ms."""
        ...


@runtime_checkable
class LibrarySearchProvider(Protocol):
    async def search(self, query: str, limit: int = 20) -> list[CatalogItem]: ...


@runtime_checkable
class ImageAuthProvider(Protocol):
    """Adapter whose artwork URLs need auth; the API serves them through a signed image proxy."""

    def image_headers(self, url: str) -> dict[str, str] | None: ...


@runtime_checkable
class InteractiveAuthProvider(Protocol):
    """Adapter that needs an interactive step before it can sync (Jellyfin Quick Connect)."""

    async def complete_auth(self) -> tuple[bool, dict[str, Any]]:
        """Advance auth. Returns (authenticated, config updates to persist; None values delete keys)."""
        ...


@dataclass(slots=True)
class AdapterContext:
    """Shared dependencies handed to adapter factories (no infra imports in adapters needed)."""

    http_factory: Callable[..., Any]  # () -> httpx.AsyncClient
    cache: Cache
    data_dir: Path
    client_id: str = "streamhub"
    product: str = "StreamHub"
    extras: dict[str, Any] = field(default_factory=dict)


SourceFactory = Callable[[SourceRef, dict[str, Any], AdapterContext], SourceProvider]


class LinkFlowProvider(Protocol):
    """Out-of-band account linking (Plex PIN)."""

    async def start(self) -> dict[str, Any]: ...

    async def poll(self, pin_id: int) -> list[SourceDraft] | None:
        """None while pending; list of sources to create when linked."""
        ...


# --------------------------------------------------------------------- other ports


class EpgProvider(Protocol):
    """Parses an EPG document (XMLTV) into programmes, streaming."""

    def iter_programmes(
        self, spec: EpgSourceSpec, wanted_channels: set[str] | None = None
    ) -> AsyncIterator[list[ProgrammeInfo]]: ...


class MetadataProvider(Protocol):
    """TMDB-like metadata + watch providers."""

    @property
    def enabled(self) -> bool: ...

    async def search(self, query: str, limit: int = 20) -> list[MetaTitle]: ...

    async def details(self, media_type: str, tmdb_id: int) -> MetaTitle | None: ...

    async def watch_providers(self, media_type: str, tmdb_id: int, region: str) -> list[ProviderOffer]: ...

    async def trending(self, limit: int = 20) -> list[MetaTitle]: ...

    async def find_by_imdb(self, imdb_id: str) -> MetaTitle | None: ...

    def image_url(self, path: str | None, size: str = "w500") -> str | None: ...


class LanguageInferrer(Protocol):
    async def prepare(self) -> None:
        """Load any external data (e.g. iptv-org DB); must never raise."""
        ...

    def infer(self, channel: ChannelInfo) -> LanguageGuess: ...

    def language_name(self, code: str) -> str: ...

    def normalise(self, code: str | None) -> str | None:
        """Map any 2/3-letter code or language name to ISO 639-3."""
        ...


class DeeplinkProvider(Protocol):
    async def link_for(
        self,
        service_key: str,
        *,
        tmdb_type: str | None,
        tmdb_id: int | None,
        title: str,
    ) -> ServiceLink | None: ...


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...
