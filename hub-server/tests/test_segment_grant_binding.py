"""Segments cannot be replayed under a different content/profile grant."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.requests import Request

from app.api.routers.playback import _proxy_url, stream_segment
from app.infra.crypto import Crypto
from app.services.errors import Forbidden, Unauthorized


@pytest.fixture
def ctx() -> Any:
    grant = SimpleNamespace(headers={}, source_id="fixture", recovery_generation=0)
    return SimpleNamespace(
        crypto=Crypto("local-fixture-only-segment-key"),
        tokens=SimpleNamespace(verify=lambda _: grant, ttl_s=300),
        proxy=SimpleNamespace(ensure_running=Mock(), generation=0, passthrough=AsyncMock(), fetch_text=AsyncMock()),
        content_policy=SimpleNamespace(require_grant=AsyncMock()), auth=object(),
    )


def request() -> Request:
    return Request({"type": "http", "headers": []})


@pytest.mark.parametrize("payload", [None, {"u": "https://fixture.invalid/video.ts"}, {"u": "x", "grant": 42}])
async def test_unbound_or_malformed_rejected(ctx: Any, payload: Any) -> None:
    with pytest.raises(Unauthorized):
        await stream_segment("child", ctx.crypto.encrypt_json(payload), request(), ctx)
    ctx.proxy.passthrough.assert_not_called()
    ctx.proxy.fetch_text.assert_not_called()


async def test_cannot_move_between_valid_grants(ctx: Any) -> None:
    sub = _proxy_url("adult", "https://fixture.invalid/video.ts", ctx).rsplit("/", 1)[1]
    with pytest.raises(Unauthorized):
        await stream_segment("child", sub, request(), ctx)
    ctx.proxy.passthrough.assert_not_called()
    ctx.proxy.fetch_text.assert_not_called()


async def test_matching_segment_guarded_during_body(ctx: Any) -> None:
    async def body():
        yield b"first"
        yield b"revoked"

    async def guard(grant: Any, auth: Any, upstream: Any):
        async for chunk in upstream:
            if chunk == b"revoked":
                raise Forbidden("session revoked")
            yield chunk

    ctx.content_policy.guard_stream = Mock(side_effect=guard)
    ctx.proxy.passthrough.return_value = (200, {}, body())
    sub = _proxy_url("original", "https://fixture.invalid/video.ts", ctx).rsplit("/", 1)[1]
    response = await stream_segment("original", sub, request(), ctx)
    iterator = response.body_iterator
    assert await anext(iterator) == b"first"
    with pytest.raises(Forbidden):
        await anext(iterator)
    ctx.content_policy.guard_stream.assert_called_once()


async def test_nested_playlist_retains_parent_binding(ctx: Any) -> None:
    ctx.proxy.fetch_text.return_value = ("https://fixture.invalid/nested.m3u8", "#EXTM3U")
    ctx.proxy.rewrite_playlist = lambda text, final, rewrite: rewrite("https://fixture.invalid/part.ts")
    sub = _proxy_url("original", "https://fixture.invalid/nested.m3u8", ctx).rsplit("/", 1)[1]
    response = await stream_segment("original", sub, request(), ctx)
    nested = bytes(response.body).decode().rsplit("/", 1)[1]
    assert ctx.crypto.decrypt_json(nested) == {
        "u": "https://fixture.invalid/part.ts", "grant": hashlib.sha256(b"original").hexdigest(),
    }
    with pytest.raises(Unauthorized):
        await stream_segment("another", nested, request(), ctx)
    ctx.proxy.passthrough.assert_not_called()
