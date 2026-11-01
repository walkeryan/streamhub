"""Actual production DVR args against a finite local file and loopback HTTP only."""

import asyncio
import os

import pytest

from app.proxy.dvr import MAX_VOLUME, PARENT_RESERVE, DvrManager, VolumeProof
from app.proxy.stream_proxy import StreamProxy

FFMPEG = os.environ.get("STREAMHUB_TEST_FFMPEG")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="local FFmpeg explicitly required")


@pytest.mark.parametrize("http_input", [False, True], ids=["file", "loopback-http-stall"])
async def test_production_dvr_paces_and_stop_cleans_blocked_input(tmp_path, http_input):
    clip = tmp_path / "input.ts"
    process = await asyncio.create_subprocess_exec(
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x90:rate=5",
        "-t",
        "12",
        "-an",
        "-c:v",
        "mpeg2video",
        "-g",
        "10",
        "-threads",
        "1",
        "-f",
        "mpegts",
        str(clip),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, errors = await process.communicate()
    assert process.returncode == 0, errors.decode(errors="replace")
    server = None
    writers = set()
    released = asyncio.Event()
    requests = []

    async def serve(reader, writer):
        writers.add(writer)
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            requests.append(request.split(b"\r\n")[0])
            payload = clip.read_bytes()
            # Announce additional data but deliberately stall after finite media.
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: video/mp2t\r\nContent-Length: "
                + str(len(payload) * 2).encode()
                + b"\r\n\r\n"
                + payload
            )
            await writer.drain()
            await released.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)

    url = str(clip)
    if http_input:
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/fixture.ts"
    volume = tmp_path / "fake-volume"
    volume.mkdir()
    dvr = DvrManager(volume, enabled=True, verify=lambda _: VolumeProof(MAX_VOLUME, MAX_VOLUME, PARENT_RESERVE * 2))
    proxy = StreamProxy(lambda **_: None, tmp_path / "normal", dvr=dvr, ffmpeg=FFMPEG)
    try:
        started = asyncio.get_running_loop().time()
        await proxy.buffer_lease("fixture", "profile", url, {}, source_id="source", max_connections=1, generation=0)
        # HTTP announces more bytes but stalls after 12 seconds of media.
        # Wait beyond that input before testing bounded producer termination.
        await asyncio.sleep(14 if http_input else 3)
        elapsed = asyncio.get_running_loop().time() - started
        window = await proxy.buffer_window("fixture")
        assert 0 < window["buffered_seconds"] <= elapsed + 2
        # Actual finite input must not already be burst-consumed and exited.
        assert proxy._hls["fixture"].proc.returncode is None
        assert len(list(volume.rglob("seg_*.ts"))) <= 8
        if http_input:
            assert requests == [b"GET /fixture.ts HTTP/1.1"]
        proxy.pause()
        await asyncio.wait_for(proxy.stop_owned(), timeout=8)
        assert not proxy._hls and not dvr.leases and not list(volume.iterdir())
    finally:
        await proxy.shutdown()
        released.set()
        if server:
            server.close()
            await server.wait_closed()
        await asyncio.sleep(0)
