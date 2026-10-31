from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.proxy.stream_proxy import HlsSourceBusy, StreamProxy
from app.services.playback import StreamGrant
from tests.conftest import Api
from tests.test_epg_integrity import add_source


class Clock:
    def __init__(self):
        self.now = 0.0
        self.poll = None

    def __call__(self):
        return self.now

    async def sleep(self, duration):
        self.now += duration
        if self.poll is not None:
            await self.poll()
        await asyncio.sleep(0)


class Process:
    def __init__(self):
        self.returncode = None
        self.stderr = asyncio.StreamReader()
        self.stopped = False

    def terminate(self):
        self.stopped = True
        self.returncode = 0
        self.stderr.feed_eof()

    def kill(self):
        self.terminate()

    async def wait(self):
        return self.returncode


@pytest.fixture
def harness(tmp_path, monkeypatch):
    clock = Clock()
    processes = []
    peaks = []

    async def spawn(*args, **kwargs):
        proc = Process()
        processes.append(proc)
        peaks.append(sum(p.returncode is None for p in processes))
        Path(args[-1]).write_text("#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nseg.ts\n")
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    proxy = StreamProxy(lambda **kwargs: None, tmp_path, clock=clock, sleep=clock.sleep)
    return proxy, clock, processes, peaks


async def start(proxy, key, source="source-a", maximum=1):
    return await proxy.hls_session(
        key, "https://provider.invalid/live", {}, live=True, transcode="none", source_id=source, max_connections=maximum
    )


async def test_same_channel_shares_without_consuming_another_slot(harness):
    proxy, _, processes, _ = harness
    first = await start(proxy, "one")
    assert await start(proxy, "one") is first
    assert len(processes) == 1
    await proxy.shutdown()


async def test_switch_waits_short_grace_then_stops_old_before_spawning(harness):
    proxy, clock, processes, peaks = harness
    old = await start(proxy, "one")
    new = await start(proxy, "two")
    assert old.proc.stopped and new is not old
    assert 5 <= clock.now <= 6
    assert max(peaks) == 1 and len(processes) == 2
    assert not old.workdir.exists()
    await proxy.shutdown()


async def test_actively_polled_other_channel_is_never_evicted(harness):
    proxy, clock, processes, _ = harness
    old = await start(proxy, "one")

    async def poll():
        # Exercises the playlist path; would deadlock if admission held its lock while waiting.
        assert await start(proxy, "one") is old

    clock.poll = poll
    with pytest.raises(HlsSourceBusy):
        await asyncio.wait_for(start(proxy, "two"), timeout=1)
    assert clock.now == 5 and not old.proc.stopped and len(processes) == 1
    await proxy.shutdown()


async def test_long_target_duration_protects_slow_polling_viewer(harness):
    proxy, clock, processes, _ = harness
    old = await start(proxy, "one")
    (old.workdir / "index.m3u8").write_text("#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nseg.ts\n")
    with pytest.raises(HlsSourceBusy):
        await start(proxy, "two")
    assert clock.now == 5 and not old.proc.stopped and len(processes) == 1
    await proxy.shutdown()


async def test_source_limits_are_independent_and_multiple_slots_work(harness):
    proxy, clock, processes, _ = harness
    a = await start(proxy, "one", maximum=2)
    b = await start(proxy, "two", maximum=2)
    c = await start(proxy, "three", source="source-b")
    assert clock.now == 0 and len(processes) == 3
    assert not any(x.proc.stopped for x in [a, b, c])
    await proxy.shutdown()


async def test_stderr_is_drained_without_retaining_sensitive_text(harness):
    proxy, _, _, _ = harness
    sess = await start(proxy, "one")
    payload = b"sensitive upstream data" * 20000
    sess.proc.stderr.feed_data(payload)
    for _ in range(4):
        await asyncio.sleep(0)
    assert sess.stderr_bytes == len(payload)
    assert not sess.proc.stderr._buffer
    assert not hasattr(sess, "stderr_tail")
    await proxy.shutdown()
    assert sess.stderr_task.done()


async def test_hls_route_passes_source_limit_and_returns_busy(api: Api, tmp_path, monkeypatch):
    source = await add_source(api)
    async with api.c.db.session() as session:
        from app.infra.models import Source

        row = await session.get(Source, source["id"])
        row.hints = {**row.hints, "max_connections": 1}
        await session.commit()
    token = api.c.tokens.issue(
        StreamGrant(api.profile_id, source["id"], "https://provider.invalid/live", {}, "hls", True, None, False)
    )
    seen = {}

    async def busy(*args, **kwargs):
        seen.update(kwargs)
        raise HlsSourceBusy()

    monkeypatch.setattr(api.c.proxy, "hls_session", busy)
    response = await api.client.get(f"/api/v1/stream/{token}/index.m3u8")
    assert response.status_code == 409 and response.json()["code"] == "source_busy"
    assert seen["source_id"] == source["id"] and seen["max_connections"] == 1


async def test_concurrent_same_channel_waits_for_shared_startup(harness, monkeypatch):
    proxy, _, processes, _ = harness
    started = asyncio.Event()
    ready = asyncio.Event()

    async def spawn(*args, **kwargs):
        proc = Process()
        processes.append(proc)
        started.set()
        return proc

    async def wait_for_playlist(_):
        await ready.wait()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    proxy._sleep = wait_for_playlist
    first = asyncio.create_task(start(proxy, "one"))
    await started.wait()
    second = asyncio.create_task(start(proxy, "one"))
    await asyncio.sleep(0)
    assert not first.done() and not second.done() and len(processes) == 1
    (proxy._hls["one"].workdir / "index.m3u8").write_text("#EXTM3U\n#EXTINF:2,\nseg.ts\n")
    ready.set()
    a, b = await asyncio.gather(first, second)
    assert a is b
    await proxy.shutdown()
