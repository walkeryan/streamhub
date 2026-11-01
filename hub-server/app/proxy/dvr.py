"""Opt-in single-producer DVR on a verified dedicated size-limited mount.

The kernel filesystem capacity is the hard bound, including FFmpeg's unsealed
files. Polling is only retention/early-stop policy, never the hard quota.
"""

from __future__ import annotations

import math
import os
import re
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.proxy.rolling_buffer import GIB, ViewerLease
from app.services.errors import Conflict, Forbidden, Gone, ServiceError

MAX_VOLUME = 12 * GIB
PARENT_RESERVE = 5 * GIB
HEADROOM = 256 * 1024**2


@dataclass(frozen=True)
class VolumeProof:
    capacity: int
    available: int
    parent_free: int


def verify_volume(path: Path, reserve_path: Path | None = None) -> VolumeProof:
    """Read-only fail-closed proof; never creates, mounts or configures storage."""
    if not path.is_absolute() or not path.is_dir() or path.is_symlink() or not os.path.ismount(path):
        raise Conflict("DVR requires its dedicated mounted filesystem.", code="buffer_storage_unverified")
    if path.stat().st_dev == path.parent.stat().st_dev:
        raise Conflict("DVR must use a separate filesystem from its parent.", code="buffer_storage_unverified")
    if reserve_path is None or not reserve_path.is_absolute() or not reserve_path.is_dir():
        raise Conflict("DVR requires an explicit parent-pool reserve path.", code="buffer_storage_unverified")
    if reserve_path.stat().st_dev == path.stat().st_dev:
        raise Conflict(
            "Reserve path must measure the parent pool, not the capped DVR volume.", code="buffer_storage_unverified"
        )
    fs = os.statvfs(path)
    proof = VolumeProof(fs.f_blocks * fs.f_frsize, fs.f_bavail * fs.f_frsize, shutil.disk_usage(reserve_path).free)
    if not 11 * GIB <= proof.capacity <= MAX_VOLUME:
        raise Conflict("DVR filesystem capacity must be capped at twelve GiB.", code="buffer_storage_unverified")
    return proof


@dataclass(frozen=True)
class DvrSegment:
    sequence: int
    name: str
    start: datetime
    end: datetime
    size: int


