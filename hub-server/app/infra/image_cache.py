"""Disk cache for artwork the hub fetches on behalf of clients.

One file per image, named by a hash of (source, URL) so provider URLs never
become file names. The first line of a file is the upstream content type, the
rest is the body. The directory is kept under a total-size cap by deleting the
oldest files first.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import secrets
import time
from pathlib import Path

MAX_AGE_S = 30 * 24 * 3600
EVICT_TO = 0.9  # free a little headroom so a full cache does not evict on every write


class ImageCache:
    def __init__(self, root: Path, max_bytes: int, max_age_s: float = MAX_AGE_S) -> None:
        self._root = root
        self._max_bytes = max_bytes
        self._max_age_s = max_age_s
        self._size: int | None = None  # bytes on disk, counted once and then tracked
        self._lock = asyncio.Lock()

    def _path(self, source_id: str, url: str) -> Path:
        return self._root / hashlib.sha256(f"{source_id}\n{url}".encode()).hexdigest()

    async def get(self, source_id: str, url: str) -> tuple[bytes, str] | None:
        """(body, content type), or ``None`` on a miss or an entry past its age limit."""
        if self._max_bytes <= 0:
            return None
        return await asyncio.to_thread(self._read, self._path(source_id, url))

    def _read(self, path: Path) -> tuple[bytes, str] | None:
        try:
            if time.time() - path.stat().st_mtime > self._max_age_s:
                return None
            head, _, body = path.read_bytes().partition(b"\n")
        except OSError:
            return None
        return body, head.decode("latin-1")

    async def put(self, source_id: str, url: str, body: bytes, content_type: str) -> None:
        head = content_type.replace("\n", " ").replace("\r", " ").encode("latin-1", "replace")
        data = head + b"\n" + body
        if self._max_bytes <= 0 or len(data) > self._max_bytes:
            return
        async with self._lock:
            await asyncio.to_thread(self._write, self._path(source_id, url), data)

    def _write(self, path: Path, data: bytes) -> None:
        # Best effort throughout: a full or read-only disk must not fail the image response.
        with contextlib.suppress(OSError):
            self._root.mkdir(parents=True, exist_ok=True)
            if self._size is None:
                self._size = sum(entry.stat().st_size for entry in self._entries())
            replaced = path.stat().st_size if path.exists() else 0
            partial = path.with_name(f"{path.name}.{secrets.token_hex(4)}.part")
            partial.write_bytes(data)
            os.replace(partial, path)
            self._size += len(data) - replaced
            if self._size > self._max_bytes:
                self._evict(keep=path)

    def _entries(self) -> list[Path]:
        return [entry for entry in self._root.iterdir() if entry.is_file()]

    def _evict(self, keep: Path) -> None:
        aged = sorted(
            ((entry.stat().st_mtime, entry.stat().st_size, entry) for entry in self._entries()), key=lambda e: e[0]
        )
        total = sum(size for _, size, _ in aged)
        for _, size, entry in aged:
            if total <= self._max_bytes * EVICT_TO:
                break
            if entry == keep:
                continue
            with contextlib.suppress(OSError):
                entry.unlink()
                total -= size
        self._size = total
