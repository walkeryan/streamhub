"""Default-disabled rolling segment store, not an FFmpeg recorder.

Only bounded chunk ingestion may write here. Production activation requires a
quota-backed volume and a producer adapter; direct FFmpeg disk output must never
be pointed at this store and mistaken for enforced quota.
"""

from __future__ import annotations

import asyncio
import math
import secrets
import shutil
import time
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.errors import Conflict, Forbidden, Gone, ServiceError

GIB = 1024**3


@dataclass(frozen=True)
class BufferLimits:
    enabled: bool = False
    quota_bytes: int = 12 * GIB
    reserve_bytes: int = 5 * GIB
    retention_seconds: int = 3600
    max_segment_bytes: int = 64 * 1024**2
    max_segments: int = 2000
    max_viewers: int = 16
    lease_seconds: int = 90
    max_pause_seconds: int = 3600
    segment_timeout_seconds: float = 15

    def __post_init__(self) -> None:
        if not (0 < self.quota_bytes <= 12 * GIB and self.reserve_bytes >= 0):
            raise ValueError("invalid buffer storage bounds")
        if not (0 < self.retention_seconds <= 3600 and 0 < self.max_pause_seconds <= 3600):
            raise ValueError("invalid retention bounds")
        if not (0 < self.max_segment_bytes <= self.quota_bytes and 0 < self.max_segments <= 2000):
            raise ValueError("invalid segment bounds")
        if not 0 < self.max_viewers <= 16:
            raise ValueError("invalid viewer bound")
        if not (0 < self.lease_seconds <= 90 and 0 < self.segment_timeout_seconds <= 15):
            raise ValueError("invalid lease/ingest bounds")


@dataclass
class ViewerLease:
    profile: str
    expires: float
    paused_since: float | None = None


@dataclass(frozen=True)
class Segment:
    number: int
    start: datetime
    end: datetime
    size: int
    path: Path


