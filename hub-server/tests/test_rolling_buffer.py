from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.proxy.rolling_buffer import BufferLimits, RollingBufferStore
from app.services.errors import Conflict, Forbidden, Gone, ServiceError


class Clock:
    mono = 0.0
    now = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)

    def tick(self, seconds):
        self.mono += seconds
        self.now += timedelta(seconds=seconds)


async def chunks(*values):
    for value in values:
        yield value


def store(tmp_path, clock, **limits):
    config = dict(enabled=True, quota_bytes=100, reserve_bytes=20, max_segment_bytes=60)
    config.update(limits)
    return RollingBufferStore(
        tmp_path / "owned",
        BufferLimits(**config),
        clock=lambda: clock.mono,
        utcnow=lambda: clock.now,
        free_bytes=lambda _: 10000,
    )


async def append(buffer, clock, size=30):
    return await buffer.append("channel", clock.now - timedelta(seconds=2), clock.now, chunks(b"x" * size))


async def test_disabled_creates_nothing_and_one_channel_shared_viewers(tmp_path):
    disabled = RollingBufferStore(tmp_path / "off")
    with pytest.raises(Conflict, match="disabled"):
        await disabled.join("channel", "a")
    assert not (tmp_path / "off").exists()
    clock = Clock()
    buffer = store(tmp_path, clock)
    a, b = await buffer.join("channel", "a"), await buffer.join("channel", "b")
    with pytest.raises(Conflict):
        await buffer.join("other", "a")
    assert (await buffer.snapshot("a", a))["buffered_seconds"] == 0
    with pytest.raises(Forbidden):
        await buffer.snapshot("b", a)
    segment = await append(buffer, clock)
    await buffer.leave("a", a)
    assert segment.path.exists()
    assert await buffer.seek("b", b, segment.start) == segment
    await buffer.leave("b", b)
    await buffer.leave("b", b)
    assert buffer.channel is None and not segment.path.exists()


async def test_quota_retention_real_window_and_no_gap_fabrication(tmp_path):
    clock = Clock()
    buffer = store(tmp_path, clock, retention_seconds=10)
    viewer = await buffer.join("channel", "a")
    oldest = await append(buffer, clock, 60)
    clock.tick(3)
    newest = await append(buffer, clock, 60)
    assert not oldest.path.exists() and buffer.used_bytes == 60
    assert (await buffer.snapshot("a", viewer))["buffered_seconds"] == 2
    with pytest.raises(Gone):
        await buffer.seek("a", viewer, oldest.start)
    clock.tick(3)
    other = await append(buffer, clock, 20)
    with pytest.raises(Gone):
        await buffer.seek("a", viewer, newest.end + timedelta(milliseconds=100))
    assert await buffer.seek("a", viewer, other.start) == other
    clock.tick(11)
    await buffer.reap()
    assert (await buffer.snapshot("a", viewer))["segments"] == 0
    assert buffer.used_bytes == 0


