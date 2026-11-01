"""Release an acquired producer even if ASGI disconnects before body iteration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

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
