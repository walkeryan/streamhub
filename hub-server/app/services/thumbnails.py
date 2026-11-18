"""Scrub previews: one frame of a title's BIF preview index per position.

A library server that generated preview thumbnails keeps them per file as one BIF file
(``/library/parts/{id}/indexes/sd`` on Plex): a header, an index of (timestamp, offset) pairs and
the JPEG frames back to back. The hub fetches it once, keeps a few in memory, and answers
``GET /thumbnails/{token}?ms=<position>`` with the nearest frame.

The token is an artwork grant (``image_grants``): signed, the same for every viewer, with a weekly
expiry. Whoever may play a title has been checked already when the descriptor carrying the URL was
made; a frame is a still of the picture, treated like any other artwork.
"""

from __future__ import annotations

import asyncio
import bisect
import itertools
import logging
import struct
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.domain.ports import ImageAuthProvider
from app.services.errors import BadGateway, NotFound
from app.services.image_grants import image_payload, validate_image_grant

log = logging.getLogger(__name__)

BIF_MAGIC = b"\x89BIF\r\n\x1a\n"
HEADER_SIZE = 64
# Frames are this far apart when the index is not in the cache yet to say (Plex's default spacing).
DEFAULT_INTERVAL_MS = 2000
# A BIF file is a few MB for an episode; a long film at a dense interval can be tens of MB.
MAX_BIF_BYTES = 96 * 1024 * 1024
CACHED_INDEXES = 6
# A failed fetch is not repeated on every scrub.
FAILURE_RETRY_S = 60.0


class BifError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Bif:
    """A parsed preview index. ``times_ms[i]`` is where frame ``i`` (``data[starts[i]:ends[i]]``) is."""

    interval_ms: int
    times_ms: list[int]
    starts: list[int]
    ends: list[int]
    data: bytes

    def __len__(self) -> int:
        return len(self.times_ms)

    def frame(self, position_ms: int) -> bytes:
        """The frame at or nearest before ``position_ms`` (the first one before it starts)."""
        if not self.times_ms:
            raise BifError("no frames")
        i = max(0, bisect.bisect_right(self.times_ms, max(0, position_ms)) - 1)
        return self.data[self.starts[i] : self.ends[i]]


def parse_bif(data: bytes) -> Bif:
    """Reads a BIF file (Roku's format, which Plex writes).

    Header (64 bytes, little endian): magic, version, frame count, then the timestamp multiplier
    in ms (0 means 1000). The index that follows has one (timestamp, offset) pair per frame and a
    closing pair whose timestamp is 0xFFFFFFFF and whose offset is the end of the last frame.
    """
    if len(data) < HEADER_SIZE + 8 or not data.startswith(BIF_MAGIC):
        raise BifError("not a BIF file")
    _version, count, multiplier = struct.unpack_from("<III", data, 8)
    multiplier = multiplier or 1000
    index_end = HEADER_SIZE + 8 * (count + 1)
    if count == 0 or index_end > len(data):
        raise BifError("BIF index is truncated")
    pairs = [struct.unpack_from("<II", data, HEADER_SIZE + 8 * i) for i in range(count + 1)]
    times: list[int] = []
    starts: list[int] = []
    ends: list[int] = []
    for (stamp, offset), (_next_stamp, next_offset) in itertools.pairwise(pairs):
        if stamp == 0xFFFFFFFF:
            break
        if not index_end <= offset <= next_offset <= len(data):
            raise BifError("BIF frame lies outside the file")
        if times and stamp * multiplier < times[-1]:
            raise BifError("BIF index is out of order")
        times.append(stamp * multiplier)
        starts.append(offset)
        ends.append(next_offset)
    if not times:
        raise BifError("no frames")
    return Bif(interval_ms=_spacing(times, multiplier), times_ms=times, starts=starts, ends=ends, data=data)


def _spacing(times: list[int], fallback: int) -> int:
    gaps = [b - a for a, b in itertools.pairwise(times) if b > a]
    return min(gaps) if gaps else fallback


class ThumbnailService:
    """Signs scrub-preview URLs and serves their frames from a small in-memory cache of BIF files."""

    def __init__(self, crypto: Any, sources: Any, http_factory: Any) -> None:
        self._crypto = crypto
        self._sources = sources
        self._http = http_factory
        self._cache: OrderedDict[tuple[str, str], Bif] = OrderedDict()
        self._loading: dict[tuple[str, str], asyncio.Task[Bif]] = {}
        self._failed: dict[tuple[str, str], float] = {}
        self._background: set[asyncio.Task[Any]] = set()

    def descriptor(self, source_id: str, url: str, absolute: Any) -> dict[str, Any]:
        """The ``thumbnails`` field of a descriptor, and the index fetched ahead so the first scrub is quick."""
        key = (source_id, url)
        token = self._crypto.sign_json(image_payload(source_id, url))
        cached = self._cache.get(key)
        if cached is None and key not in self._loading:
            task = asyncio.ensure_future(self._quietly(key))
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        return {
            "url": absolute(f"/api/v1/thumbnails/{token}"),
            "interval_ms": cached.interval_ms if cached is not None else DEFAULT_INTERVAL_MS,
        }

    async def _quietly(self, key: tuple[str, str]) -> None:
        try:
            await self.index(*key)
        except Exception as exc:
            log.info("preview index not prefetched", extra={"source": key[0], "err": type(exc).__name__})

    async def frame(self, token: str, position_ms: int) -> tuple[bytes, dict[str, Any]]:
        """The JPEG at ``position_ms`` of the index ``token`` grants, and the grant (for its cache lifetime)."""
        payload = self._crypto.verify_json(token)
        if not isinstance(payload, dict):
            raise NotFound("thumbnail not found")
        await validate_image_grant(payload)
        bif = await self.index(str(payload.get("s")), str(payload.get("u") or ""))
        return bif.frame(position_ms), payload

    async def index(self, source_id: str, url: str) -> Bif:
        key = (source_id, url)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        if time.monotonic() - self._failed.get(key, -FAILURE_RETRY_S) < FAILURE_RETRY_S:
            raise NotFound("thumbnail not available")
        task = self._loading.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch(source_id, url))
            self._loading[key] = task
        try:
            bif = await asyncio.shield(task)
        except Exception:
            self._failed[key] = time.monotonic()
            raise
        finally:
            if task.done():
                self._loading.pop(key, None)
        self._failed.pop(key, None)
        self._cache[key] = bif
        self._cache.move_to_end(key)
        while len(self._cache) > CACHED_INDEXES:
            self._cache.popitem(last=False)
        return bif

    async def _fetch(self, source_id: str, url: str) -> Bif:
        _src, prov = await self._sources.provider_for(source_id)
        # Only an address of the source's own server, fetched with its credential, never a foreign URL.
        headers = prov.image_headers(url) if isinstance(prov, ImageAuthProvider) else None
        if headers is None:
            raise NotFound("thumbnail not found")
        try:
            async with self._http(timeout=30.0, headers={**headers, "Accept": "*/*"}) as client:
                resp = await client.get(url)
        except Exception as exc:
            raise BadGateway("preview index unavailable") from exc
        if resp.status_code >= 400:
            raise NotFound("thumbnail not found")
        if len(resp.content) > MAX_BIF_BYTES:
            raise NotFound("preview index too large")
        try:
            return parse_bif(resp.content)
        except BifError as exc:
            raise NotFound("thumbnail not found") from exc
