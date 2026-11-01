"""Closing a returned stream must release ownership even before iteration."""
import asyncio

import httpx
import pytest

from app.proxy.stream_proxy import StreamProxy
from app.services.errors import Conflict


async def test_unstarted_http_iterator_close_releases_provider_slot(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"fixture"))
    proxy = StreamProxy(lambda **kw: httpx.AsyncClient(transport=transport, **kw), tmp_path)
    _, _, stream = await proxy.passthrough("https://fixture.invalid/video", {}, "source", max_connections=1)
    assert proxy._source_use("source") == 1
    await stream.aclose()
    assert proxy._source_use("source") == 0
    _, _, replacement = await proxy.passthrough("https://fixture.invalid/video", {}, "source", max_connections=1)
    await replacement.aclose()
    assert proxy._source_use("source") == 0


async def test_unstarted_shared_iterator_close_removes_subscriber(tmp_path):
    class Blocked(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b""

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Blocked()))
    proxy = StreamProxy(lambda **kw: httpx.AsyncClient(transport=transport, **kw), tmp_path)
    try:
        _, stream = await proxy.live_shared("channel", "https://fixture.invalid/live", {}, "source", max_connections=1)
        up = proxy._shared["channel"]
        assert up.subscribers == 1
        await stream.aclose()
        assert up.subscribers == 0
        # Keep counting the producer during its normal bounded shutdown linger.
        with pytest.raises(Conflict):
            await proxy.passthrough("https://fixture.invalid/other", {}, "source", max_connections=1)
    finally:
        proxy.pause()
        await proxy.stop_owned()


async def test_iteration_error_closes_http_once_and_close_is_idempotent(tmp_path):
    from unittest.mock import AsyncMock

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            raise RuntimeError("fixture body failure")

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Broken()))
    client = httpx.AsyncClient(transport=transport)
    client.aclose = AsyncMock(wraps=client.aclose)
    proxy = StreamProxy(lambda **kw: client, tmp_path)
    _, _, stream = await proxy.passthrough("https://fixture.invalid/video", {}, "source", max_connections=1)
    with pytest.raises(RuntimeError, match="fixture body failure"):
        _ = [chunk async for chunk in stream]
    assert proxy._source_use("source") == 0
    await stream.aclose()
    await stream.aclose()
    assert client.aclose.await_count == 1


async def test_cancelled_http_read_closes_upstream_and_releases_budget(tmp_path):
    entered = asyncio.Event()

    class Blocked(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Blocked()))
    proxy = StreamProxy(lambda **kw: httpx.AsyncClient(transport=transport, **kw), tmp_path)
    _, _, stream = await proxy.passthrough("https://fixture.invalid/video", {}, "source", max_connections=1)
    task = asyncio.create_task(anext(stream))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proxy._source_use("source") == 0
    await stream.aclose()


async def test_cancelled_shared_read_removes_only_owned_subscriber(tmp_path):
    class Blocked(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b""

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Blocked()))
    proxy = StreamProxy(lambda **kw: httpx.AsyncClient(transport=transport, **kw), tmp_path)
    try:
        _, first = await proxy.live_shared("channel", "https://fixture.invalid/live", {}, "source", max_connections=1)
        _, second = await proxy.live_shared("channel", "https://fixture.invalid/live", {}, "source", max_connections=1)
        task = asyncio.create_task(anext(first))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert proxy._shared["channel"].subscribers == 1
        assert proxy._source_use("source") == 1
        await first.aclose()
        await second.aclose()
        assert proxy._shared["channel"].subscribers == 0
        assert not proxy._viewers
    finally:
        proxy.pause()
        await proxy.stop_owned()


async def test_failed_http_close_retains_slot_until_successful_retry(tmp_path):
    from unittest.mock import AsyncMock

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"fixture"))
    client = httpx.AsyncClient(transport=transport)
    real_close = client.aclose
    client.aclose = AsyncMock(side_effect=OSError("fixture close failure"))
    proxy = StreamProxy(lambda **kw: client, tmp_path)
    _, _, stream = await proxy.passthrough("https://fixture.invalid/video", {}, "source", max_connections=1)
    with pytest.raises(OSError, match="fixture close failure"):
        await stream.aclose()
    assert proxy._source_use("source") == 1
    with pytest.raises(Conflict):
        await proxy.passthrough("https://fixture.invalid/other", {}, "source", max_connections=1)
    client.aclose = real_close
    await stream.aclose()
    assert proxy._source_use("source") == 0


async def test_response_start_disconnect_closes_unstarted_owned_stream(tmp_path):
    from starlette.requests import ClientDisconnect

    from app.api.streaming import OwnedStreamingResponse

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"fixture"))
    proxy = StreamProxy(lambda **kw: httpx.AsyncClient(transport=transport, **kw), tmp_path)
    _, _, stream = await proxy.passthrough("https://fixture.invalid/video", {}, "source", max_connections=1)

    async def guard():
        async for chunk in stream:
            yield chunk

    async def send(message):
        assert message["type"] == "http.response.start"
        raise OSError("fixture client disconnected before body")

    async def receive():
        raise AssertionError("ASGI 2.4 does not need a disconnect listener")

    response = OwnedStreamingResponse(guard(), owned_stream=stream)
    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert proxy._source_use("source") == 0


async def test_response_start_cancellation_closes_shared_subscription(tmp_path):
    from app.api.streaming import OwnedStreamingResponse

    class Blocked(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b""

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Blocked()))
    proxy = StreamProxy(lambda **kw: httpx.AsyncClient(transport=transport, **kw), tmp_path)
    try:
        _, stream = await proxy.live_shared("channel", "https://fixture.invalid/live", {}, "source", max_connections=1)
        started = asyncio.Event()

        async def guard():
            async for chunk in stream:
                yield chunk

        async def send(message):
            started.set()
            await asyncio.Event().wait()

        async def receive():
            raise AssertionError("ASGI 2.4 does not need a disconnect listener")

        response = OwnedStreamingResponse(guard(), owned_stream=stream)
        task = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert proxy._shared["channel"].subscribers == 0
    finally:
        proxy.pause()
        await proxy.stop_owned()
