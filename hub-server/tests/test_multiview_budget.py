import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routers.playback import _start_hls
from app.proxy.stream_proxy import HttpSession
from app.services.errors import Conflict, Forbidden
from tests.test_hls_admission import harness as base_harness
from tests.test_hls_admission import start

harness = base_harness


async def reserve(proxy, maximum=2, profile="p", session="session"):
    return await proxy.reserve_pair(
        profile, session, {"one": ("source-a", maximum), "two": ("source-a", maximum)}, generation=proxy.generation
    )


async def pair_start(proxy, key, lease):
    return await proxy.hls_session(
        key,
        "https://fixture.invalid/live",
        {},
        live=True,
        transcode="none",
        source_id="source-a",
        max_connections=2,
        pair_lease_id=lease,
    )


async def test_capacity_one_and_unknown_never_start_either_pane(harness):
    proxy, _, processes, _ = harness
    for maximum in (1, None, 0):
        with pytest.raises(Conflict):
            await reserve(proxy, maximum)
        assert proxy._source_use("source-a") == 0
        assert not processes


async def test_pair_atomic_reservation_competes_across_all_modes(harness):
    proxy, _, processes, _ = harness
    lease = await reserve(proxy)
    assert proxy._source_use("source-a") == 2 and not processes
    with pytest.raises(Conflict):
        await reserve(proxy)
    # No client is even constructed on rejected admission.
    with pytest.raises(Conflict):
        await proxy.passthrough("https://fixture.invalid", {}, "source-a", max_connections=2)
    with pytest.raises(Conflict):
        await proxy.fetch_text("https://fixture.invalid", {}, source_id="source-a", max_connections=2)
    with pytest.raises(Conflict):
        await proxy.live_shared("other", "https://fixture.invalid", {}, "source-a", max_connections=2)
    first = await pair_start(proxy, "one", lease)
    assert await pair_start(proxy, "one", lease) is first
    await pair_start(proxy, "two", lease)
    assert len(processes) == proxy._source_use("source-a") == 2
    await proxy.release_pair(lease, "p", "session")
    assert not proxy._pairs and proxy._source_use("source-a") == 0
    assert all(p.stopped for p in processes)


async def test_other_session_wrong_key_and_stale_generation_rejected(harness):
    proxy, _, _, _ = harness
    lease = await reserve(proxy)
    for profile, session, key in [("other", "session", "one"), ("p", "other", "one"), ("p", "session", "foreign")]:
        with pytest.raises(Forbidden):
            await proxy.validate_pair(lease, key, profile, session)
    with pytest.raises(Forbidden):
        await proxy.release_pair(lease, "p", "other")
    proxy.pause()
    with pytest.raises(Forbidden):
        await pair_start(proxy, "one", lease)
    await proxy.stop_owned()
    assert not proxy._pairs
    proxy.paused = False
    with pytest.raises(Forbidden):
        await pair_start(proxy, "one", lease)


async def test_expiry_stops_owned_producers_before_readmission(harness):
    proxy, clock, processes, _ = harness
    lease = await reserve(proxy)
    await pair_start(proxy, "one", lease)
    clock.now += 61
    with pytest.raises(Forbidden):
        await proxy.validate_pair(lease, "one", "p", "session")
    replacement = await reserve(proxy)
    assert replacement != lease and processes[0].stopped
    await proxy.release_pair(replacement, "p", "session")


async def test_pair_cannot_take_dvr_or_existing_hls_slot(harness):
    proxy, _, _, _ = harness
    await start(proxy, "one", maximum=2)
    with pytest.raises(Conflict):
        await reserve(proxy)
    assert proxy._source_use("source-a") == 1
    await proxy.shutdown()


async def test_concurrent_pair_admission_is_atomic(harness):
    proxy, _, processes, _ = harness
    results = await asyncio.gather(reserve(proxy), reserve(proxy), return_exceptions=True)
    assert sum(isinstance(r, str) for r in results) == 1
    assert sum(isinstance(r, Conflict) for r in results) == 1
    assert not processes and proxy._source_use("source-a") == 2
    await proxy.stop_owned()


