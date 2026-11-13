"""Release an acquired producer even if ASGI disconnects before body iteration.

Also: one local file as a ranged body (``file_range``), for files that are served through a
stream guard rather than handed to ``FileResponse``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


class OwnedStreamingResponse(StreamingResponse):
    def __init__(self, content: AsyncIterator[bytes], *, owned_stream: AsyncIterator[bytes], **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self.owned_stream = owned_stream

    async def _close_streams(self) -> None:
        try:
            close_body = getattr(self.body_iterator, "aclose", None)
            if close_body is not None:
                await close_body()
        finally:
            close_owned = getattr(self.owned_stream, "aclose", None)
            if close_owned is not None:
                await close_owned()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Cancellation must not leave the producer's connection budget held.
            # Failed close retains its slot for explicit recovery/retry.
            await asyncio.shield(self._close_streams())


FILE_CHUNK = 64 * 1024


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """First and last byte a ``Range`` header asks for. ``None``: not satisfiable. No header: the whole file."""
    if not header:
        return (0, size - 1) if size > 0 else None
    unit, _, spec = header.partition("=")
    first, dash, last = spec.split(",", 1)[0].strip().partition("-")
    if unit.strip().lower() != "bytes" or not dash:
        return None
    try:
        if not first:  # the last N bytes
            count = int(last)
            return (max(0, size - count), size - 1) if count > 0 and size > 0 else None
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    except ValueError:
        return None
    return (start, end) if 0 <= start <= end else None


async def file_range(path: Path, start: int, end: int) -> AsyncIterator[bytes]:
    """Bytes ``start`` to ``end`` (inclusive) of a file, in chunks."""
    async with await anyio.open_file(path, "rb") as f:
        await f.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = await f.read(min(FILE_CHUNK, left))
            if not chunk:
                return
            left -= len(chunk)
            yield chunk