class RollingBufferStore:
    """One channel, shared by explicit viewers; no polling or producer is started.

    Methods are serialized so quota admission, expiry, seeks and cancellation
    cannot publish partial segments. Profile identity is required on every viewer
    operation. The caller must stop its producer before releasing the final lease.
    """

    def __init__(
        self,
        root: Path,
        limits: BufferLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
        free_bytes: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
    ) -> None:
        self.root = root
        self.limits = limits or BufferLimits()
        self.clock, self.utcnow, self.free_bytes = clock, utcnow, free_bytes
        self.lock = asyncio.Lock()
        self.channel: str | None = None
        self.directory: Path | None = None
        self.viewers: dict[str, ViewerLease] = {}
        self.segments: list[Segment] = []
        self.used_bytes = 0
        self.sequence = 0

    def _enabled(self) -> None:
        if not self.limits.enabled:
            raise Conflict("Local recording is disabled.", code="recording_disabled")

    def _viewer(self, profile: str, viewer: str) -> ViewerLease:
        lease = self.viewers.get(viewer)
        if lease is None or lease.profile != profile:
            raise Forbidden("Unknown viewer lease.")
        if lease.expires <= self.clock():
            raise Gone("Viewer lease expired.", code="buffer_lease_expired")
        return lease

    def _remove_first(self) -> None:
        segment = self.segments[0]
        segment.path.unlink(missing_ok=True)
        self.segments.pop(0)
        self.used_bytes -= segment.size

    def _prune(self) -> None:
        cutoff = self.utcnow().timestamp() - self.limits.retention_seconds
        # Evict boundary-crossing segments too: never claim more than retention.
        while self.segments and self.segments[0].start.timestamp() < cutoff:
            self._remove_first()

    def _clear(self) -> None:
        # Only this instance's unguessable session directory is owned here.
        if self.directory is not None:
            shutil.rmtree(self.directory)
        self.directory = None
        self.channel = None
        self.viewers.clear()
        self.segments.clear()
        self.used_bytes = 0

    def _expire_viewers(self) -> None:
        for viewer, lease in list(self.viewers.items()):
            if lease.expires <= self.clock():
                del self.viewers[viewer]
        if self.channel is not None and not self.viewers:
            self._clear()

    async def join(self, channel: str, profile: str) -> str:
        self._enabled()
        async with self.lock:
            self._expire_viewers()
            if self.channel is not None and self.channel != channel:
                raise Conflict("The one-channel buffer is occupied.", code="buffer_busy")
            if len(self.viewers) >= self.limits.max_viewers:
                raise Conflict("Viewer lease limit reached.", code="buffer_busy")
            if self.channel is None:
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                if self.free_bytes(self.root) <= self.limits.reserve_bytes:
                    raise Conflict("Insufficient free-space reserve.", code="buffer_storage_full")
                directory = self.root / ("buffer-" + secrets.token_hex(16))
                directory.mkdir(mode=0o700)
                self.directory, self.channel = directory, channel
            viewer = secrets.token_urlsafe(24)
            self.viewers[viewer] = ViewerLease(profile, self.clock() + self.limits.lease_seconds)
            return viewer

    async def heartbeat(self, profile: str, viewer: str, *, paused: bool = False) -> None:
        async with self.lock:
            lease = self._viewer(profile, viewer)
            now = self.clock()
            if paused:
                if lease.paused_since is None:
                    lease.paused_since = now
                expiry = min(now + self.limits.lease_seconds, lease.paused_since + self.limits.max_pause_seconds)
                if expiry <= now:
                    del self.viewers[viewer]
                    if not self.viewers:
                        self._clear()
                    raise Gone("Paused viewer lease expired.", code="buffer_pause_expired")
                lease.expires = expiry
            else:
                lease.paused_since = None
                lease.expires = now + self.limits.lease_seconds

    async def leave(self, profile: str, viewer: str) -> None:
        async with self.lock:
            lease = self.viewers.get(viewer)
            if lease is None:
                return  # Idempotent after teardown.
            if lease.profile != profile:
                raise Forbidden("Unknown viewer lease.")
            del self.viewers[viewer]
            if not self.viewers:
                self._clear()

    async def reap(self) -> None:
        async with self.lock:
            self._expire_viewers()
            self._prune()

    async def append(self, channel: str, start: datetime, end: datetime, chunks: AsyncIterable[bytes]) -> Segment:
        self._enabled()
        if start.tzinfo is None or end.tzinfo is None or not 0 < (end - start).total_seconds() <= 60:
            raise ServiceError("Segments require aware timestamps and a duration up to sixty seconds.")
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end.timestamp() > self.utcnow().timestamp() + 1:
            raise ServiceError("Cannot publish future segments.")
        async with self.lock:
            self._expire_viewers()
            if channel != self.channel or self.directory is None:
                raise Conflict("No viewer owns this channel buffer.", code="buffer_not_active")
            self._prune()
            if start.timestamp() < self.utcnow().timestamp() - self.limits.retention_seconds:
                raise Gone("Segment is outside retention.", code="buffer_expired")
            if self.segments and start < self.segments[-1].end:
                raise ServiceError("Segment timestamps overlap; begin a new buffer after timeline reset.")
            partial = self.directory / "incoming.part"
            size = 0
            try:
                async with asyncio.timeout(self.limits.segment_timeout_seconds):
                    with partial.open("xb", buffering=0) as stream:
                        async for chunk in chunks:
                            if size + len(chunk) > self.limits.max_segment_bytes:
                                raise Conflict("Segment exceeds ingest cap.", code="buffer_storage_full")
                            while self.segments and self.used_bytes + size + len(chunk) > self.limits.quota_bytes:
                                self._remove_first()
                            if (
                                self.used_bytes + size + len(chunk) > self.limits.quota_bytes
                                or self.free_bytes(self.directory) - len(chunk) < self.limits.reserve_bytes
                            ):
                                raise Conflict(
                                    "Buffer quota or free-space reserve reached.", code="buffer_storage_full"
                                )
                            if stream.write(chunk) != len(chunk):
                                raise OSError("Incomplete segment write")
                            size += len(chunk)
                if not size:
                    raise ServiceError("Empty segments are not published.")
                # A slow ingest may outlive its last viewer or retention window.
                self._expire_viewers()
                if self.directory is None:
                    raise Gone("Viewer lease ended during ingest.", code="buffer_lease_expired")
                if start.timestamp() < self.utcnow().timestamp() - self.limits.retention_seconds:
                    raise Gone("Segment expired during ingest.", code="buffer_expired")
                while len(self.segments) >= self.limits.max_segments:
                    self._remove_first()
                self.sequence += 1
                path = self.directory / f"segment-{self.sequence}.ts"
                partial.rename(path)
                segment = Segment(self.sequence, start, end, size, path)
                self.segments.append(segment)
                self.used_bytes += size
                return segment
            finally:
                partial.unlink(missing_ok=True)

    async def snapshot(self, profile: str, viewer: str) -> dict[str, Any]:
        async with self.lock:
            self._viewer(profile, viewer)
            self._prune()
            return {
                "mode": "local_buffer",
                "recording_enabled": self.limits.enabled,
                "start": self.segments[0].start if self.segments else None,
                "end": self.segments[-1].end if self.segments else None,
                "buffered_seconds": sum((s.end - s.start).total_seconds() for s in self.segments),
                "bytes": self.used_bytes,
                "segments": len(self.segments),
                "target_seconds": self.limits.retention_seconds,
            }

    async def manifest(self, profile: str, viewer: str) -> str:
        """Relative segment names only; caller must serve every segment under its auth lease."""
        async with self.lock:
            self._viewer(profile, viewer)
            self._prune()
            segments = self.segments
            duration = max(((s.end - s.start).total_seconds() for s in segments), default=2)
            lines = [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                f"#EXT-X-TARGETDURATION:{math.ceil(duration)}",
                f"#EXT-X-MEDIA-SEQUENCE:{segments[0].number if segments else self.sequence + 1}",
            ]
            previous = None
            for segment in segments:
                if previous is not None and previous.end != segment.start:
                    lines.append("#EXT-X-DISCONTINUITY")
                lines.extend(
                    [
                        f"#EXT-X-PROGRAM-DATE-TIME:{segment.start.isoformat()}",
                        f"#EXTINF:{(segment.end - segment.start).total_seconds():.3f},",
                        segment.path.name,
                    ]
                )
                previous = segment
            return "\n".join(lines) + "\n"

    async def seek(self, profile: str, viewer: str, at: datetime) -> Segment:
        if at.tzinfo is None:
            raise ServiceError("Seek time requires an explicit offset.")
        async with self.lock:
            self._viewer(profile, viewer)
            self._prune()
            for segment in self.segments:
                if segment.start <= at < segment.end:
                    return segment
            raise Gone(
                "Position is unavailable or expired. Choose the available window or Go Live.", code="buffer_expired"
            )
