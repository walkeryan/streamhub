"""Web VOD against the real ffprobe and ffmpeg, on a generated two-second file. No network."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from app.proxy.stream_proxy import StreamProxy, VodPlan
from app.services.media_probe import MediaProbe
from app.services.playback import web_vod_plan

FFMPEG = os.environ.get("STREAMHUB_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("STREAMHUB_TEST_FFPROBE") or shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg and ffprobe are not installed")


async def test_real_probe_and_transcode_of_a_file_no_browser_plays(tmp_path: Path) -> None:
    assert FFMPEG and FFPROBE
    clip = tmp_path / "film.mkv"
    # MPEG-4 video with two AC-3 tracks, the second one French: nothing here plays in a browser.
    generated = await asyncio.create_subprocess_exec(
        FFMPEG,
        *("-hide_banner", "-loglevel", "error", "-nostdin"),
        *("-f", "lavfi", "-i", "testsrc=size=320x180:rate=10"),
        *("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"),
        *("-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000"),
        *("-t", "2", "-map", "0:v", "-map", "1:a", "-map", "2:a"),
        *("-c:v", "mpeg4", "-c:a", "ac3", "-ac:a:0", "6", "-ac:a:1", "2"),
        *("-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre"),
        *("-disposition:a:0", "default", "-disposition:a:1", "0"),
        str(clip),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await generated.communicate()
    assert generated.returncode == 0, stderr.decode(errors="replace")

    probe = MediaProbe(FFPROBE)
    probe.available = True
    found = await probe.run(str(clip), {})
    assert found is not None
    assert found["video"]["codec"] == "mpeg4" and (found["video"]["width"], found["video"]["height"]) == (320, 180)
    assert [(t["language"], t["codec"], t["channels"], t["default"]) for t in found["audio"]] == [
        ("eng", "ac3", 6, True),
        ("fra", "ac3", 2, False),
    ]
    assert "matroska" in found["container"] and 1500 <= found["duration_ms"] <= 2500
    assert web_vod_plan(found, 1) == "transcode"

    proxy = StreamProxy(lambda **kwargs: None, tmp_path / "transcode", ffmpeg=FFMPEG)
    plan = VodPlan(video="transcode", hw="none", codec="mpeg4", width=320, audio_track=1, family="viewer")
    session = await proxy.hls_session("film", str(clip), {}, live=False, transcode="none", vod=plan)
    try:
        assert await asyncio.wait_for(session.proc.wait(), 60) == 0
        playlist = (session.workdir / "index.m3u8").read_text()
        assert "#EXT-X-PLAYLIST-TYPE:EVENT" in playlist and playlist.rstrip().endswith("#EXT-X-ENDLIST")
        assert session.complete and proxy._source_use(None) == 0
        segment = await probe.run(str(session.workdir / "seg_00000.ts"), {})
        assert segment is not None
        assert (segment["video"]["codec"], segment["video"]["pix_fmt"]) == ("h264", "yuv420p")
        assert [(t["codec"], t["channels"]) for t in segment["audio"]] == [("aac", 2)]
    finally:
        await proxy.shutdown()
    assert not session.workdir.exists()