async def test_partial_start_failure_releases_both_panes(harness):
    proxy, _, processes, _ = harness
    lease = await reserve(proxy)
    await pair_start(proxy, "one", lease)
    proxy.hls_session = AsyncMock(side_effect=ConnectionError("fixture failure"))
    grant = SimpleNamespace(pair_lease_id=lease, profile_id="p", session_id="session")
    with pytest.raises(ConnectionError):
        await _start_hls(SimpleNamespace(proxy=proxy), grant, "two")
    assert all(p.stopped for p in processes)
    assert not proxy._pairs and proxy._source_use("source-a") == 0


async def test_http_reservation_blocks_hls_and_closes_before_release(harness):
    proxy, _, processes, _ = harness
    client = SimpleNamespace(aclose=AsyncMock())
    sess = HttpSession(client, None, source_id="source-a")
    proxy._http.add(sess)
    with pytest.raises(Conflict):
        await reserve(proxy)
    from app.proxy.stream_proxy import HlsSourceBusy

    with pytest.raises(HlsSourceBusy):
        await start(proxy, "one")
    assert not processes
    await proxy._close_http(sess)
    assert client.aclose.await_count == 1 and proxy._source_use("source-a") == 0
    await start(proxy, "one")
    await proxy.shutdown()


async def test_failed_stop_retains_reservation_until_confirmed(harness, monkeypatch):
    proxy, _, processes, _ = harness
    lease = await reserve(proxy)
    await pair_start(proxy, "one", lease)
    original = proxy._stop_hls
    monkeypatch.setattr(proxy, "_stop_hls", AsyncMock(side_effect=TimeoutError("fixture kill timeout")))
    with pytest.raises(TimeoutError):
        await proxy.release_pair(lease, "p", "session")
    assert proxy._pairs[lease].closing and proxy._source_use("source-a") == 2
    with pytest.raises(Forbidden):
        await pair_start(proxy, "two", lease)
    assert not processes[0].stopped
    monkeypatch.setattr(proxy, "_stop_hls", original)
    await proxy.release_pair(lease, "p", "session")
    assert processes[0].stopped and proxy._source_use("source-a") == 0


async def test_http_success_failure_and_shared_viewers_count_producers(harness):
    import httpx

    proxy, _, _, _ = harness
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"fixture"))
    proxy._client_factory = lambda **kw: httpx.AsyncClient(transport=transport, **kw)
    _, _, stream = await proxy.passthrough("https://fixture.invalid", {}, "source-a", max_connections=1)
    assert proxy._source_use("source-a") == 1
    assert b"".join([chunk async for chunk in stream]) == b"fixture"
    assert proxy._source_use("source-a") == 0
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(ConnectionError):
        await proxy.fetch_text("https://fixture.invalid", {}, source_id="source-a", max_connections=1)
    assert proxy._source_use("source-a") == 0

    class Blocked(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b""

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Blocked()))
    await proxy.live_shared("channel", "https://fixture.invalid", {}, "source-a", max_connections=1)
    await proxy.live_shared("channel", "https://fixture.invalid", {}, "source-a", max_connections=1)
    assert proxy._source_use("source-a") == 1 and proxy._shared["channel"].subscribers == 2
    proxy.pause()
    await proxy.stop_owned()
    assert proxy._source_use("source-a") == 0


async def test_capacity_change_between_grant_and_start_never_opens_upstream(harness):
    proxy, _, processes, _ = harness
    lease = await reserve(proxy)
    with pytest.raises(Conflict):
        await proxy.hls_session(
            "one",
            "https://fixture.invalid/live",
            {},
            live=True,
            transcode="none",
            source_id="source-a",
            max_connections=1,
            pair_lease_id=lease,
        )
    assert not processes
    await proxy.release_pair(lease, "p", "session")
