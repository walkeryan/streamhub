"""Read-only PMS guide snapshots; never a LiveProvider or a playback grant.

Official contract: https://developer.plex.tv/pms/ (Media Providers / Live TV).
Only advertised same-server grid paths are followed. No tuner, DVR, linking,
recording, media-part, or stream endpoint is called.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

GRID = re.compile(r"^/(tv\.plex\.providers\.epg\.(?:xmltv|cloud|custom):[0-9]+)/grid$")
MAX_PROVIDERS = 4
MAX_CHANNELS = 40
MAX_AIRINGS = 200


@dataclass(frozen=True, slots=True)
class GuideChannel:
    source_id: str
    provider_id: str
    channel_id: str
    grid_key: str
    title: str
    number: str | None = None
    language_hint: str | None = None
    audio_language: None = None
    audio_verified: bool = False
    playable: bool = False


@dataclass(frozen=True, slots=True)
class GuideProgramme:
    source_id: str
    provider_id: str
    channel_id: str
    programme_id: str
    airing_id: str
    start: datetime
    stop: datetime
    title: str
    description: str | None = None
    content_rating: str | None = None


@dataclass(frozen=True, slots=True)
class GuideSnapshot:
    source_id: str
    day: date
    status: str
    channels: tuple[GuideChannel, ...] = ()
    programmes: tuple[GuideProgramme, ...] = ()
    truncated: bool = False
    rejected_airings: int = 0


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _identity(value: Any) -> str | None:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else _text(value)


def _time(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def advertised_grids(container: dict[str, Any]) -> list[tuple[str, str]]:
    result: dict[str, str] = {}
    for provider in _rows(container.get("MediaProvider")):
        for feature in _rows(provider.get("Feature")):
            key = _text(feature.get("key"))
            match = GRID.fullmatch(key or "")
            if feature.get("type") == "grid" and match and provider.get("identifier") == match[1]:
                result[match[1]] = key or ""
    return list(result.items())


def map_airing(channel: GuideChannel, metadata: dict[str, Any], media: dict[str, Any]) -> GuideProgramme | None:
    # Neither display numbers/names nor the shorter gridKey establish channel identity.
    if media.get("channelIdentifier") != channel.channel_id or media.get("protocol") != "livetv":
        return None
    programme_id = _identity(metadata.get("ratingKey"))
    airing_id = _identity(media.get("id"))
    title = _text(metadata.get("title"))
    start, stop = _time(media.get("beginsAt")), _time(media.get("endsAt"))
    if not programme_id or not airing_id or not title or start is None or stop is None or stop <= start:
        return None
    return GuideProgramme(
        channel.source_id,
        channel.provider_id,
        channel.channel_id,
        programme_id,
        airing_id,
        start,
        stop,
        title,
        _text(metadata.get("summary")),
        _text(metadata.get("contentRating")),
    )


async def read_guide(source_id: str, day: date, get: Callable[..., Awaitable[dict[str, Any]]]) -> GuideSnapshot:
    """At most 4 providers / 40 channel-day reads / 200 airings per channel, 30s total.

    Failure propagates rather than masquerading as an empty successful schedule.
    No current programme or time is inferred from title, duration, or addedAt.
    """
    async with asyncio.timeout(30):
        providers = advertised_grids(await get("/media/providers"))
        if not providers:
            return GuideSnapshot(source_id, day, "no_advertised_guide")
        truncated = len(providers) > MAX_PROVIDERS
        channels: list[GuideChannel] = []
        programmes: dict[tuple[str, str, str, str, datetime], GuideProgramme] = {}
        seen_channels: set[tuple[str, str]] = set()
        rejected = 0
        for provider_id, grid_path in providers[:MAX_PROVIDERS]:
            response = await get(grid_path.removesuffix("/grid") + "/lineups/dvr/channels")
            raw_channels = _rows(response.get("Channel"))
            for row in raw_channels:
                channel_id, grid_key, title = (
                    _identity(row.get("id")),
                    _text(row.get("gridKey")),
                    _text(row.get("title")),
                )
                if not channel_id or not grid_key or not title or (provider_id, channel_id) in seen_channels:
                    continue
                if len(channels) >= MAX_CHANNELS:
                    truncated = True
                    break
                channel = GuideChannel(
                    source_id,
                    provider_id,
                    channel_id,
                    grid_key,
                    title,
                    _identity(row.get("vcn")),
                    _text(row.get("language")),
                )
                seen_channels.add((provider_id, channel_id))
                channels.append(channel)
                grid = await get(grid_path, channelGridKey=grid_key, date=day.isoformat())
                count = 0
                for metadata in _rows(grid.get("Metadata")):
                    for media in _rows(metadata.get("Media")):
                        if count >= MAX_AIRINGS:
                            truncated = True
                            break
                        count += 1
                        programme = map_airing(channel, metadata, media)
                        if programme is None:
                            rejected += 1
                            continue
                        key = (provider_id, channel_id, programme.programme_id, programme.airing_id, programme.start)
                        programmes[key] = programme
        return GuideSnapshot(
            source_id, day, "available", tuple(channels), tuple(programmes.values()), truncated, rejected
        )
