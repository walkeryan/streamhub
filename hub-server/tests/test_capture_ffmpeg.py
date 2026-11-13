"""Capture against the real ffmpeg and ffprobe: a few seconds of a generated MPEG-TS stream.

The stream is served from this process on the loopback interface at playback speed. Both ways a
recording gets its bytes are exercised: read by ffmpeg itself (stopped by interrupting it) and
piped from a shared upstream whose viewer leaves mid-capture (stopped by end of input).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.infra.models import Channel, Recording
from app.infra.settings import AppSettings
from app.proxy.capture import Capture
from app.services import ids
from app.services.media_probe import MediaProbe
from tests.conftest import Api
from tests.test_epg_integrity import add_source

FFMPEG = os.environ.get("STREAMHUB_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("STREAMHUB_TEST_FFPROBE") or shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg and ffprobe are not installed")

CLIP_S = 30
PACKET = 188


@pytest.fixture
def settings(settings: AppSettings, tmp_path: Path) -> AppSettings:
    root = tmp_path / "recordings"
    root.mkdir()
    return settings.model_copy(update={"recordings_dir": str(root)})


async def generate_ts(path: Path) -> bytes:
    """H.264 and AAC in MPEG-TS, a keyframe every second, like a live channel."""
    assert FFMPEG
    made = await asyncio.create_subprocess_exec(
        FFMPEG,
        *("-hide_banner", "-loglevel", "error", "-nostdin"),
        *("-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10"),
        *("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"),
        *("-t", str(CLIP_S), "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", "-pix_fmt", "yuv420p"),
        *("-c:a", "aac", "-b:a", "64k", "-f", "mpegts", str(path)),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await made.communicate()
    assert made.returncode == 0, stderr.decode(errors="replace")
    return path.read_bytes()


async def serve(clip: bytes) -> tuple[asyncio.Server, str]:
    """Send ``clip`` once per connection at about playback speed. Returns the server and its URL."""
    step = max(PACKET, len(clip) // (CLIP_S * 20) // PACKET * PACKET)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: video/mp2t\r\nConnection: close\r\n\r\n")
            for at in range(0, len(clip), step):
                writer.write(clip[at : at + step])
                await writer.drain()
                await asyncio.sleep(0.05)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}/live.ts"


async def channel_at(api: Api, url: str) -> Channel:
    source = await add_source(api)
    async with api.c.db.session() as s:
        channel = (
            await s.execute(select(Channel).where(Channel.source_id == source["id"]).order_by(Channel.id).limit(1))
        ).scalar_one()
        channel.stream_url = url
        channel.data = {k: v for k, v in (channel.data or {}).items() if k != "headers"}
        await s.commit()
    return channel


async def recording(api: Api, channel: Channel, rid: str, title: str) -> Recording:
    start = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)
    row = Recording(
        id=rid,
        profile_id=api.profile_id,
        channel_id=ids.channel_id(channel.source_id, channel.ext_id),
        channel_name=channel.name,
        programme_key=f"{channel.source_id}:{channel.ext_id}@{rid}",
        title=title,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        state="recording",
    )
    async with api.c.db.session() as s:
        s.add(row)
        await s.commit()
    return row


async def check(root: Path, result: Any, name: str) -> None:
    assert FFPROBE
    assert result.error is None and result.path == name, result
    file = root / name
    assert file.stat().st_size == result.size_bytes > 0
    probe = MediaProbe(FFPROBE)
    probe.available = True
    found = await probe.run(str(file), {})
    assert found is not None
    assert found["video"]["codec"] == "h264" and [t["codec"] for t in found["audio"]] == ["aac"]  # copied, not encoded
    assert "matroska" in found["container"]
    # A finalised file knows its length; an unfinished Matroska file does not.
    assert found["duration_ms"] is not None and 500 <= found["duration_ms"] <= CLIP_S * 1000


async def test_real_capture_on_both_paths(api: Api, tmp_path: Path) -> None:
    assert FFMPEG
    clip = await generate_ts(tmp_path / "live.ts")
    server, url = await serve(clip)
    root = Path(api.c.settings.recordings_dir)
    capture = Capture(api.c.proxy, api.c.sources, api.c.db, recordings_dir=root, ffmpeg=FFMPEG, min_free_gb=0, tz=UTC)
    channel = await channel_at(api, url)
    key = f"{channel.source_id}:{channel.ext_id}"
    try:
        # A connection of its own: ffmpeg reads the URL and is interrupted to finish the file.
        alone = await recording(api, channel, "real1", "Alone")
        await capture.start(alone)
        assert capture.is_running(alone.id) and len(api.c.proxy._captures) == 1
        await asyncio.sleep(4)
        assert capture.is_running(alone.id)
        await check(root, await capture.stop(alone), "Alone (2026-03-01 2000).mkv")
        assert not api.c.proxy._captures

        # Someone is watching: the recording is fed from their upstream, and keeps it when they leave.
        _, viewer = await api.c.proxy.live_shared(key, url, {}, channel.source_id)

        async def watch() -> None:
            async for _ in viewer:
                pass

        watching = asyncio.create_task(watch())
        shared = await recording(api, channel, "real2", "Shared")
        await capture.start(shared)
        assert capture.is_running(shared.id) and not api.c.proxy._captures
        await asyncio.sleep(2)
        watching.cancel()
        await asyncio.gather(watching, return_exceptions=True)
        await viewer.aclose()
        await asyncio.sleep(2)
        assert capture.is_running(shared.id) and key in api.c.proxy._shared
        await check(root, await capture.stop(shared), "Shared (2026-03-01 2000).mkv")
    finally:
        await capture.shutdown()
        await api.c.proxy.shutdown()
        server.close()
        await server.wait_closed()
