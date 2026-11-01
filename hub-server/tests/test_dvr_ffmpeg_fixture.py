"""Optional tiny real-FFmpeg fixture. No provider network or real quota allocation."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from app.proxy.dvr import MAX_VOLUME, PARENT_RESERVE, DvrManager, VolumeProof
from app.services.playback import StreamGrant
from tests.test_epg_integrity import add_source

FFMPEG = os.environ.get("STREAMHUB_TEST_FFMPEG") or shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="explicit local ffmpeg fixture tool is unavailable")


@pytest.mark.parametrize("paced", [True, False], ids=["paced", "startup-burst"])
async def test_real_ffmpeg_archive_window_and_seek(api, tmp_path, monkeypatch, paced):
    clip = tmp_path / "synthetic.ts"
    real_spawn = asyncio.create_subprocess_exec
    generated = await real_spawn(
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
    _, stderr = await generated.communicate()
    assert generated.returncode == 0, stderr.decode(errors="replace")
    root = tmp_path / "fake-quota-volume"
    root.mkdir()
    manager = DvrManager(root, enabled=True, verify=lambda _: VolumeProof(MAX_VOLUME, MAX_VOLUME, PARENT_RESERVE * 10))
    api.c.proxy.dvr = manager
    api.c.proxy.ffmpeg = FFMPEG

    async def fixture_spawn(*args, **kwargs):
        # Fixture-only pacing; production input flags remain unchanged.
        if paced and args[0] == FFMPEG:
            args = (*args[: args.index("-i")], "-re", *args[args.index("-i") :])
        return await real_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fixture_spawn)
    source = await add_source(api)
    channel = (await api.get("/channels", params={"language": "all"})).json()["channels"][0]
    grant = StreamGrant(api.profile_id, source["id"], str(clip), {}, "hls", True, channel["id"][3:], False)
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
        # Decode the actual served segment and inspect multiple advancing frames.
        served = tmp_path / "served.ts"
        served.write_bytes(segment.content)
        probe = await real_spawn(
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
    assert (await api.post("/playback/buffer/release", json=body)).status_code == 204
    assert not api.c.proxy._hls and not list(root.iterdir())