class DvrManager:
    def __init__(
        self,
        root: Path | None = None,
        *,
        enabled: bool = False,
        verify: Callable[[Path], VolumeProof] | None = None,
        reserve_root: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.root, self.enabled, self.clock, self.now = root, enabled, clock, now
        self.verify = verify or (lambda path: verify_volume(path, reserve_root))
        self.key: str | None = None
        self.directory: Path | None = None
        self.leases: dict[str, ViewerLease] = {}
        self.segments: list[DvrSegment] = []
        self.ever_leased = False
        self.pending = 0

    def reserve(self, key: str, sid: str) -> Path:
        if not self.enabled or self.root is None:
            raise Conflict("Local recording is disabled.", code="recording_disabled")
        proof = self.verify(self.root)
        if proof.parent_free < PARENT_RESERVE or proof.available < HEADROOM:
            raise Conflict("DVR storage reserve unavailable.", code="buffer_storage_full")
        if self.key is not None and self.key != key:
            raise Conflict("The one-channel DVR is occupied.", code="buffer_busy")
        if self.directory is None:
            # Refuse unknown/orphan content. No broad startup deletion on this mount.
            if any(self.root.iterdir()):
                raise Conflict("DVR volume needs owned-orphan reconciliation.", code="buffer_storage_unverified")
            directory = self.root / ("dvr-" + sid)
            directory.mkdir(mode=0o700)
            self.key, self.directory = key, directory
        return self.directory

    def join(self, key: str, profile: str) -> str:
        if key != self.key or self.directory is None:
            raise Gone("Buffered session is unavailable.", code="buffer_expired")
        self.expire()
        if len(self.leases) >= 16:
            raise Conflict("DVR viewer limit reached.", code="buffer_busy")
        lease = secrets.token_urlsafe(24)
        self.leases[lease] = ViewerLease(profile, self.clock() + 90)
        self.ever_leased = True
        return lease

    def check(self, key: str, profile: str, lease: str) -> ViewerLease:
        value = self.leases.get(lease)
        if key != self.key or value is None or value.profile != profile:
            raise Forbidden("Invalid DVR viewer lease.")
        if value.expires <= self.clock():
            raise Gone("DVR viewer lease expired.", code="buffer_lease_expired")
        return value

    def heartbeat(self, key: str, profile: str, lease: str, paused: bool) -> None:
        value = self.check(key, profile, lease)
        now = self.clock()
        if paused:
            if value.paused_since is None:
                value.paused_since = now
            value.expires = min(now + 90, value.paused_since + 3600)
        else:
            value.paused_since = None
            value.expires = now + 90
        if value.expires <= now:
            raise Gone("Maximum paused duration reached.", code="buffer_pause_expired")

    def leave(self, key: str, profile: str, lease: str) -> None:
        if lease not in self.leases:
            return
        self.check(key, profile, lease)
        del self.leases[lease]

    def expire(self) -> None:
        for lease, value in list(self.leases.items()):
            if value.expires <= self.clock():
                del self.leases[lease]

    def held(self, key: str) -> bool:
        self.expire()
        return self.key == key and (bool(self.leases) or self.pending > 0)

    def refresh(self, key: str) -> None:
        if key != self.key or self.directory is None or self.root is None:
            raise Gone("Buffered session is unavailable.", code="buffer_expired")
        proof = self.verify(self.root)
        if proof.parent_free < PARENT_RESERVE or proof.available < HEADROOM // 2:
            raise Conflict("DVR storage reserve reached.", code="buffer_storage_full")
        playlist = self.directory / "index.m3u8"
        if not playlist.exists():
            self.segments = []
            return
        if playlist.stat().st_size > 2 * 1024**2:
            raise ServiceError("DVR manifest exceeds its bound.")
        start: datetime | None = None
        duration = 0.0
        segments: list[DvrSegment] = []
        cutoff = self.now() - timedelta(hours=1)
        for line in playlist.read_text().splitlines():
            if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
                start = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
                if start.tzinfo is None:
                    raise ServiceError("DVR timestamps require an explicit offset.")
                start = start.astimezone(UTC)
            elif line.startswith("#EXTINF:"):
                duration = float(line.split(":", 1)[1].split(",")[0])
            elif line and not line.startswith("#"):
                match = re.fullmatch(r"seg_(\d+)\.ts", line)
                if match is None or start is None or not 0 < duration <= 60:
                    raise ServiceError("DVR producer supplied an unsupported segment timeline.")
                end = start + timedelta(seconds=duration)
                path = self.directory / line
                if path.is_symlink():
                    raise ServiceError("DVR segment cannot be a symbolic link.")
                if start < cutoff:
                    path.unlink(missing_ok=True)
                elif path.is_file():
                    size = path.stat().st_size
                    if size > 64 * 1024**2 or end > self.now() + timedelta(seconds=2):
                        raise ServiceError("DVR segment exceeds its bounds.")
                    if segments and start < segments[-1].end:
                        raise ServiceError("DVR timeline reset requires a new session.")
                    segments.append(DvrSegment(int(match.group(1)), line, start, end, size))
                start, duration = end, 0
        used = sum(segment.size for segment in segments)
        while segments and (used > proof.capacity - HEADROOM or len(segments) > 1800):
            old = segments.pop(0)
            (self.directory / old.name).unlink(missing_ok=True)
            used -= old.size
        self.segments = segments

    def window(self, key: str) -> dict[str, Any]:
        self.refresh(key)
        return {
            "mode": "local_buffer",
            "start": self.segments[0].start if self.segments else None,
            "end": self.segments[-1].end if self.segments else None,
            "buffered_seconds": sum((s.end - s.start).total_seconds() for s in self.segments),
            "target_seconds": 3600,
            "recording_enabled": True,
        }

    def manifest(self, key: str, at: datetime | None = None) -> str:
        self.refresh(key)
        segments = self.segments
        offset = None
        if at is not None:
            if at.tzinfo is None:
                raise ServiceError("Seek time requires an explicit offset.")
            index = next((i for i, s in enumerate(segments) if s.start <= at < s.end), None)
            if index is None:
                raise Gone("Buffered position expired or is unavailable. Go Live.", code="buffer_expired")
            segments = segments[index:]
            offset = (at - segments[0].start).total_seconds()
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:6",
            f"#EXT-X-TARGETDURATION:{math.ceil(max(((s.end - s.start).total_seconds() for s in segments), default=2))}",
            f"#EXT-X-MEDIA-SEQUENCE:{segments[0].sequence if segments else 0}",
        ]
        if offset is not None:
            lines.append(f"#EXT-X-START:TIME-OFFSET={offset:.3f},PRECISE=YES")
        previous = None
        for segment in segments:
            if previous and previous.end != segment.start:
                lines.append("#EXT-X-DISCONTINUITY")
            lines += [
                f"#EXT-X-PROGRAM-DATE-TIME:{segment.start.isoformat()}",
                f"#EXTINF:{(segment.end - segment.start).total_seconds():.3f},",
                segment.name,
            ]
            previous = segment
        return "\n".join(lines) + "\n"

    def stopped(self, key: str) -> None:
        if key == self.key:
            # Producer termination and owned-directory cleanup belong to StreamProxy.
            self.key = self.directory = None
            self.leases.clear()
            self.segments.clear()
            self.ever_leased = False
