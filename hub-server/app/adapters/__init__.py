"""Adapter registration.

This is the *only* module that knows the concrete adapter set. Adding a new
source type = write an adapter package + one ``registry.register`` line here.
Services only see ``SourceRegistry`` and the ports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.entities import SourceType

if TYPE_CHECKING:
    from app.services.registry import SourceRegistry


def register_builtin_adapters(registry: SourceRegistry) -> None:
    from app.adapters.jellyfin import provider as jellyfin
    from app.adapters.m3u import provider as m3u
    from app.adapters.plex import provider as plex
    from app.adapters.streaming_service import provider as streaming
    from app.adapters.xtream import provider as xtream

    registry.register(SourceType.PLEX, plex.factory, in_app=True, requires_disclaimer=False)
    registry.register(SourceType.JELLYFIN, jellyfin.factory, in_app=True, requires_disclaimer=False)
    registry.register(SourceType.XTREAM, xtream.factory, in_app=True, requires_disclaimer=True)
    registry.register(SourceType.M3U, m3u.factory, in_app=True, requires_disclaimer=True)
    registry.register(SourceType.STREAMING_SERVICE, streaming.factory, in_app=False, requires_disclaimer=False)
