"""Optional tiny real-FFmpeg fixture. No provider network or real quota allocation."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise
from threading import Thread

import pytest

from app.proxy.dvr import MAX_VOLUME, PARENT_RESERVE, DvrManager, VolumeProof
from app.services.playback import StreamGrant
from tests.conftest import add_m3u
from tests.test_api_playback import channels
from tests.test_epg_integrity import add_source

FFMPEG = os.environ.get("STREAMHUB_TEST_FFMPEG") or shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="explicit local ffmpeg fixture tool is unavailable")


async def generate_clip(clip, *, audio=False):
    generated = await asyncio.create_subprocess_exec(
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x90:rate=5",
        *(["-f", "lavfi", "-i", "sine=frequency=440", "-c:a", "aac"] if audio else ["-an"]),
        "-t",
        "12",
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
    _, stderr = await generated.communicate()
    assert generated.returncode == 0, stderr.decode(errors="replace")


async def assert_decodable(content, tmp_path):
    # Decode the actual served segment and inspect multiple advancing frames.
    served = tmp_path / "served.ts"
    served.write_bytes(content)
    probe = await asyncio.create_subprocess_exec(
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(served),
        "-map",
        "0:v:0",
        "-f",
        "framemd5",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    frames, errors = await probe.communicate()
    assert probe.returncode == 0, errors.decode(errors="replace")
    rows = [line.split(",") for line in frames.decode().splitlines() if line and not line.startswith("#")]
    assert len(rows) >= 2
    timestamps = [int(row[2]) for row in rows]
    assert all(a < b for a, b in pairwise(timestamps))
    assert len({row[-1].strip() for row in rows}) >= 2


@pytest.mark.parametrize("paced", [True, False], ids=["production-paced", "unpaced-fault-injection"])
async def test_real_ffmpeg_archive_window_and_seek(api, tmp_path, monkeypatch, paced):
    clip = tmp_path / "synthetic.ts"
    real_spawn = asyncio.create_subprocess_exec
    await generate_clip(clip)
    root = tmp_path / "fake-quota-volume"
    root.mkdir()
    manager = DvrManager(root, enabled=True, verify=lambda _: VolumeProof(MAX_VOLUME, MAX_VOLUME, PARENT_RESERVE * 10))
    api.c.proxy.dvr = manager
    api.c.proxy.ffmpeg = FFMPEG

    async def fixture_spawn(*args, **kwargs):
        # Normal case uses exact production DVR pacing. Only the fault case
        # removes pacing to verify manager defense against future timestamps.
        if not paced and args[0] == FFMPEG:
            args = tuple(arg for arg in args if arg != "-re")
        return await real_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fixture_spawn)
    source = await add_source(api)
    channel = (await api.get("/channels", params={"language": "all"})).json()["channels"][0]
    grant = StreamGrant(
        api.profile_id, source["id"], str(clip), {}, "hls", True, channel["id"][3:], False, **api.identity()
    )
    token = api.c.tokens.issue(grant)
    response = await api.post("/playback/buffer/lease", json={"stream_token": token})
    assert response.status_code == 200, response.text
    body = {"stream_token": token, "lease_id": response.json()["lease_id"]}
    await asyncio.sleep(2.2)
    heartbeat = await api.post("/playback/buffer/heartbeat", json=body)
    assert heartbeat.status_code == 200, heartbeat.text
    window = heartbeat.json()
    assert 0 < window["buffered_seconds"] < 12
    end = datetime.fromisoformat(window["end"].replace("Z", "+00:00"))
    assert end <= datetime.now(UTC)  # Never publish future history during an input burst.
    start = datetime.fromisoformat(window["start"].replace("Z", "+00:00"))
    seek = await api.post("/playback/buffer/seek", json={**body, "at": (start + timedelta(seconds=0.5)).isoformat()})
    assert seek.status_code == 200
    if paced:
        playlist = await api.client.get(seek.json()["url"])
        assert playlist.status_code == 200 and "TIME-OFFSET=0.500" in playlist.text
        segment_url = next(line for line in playlist.text.splitlines() if line and not line.startswith("#"))
        segment = await api.client.get(segment_url)
        assert segment.status_code == 200 and segment.content[:1] == b"\x47"
        await assert_decodable(segment.content, tmp_path)
    assert (await api.post("/playback/buffer/release", json=body)).status_code == 204
    assert not api.c.proxy._hls and not list(root.iterdir())


async def assert_canvas_decodable(payload, tmp_path, *, sound):
    import io

    from PIL import Image
    from app.proxy.canvas import PICTURE_OWNER

    assert payload.startswith(b"ID3")
    first = payload.index(PICTURE_OWNER + b"\0") + len(PICTURE_OWNER) + 5
    end = 10 + sum(b << shift for b, shift in zip(payload[6:10], (21, 14, 7, 0), strict=True))
    with Image.open(io.BytesIO(payload[first:end])) as sheet:
        sheet.load()
        assert sheet.size == (160 * 4, 90 * 6)
        assert sheet.crop((0, 0, 160, 90)).tobytes() != sheet.crop((0, 90, 160, 180)).tobytes()
    served = tmp_path / "rewound.aac"
    served.write_bytes(payload)
    decoder = await asyncio.create_subprocess_exec(
        FFMPEG, "-v", "error", "-f", "aac", "-i", str(served), "-f", "s16le", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    pcm, stderr = await decoder.communicate()
    assert decoder.returncode == 0, stderr.decode(errors="replace")
    assert len(pcm) > 100_000 and bool(any(pcm)) == sound


@pytest.mark.parametrize("client", ["web", "android_tv", "canvas", "canvas-silent"])
async def test_watched_tune_scrub_live_then_change_channel(api, tmp_path, client):
    clip = tmp_path / "signal.ts"
    canvas = client.startswith("canvas")
    await generate_clip(clip, audio=client == "canvas")
    http = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    server = Thread(target=http.serve_forever, daemon=True)
    server.start()
    root = tmp_path / "watched-volume"
    root.mkdir()
    api.c.proxy.dvr = DvrManager(
        root,
        enabled=True,
        watched_session=True,
        verify=lambda _: VolumeProof(MAX_VOLUME, MAX_VOLUME, PARENT_RESERVE * 10),
    )
    api.c.proxy.ffmpeg = FFMPEG
    api.c.playback._buffer_live_channels = True
    try:
        base = f"http://127.0.0.1:{http.server_port}/signal.ts"
        playlist = tmp_path / "channels.m3u"
        playlist.write_text(
            '#EXTM3U\n#EXTINF:-1 tvg-id="one" catchup="default" catchup-days="1" '
            f'catchup-source="{base}?utc={{utc}}",Archive channel\n{base}?channel=one\n'
            f'#EXTINF:-1 tvg-id="two",Live channel\n{base}?channel=two\n'
        )
        await add_m3u(api, playlist)
        lineup = {row["name"]: row["id"] for row in await channels(api)}
        for name, provider_mode in [("Archive channel", "provider_archive"), ("Live channel", "unavailable")]:
            channel_id = lineup[name]
            capability = await api.get("/playback/rewind", params={"id": channel_id})
            assert capability.json()["mode"] == provider_mode
            tune_time = datetime.now(UTC)
            request = {"id": channel_id, "client": "web" if canvas else client, "prefer_canvas": canvas}
            if canvas:
                preview = (await api.post("/playback/resolve", json={**request, "preview_only": True})).json()
                assert preview["canvas"] and not preview["buffer_available"]
                preview_token = preview["url"].split("/stream/", 1)[1].split("/", 1)[0]
                assert (await api.post("/playback/buffer/lease", json={"stream_token": preview_token})).status_code == 403
                assert not list(root.iterdir())
            resolve = await api.post("/playback/resolve", json=request)
            assert resolve.status_code == 200, resolve.text
            descriptor = resolve.json()
            assert descriptor["live"] and descriptor["mode"] == "hls" and descriptor["buffer_available"]
            token = descriptor["url"].split("/stream/", 1)[1].split("/", 1)[0]
            opened = await api.post("/playback/buffer/lease", json={"stream_token": token})
            assert opened.status_code == 200, opened.text
            viewer = {"stream_token": token, "lease_id": opened.json()["lease_id"]}
            async with asyncio.timeout(15):
                while True:
                    beat = await api.post("/playback/buffer/heartbeat", json=viewer)
                    assert beat.status_code == 200, beat.text
                    window = beat.json()
                    if window["buffered_seconds"] >= 1:
                        break
                    await asyncio.sleep(0.25)
            start = datetime.fromisoformat(window["start"])
            assert tune_time <= start < datetime.fromisoformat(window["end"]) <= datetime.now(UTC)
            selected = start + timedelta(seconds=0.5)
            expired = await api.post(
                "/playback/buffer/seek", json={**viewer, "at": (tune_time - timedelta(seconds=2)).isoformat()}
            )
            assert expired.status_code == 410
            scrub = await api.post("/playback/buffer/seek", json={**viewer, "at": selected.isoformat()})
            assert scrub.status_code == 200, scrub.text
            manifest = await api.client.get(scrub.json()["url"])
            assert manifest.status_code == 200 and "#EXT-X-START:" in manifest.text
            href = next(line for line in manifest.text.splitlines() if line and not line.startswith("#"))
            part = await api.client.get(href)
            assert part.status_code == 200
            if canvas:
                assert descriptor["canvas"] and href.endswith(".aac") and part.headers["content-type"] == "audio/aac"
                await assert_canvas_decodable(part.content, tmp_path, sound=client == "canvas")
            else:
                await assert_decodable(part.content, tmp_path)
            live = await api.post("/playback/buffer/seek", json=viewer)
            assert live.status_code == 200, live.text
            edge = await api.client.get(live.json()["url"])
            assert edge.status_code == 200 and "#EXTINF:" in edge.text and "#EXT-X-START:" not in edge.text
            assert len(api.c.proxy._hls) == 1
            assert (await api.post("/playback/buffer/release", json=viewer)).status_code == 204
            assert not list(root.iterdir()) and not api.c.proxy._hls
            assert (await api.post("/playback/buffer/heartbeat", json=viewer)).status_code == 403
            assert (await api.client.get(href)).status_code in (404, 410)
    finally:
        await api.c.proxy.shutdown()
        await asyncio.to_thread(http.shutdown)
        http.server_close()
        server.join()