async def test_partial_cancellation_oversize_reserve_and_empty_cleanup(tmp_path):
    clock = Clock()
    buffer = store(tmp_path, clock)
    await buffer.join("channel", "a")
    entered = asyncio.Event()

    async def slow():
        yield b"123"
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(buffer.append("channel", clock.now - timedelta(seconds=2), clock.now, slow()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(buffer.directory.iterdir()) and buffer.used_bytes == 0
    for data in [chunks(b"x" * 61), chunks()]:
        with pytest.raises(ServiceError):
            await buffer.append("channel", clock.now - timedelta(seconds=2), clock.now, data)
    buffer.free_bytes = lambda _: 25
    with pytest.raises(Conflict):
        await append(buffer, clock, 10)
    assert not list(buffer.directory.iterdir()) and buffer.used_bytes == 0


async def test_expired_and_pause_leases_are_bounded(tmp_path):
    clock = Clock()
    buffer = store(tmp_path, clock, lease_seconds=10, max_pause_seconds=20)
    viewer = await buffer.join("channel", "a")
    segment = await append(buffer, clock)
    await buffer.heartbeat("a", viewer, paused=True)
    for _ in range(3):
        clock.tick(5)
        await buffer.heartbeat("a", viewer, paused=True)
    clock.tick(5)
    with pytest.raises(Gone):
        await buffer.heartbeat("a", viewer, paused=True)
    await buffer.reap()
    assert not segment.path.exists() and buffer.channel is None
    viewer = await buffer.join("channel", "a")
    clock.tick(11)
    with pytest.raises(Gone):
        await buffer.snapshot("a", viewer)
    await buffer.reap()
    assert buffer.channel is None


async def test_concurrent_admission_and_invalid_timestamps(tmp_path):
    clock = Clock()
    buffer = store(tmp_path, clock)
    results = await asyncio.gather(buffer.join("channel", "a"), buffer.join("other", "b"), return_exceptions=True)
    assert sum(isinstance(result, Conflict) for result in results) == 1
    for start, end in [
        (clock.now, clock.now),
        (clock.now.replace(tzinfo=None), clock.now.replace(tzinfo=None)),
        (clock.now, clock.now + timedelta(seconds=10)),
    ]:
        with pytest.raises(ServiceError):
            await buffer.append("channel", start, end, chunks(b"x"))
    await append(buffer, clock)
    with pytest.raises(ServiceError, match="overlap"):
        await append(buffer, clock)


async def test_slow_ingest_expiry_and_owned_cleanup_only(tmp_path):
    clock = Clock()
    buffer = store(tmp_path, clock, lease_seconds=5)
    unrelated = tmp_path / "preserve.txt"
    unrelated.write_text("unrelated")
    await buffer.join("channel", "a")

    async def expired():
        yield b"x"
        clock.tick(6)
        yield b"y"

    with pytest.raises(Gone):
        await buffer.append("channel", clock.now - timedelta(seconds=2), clock.now, expired())
    assert unrelated.read_text() == "unrelated"
    assert buffer.channel is None and not list(buffer.root.iterdir())


async def test_hour_is_not_claimed_before_ingestion_and_manifest_preserves_gaps(tmp_path):
    clock = Clock()
    buffer = store(tmp_path, clock)
    viewer = await buffer.join("channel", "a")
    assert (await buffer.snapshot("a", viewer))["start"] is None
    for i in range(60):
        if i:
            clock.tick(60)
            await buffer.heartbeat("a", viewer)
        await buffer.append("channel", clock.now - timedelta(seconds=60), clock.now, chunks(b"x"))
        assert (await buffer.snapshot("a", viewer))["buffered_seconds"] == (i + 1) * 60
    manifest = await buffer.manifest("a", viewer)
    assert manifest.count("#EXTINF:") == 60
    assert "#EXT-X-PROGRAM-DATE-TIME:" in manifest and "#EXT-X-ENDLIST" not in manifest
    clock.tick(5)
    await append(buffer, clock, 1)
    manifest = await buffer.manifest("a", viewer)
    assert "#EXT-X-DISCONTINUITY" in manifest
    assert (await buffer.snapshot("a", viewer))["buffered_seconds"] < 3600


async def test_viewer_and_ingest_timeout_limits(tmp_path):
    clock = Clock()
    buffer = store(tmp_path, clock, max_viewers=1, segment_timeout_seconds=0.01)
    await buffer.join("channel", "a")
    with pytest.raises(Conflict):
        await buffer.join("channel", "b")

    async def stalled():
        yield b"x"
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await buffer.append("channel", clock.now - timedelta(seconds=2), clock.now, stalled())
    assert buffer.used_bytes == 0 and not list(buffer.directory.iterdir())


async def test_cleanup_failure_does_not_admit_another_channel(tmp_path, monkeypatch):
    import shutil

    clock = Clock()
    buffer = store(tmp_path, clock)
    viewer = await buffer.join("channel", "a")
    segment = await append(buffer, clock)
    real = shutil.rmtree

    def fail(_):
        raise OSError("fixture cleanup failure")

    monkeypatch.setattr(shutil, "rmtree", fail)
    with pytest.raises(OSError):
        await buffer.leave("a", viewer)
    assert buffer.channel == "channel" and segment.path.exists()
    with pytest.raises(OSError):
        await buffer.join("other", "b")
    monkeypatch.setattr(shutil, "rmtree", real)
    await buffer.reap()
    assert buffer.channel is None and not segment.path.exists()
