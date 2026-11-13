"""A viewer's quality cap (``max_height``): the transcode it makes, the session it keys, what it leaves alone.

ffmpeg and ffprobe are stubbed as in ``test_web_vod.py``; nothing here opens a stream.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.infra.models import Title
from app.services.media_info import QUALITY_KBPS, capped_height, output_size
from app.services.playback import RoomScope
from tests.conftest import FIXTURES, Api, add_m3u
from tests.test_web_vod import FakeProbe, Process, add_film, after, attempts, grant_of, local, probe

UHD = {"codec": "hevc", "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le"}
MP4 = "mov,mp4,m4a,3gp,3g2,mj2"
AAC = [{"index": 0, "language": "eng", "codec": "aac", "channels": 2, "title": None, "default": True}]


def film(video: dict[str, Any] = UHD, **kw: Any) -> dict[str, Any]:
    return {**probe(), "video": video, **kw}


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], Process]]:
    """Stub ffmpeg: every spawn writes a one-segment playlist, like a session that started."""
    runs: list[tuple[list[str], Process]] = []

    async def spawn(*args: Any, **kwargs: Any) -> Process:
        proc = Process()
        runs.append((list(args), proc))
        Path(args[-1]).write_text("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.000000,\nseg_00000.ts\n")
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return runs


# ---------------------------------------------------------------- the rule
def test_a_cap_applies_only_to_a_source_that_is_taller() -> None:
    assert capped_height(2160, 1080) == 1080 and capped_height(1080, 720) == 720 and capped_height(720, 480) == 480
    # At or below the cap, or of unknown height: as without one. Nothing is scaled up.
    assert capped_height(1080, 1080) is None and capped_height(576, 720) is None
    assert capped_height(None, 720) is None and capped_height("1080", 720) is None
    assert capped_height(2160, None) is None and capped_height(2160, 360) is None
    assert sorted(QUALITY_KBPS) == [480, 720, 1080]


def test_capped_output_keeps_the_shape_and_even_sizes() -> None:
    assert output_size(3840, 2160, 1080) == (1920, 1080) and output_size(3840, 2160, 720) == (1280, 720)
    assert output_size(1920, 1080, 480) == (854, 480) and output_size(1920, 800, 480) == (1152, 480)
    assert output_size(1440, 1080, 720) == (960, 720)  # 4:3 stays 4:3
    # A wide film is already under the cap once it is 1920 wide: the width rule alone decides.
    assert output_size(3840, 1600, 1080) == (1920, 800)
    assert output_size(1280, 536, 720) == (1280, 536)


# ---------------------------------------------------------------- ffmpeg arguments
@pytest.mark.parametrize(
    ("cap", "size", "rate"), [(1080, "1920:1080", 5000), (720, "1280:720", 2800), (480, "854:480", 1400)]
)
def test_each_cap_scales_and_holds_the_rate_on_every_encoder(tmp_path: Path, cap: int, size: str, rate: int) -> None:
    plan = {"video": "transcode", "codec": "hevc", "width": 3840, "height": 2160, "max_height": cap}
    chain = attempts(tmp_path, hw="nvenc", **plan)
    # The same ladder as an uncapped transcode: hardware first, software last.
    assert [label for label, _ in chain] == ["nvenc+cuda", "nvenc", "libx264"]
    for _, args in chain:
        assert after(args, "-vf") == f"scale={size},format=yuv420p"
        assert (after(args, "-maxrate"), after(args, "-bufsize")) == (f"{rate}k", f"{rate * 2}k")
        assert after(args, "-force_key_frames") == "expr:gte(t,n_forced*4)"
        # The audio of a capped quality is what it always is in HLS: one track, AAC stereo.
        assert (after(args, "-c:a"), after(args, "-ac"), after(args, "-b:a")) == ("aac", "2", "160k")
    assert after(chain[0][1], "-c:v") == "h264_nvenc" and after(chain[2][1], "-c:v") == "libx264"
    [(_, software)] = attempts(tmp_path, hw="none", **plan)
    assert after(software, "-vf") == f"scale={size},format=yuv420p" and after(software, "-crf") == "23"
    vaapi, fallback = attempts(tmp_path, hw="vaapi", **plan)
    width, height = size.split(":")
    assert after(vaapi[1], "-vf") == f"format=nv12|vaapi,hwupload,scale_vaapi=w={width}:h={height}:format=nv12"
    assert after(vaapi[1], "-maxrate") == f"{rate}k" and fallback[0] == "libx264"
    qsv, _ = attempts(tmp_path, hw="qsv", **plan)
    assert after(qsv[1], "-vf") == f"scale={size}" and after(qsv[1], "-maxrate") == f"{rate}k"


def test_without_a_cap_the_transcode_is_as_it_was(tmp_path: Path) -> None:
    [(_, args)] = attempts(tmp_path, video="transcode", hw="none", codec="hevc", width=3840, height=2160)
    assert after(args, "-vf") == "scale=1920:-2,format=yuv420p"
    assert (after(args, "-maxrate"), after(args, "-bufsize")) == ("8M", "16M")
    [(_, small)] = attempts(tmp_path, video="transcode", hw="none", codec="hevc", width=1280, height=720)
    assert after(small, "-vf") == "format=yuv420p"
    [(_, qsv), _] = attempts(tmp_path, video="transcode", hw="qsv", codec="hevc", width=3840, height=2160)
    assert "-vf" not in qsv
    # A cap on a source whose width nobody knows still bounds the height.
    [(_, blind)] = attempts(tmp_path, video="transcode", hw="none", codec="hevc", height=2160, max_height=720)
    assert after(blind, "-vf") == "scale=-2:720,format=yuv420p" and after(blind, "-maxrate") == "2800k"


# ---------------------------------------------------------------- resolve
async def test_a_cap_below_the_source_transcodes_and_is_reported(api: Api) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(film())
    await api.patch("/settings", json={"transcode_hw": "nvenc"})
    d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", "max_height": 720})).json()
    grant = grant_of(api, d)
    assert d["mode"] == "hls" and d["start_position_ms"] == 0
    assert (grant.vod_video, grant.vod_width, grant.vod_height, grant.max_height) == ("transcode", 3840, 2160, 720)
    info = d["media_info"]
    assert (info["delivery"], info["encoder"], info["max_height"]) == ("transcode", "hardware", 720)
    assert (info["output_width"], info["output_height"]) == (1280, 720)
    assert (info["video"]["width"], info["video"]["height"]) == (3840, 2160)  # the file is still what it is
    # Without one the same film is transcoded to 1080p as before, and says no cap is in force.
    plain = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    assert grant_of(api, plain).max_height is None and "max_height" not in plain["media_info"]
    assert (plain["media_info"]["output_width"], plain["media_info"]["output_height"]) == (1920, 1080)
    # A change of quality mid-film restarts where the viewer is.
    moved = {"id": "lib:iptv:7", "max_height": 480, "start_position_ms": 1_234_567}
    again = (await api.post("/playback/resolve", json=moved)).json()
    assert again["start_position_ms"] == 1_230_000 and grant_of(api, again).max_height == 480


async def test_a_cap_turns_copied_video_and_a_browser_ready_file_into_a_transcode(api: Api) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(probe("h264", "yuv420p"))  # 1080p H.264 in MKV: copied
    assert grant_of(api, (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()).vod_video == "copy"
    capped = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", "max_height": 480})).json()
    grant = grant_of(api, capped)
    assert (grant.vod_video, grant.max_height) == ("transcode", 480)
    assert capped["media_info"]["delivery"] == "transcode" and capped["media_info"]["output_height"] == 480
    # A file the browser plays as it is becomes a restart-at-offset stream only while it is capped.
    async with api.c.db.session() as session:
        row = (await session.execute(select(Title).where(Title.source_id == "iptv"))).scalar_one()
        row.extra = {"ext": "mp4"}
        await session.commit()
    api.c.playback.probe = FakeProbe(probe("h264", "yuv420p", MP4, audio=AAC))
    direct = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    assert direct["mode"] == "direct" and direct["start_position_ms"] is None
    saver = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", "max_height": 720})).json()
    assert saver["mode"] == "hls" and saver["start_position_ms"] == 0 and saver["audio_track"] == 0
    assert grant_of(api, saver).vod_video == "transcode"


async def test_a_cap_at_or_above_the_source_changes_nothing(api: Api) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(probe("h264", "yuv420p"))  # 1920x1080
    for cap in (1080, None):
        body = {"id": "lib:iptv:7", **({"max_height": cap} if cap else {})}
        d = (await api.post("/playback/resolve", json=body)).json()
        grant = grant_of(api, d)
        assert (grant.vod_video, grant.max_height) == ("copy", None)
        assert d["media_info"]["delivery"] == "remux" and "max_height" not in d["media_info"]
    # A file that could not be probed has no known height: it is transcoded blind, uncapped, as before.
    async with api.c.db.session() as session:
        row = (await session.execute(select(Title).where(Title.source_id == "iptv"))).scalar_one()
        row.extra = {"ext": "mkv"}
        await session.commit()
    api.c.playback._probe_retry.clear()
    api.c.playback.probe = FakeProbe(None)
    blind = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", "max_height": 480})).json()
    assert grant_of(api, blind).max_height is None and grant_of(api, blind).vod_video == "transcode"


async def test_only_the_three_heights_are_accepted(api: Api) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(film())
    for bad in (360, 0, 2160, "720p", 720.5):
        refused = await api.post("/playback/resolve", json={"id": "lib:iptv:7", "max_height": bad})
        assert refused.status_code == 422, (bad, refused.text)
    assert (await api.post("/playback/resolve", json={"id": "lib:iptv:7", "max_height": None})).status_code == 200


async def test_the_tv_a_channel_and_a_room_ignore_the_cap(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(film())
    # android_tv plays the file itself.
    tv = await api.post("/playback/resolve", json={"id": "lib:iptv:7", "client": "android_tv", "max_height": 480})
    assert tv.status_code == 200, tv.text
    assert tv.json()["mode"] == "direct" and "max_height" not in tv.json()["media_info"]
    # A channel is delivered as it is: the field is accepted and unused.
    await add_m3u(api, FIXTURES / "sample.m3u")
    channel = (await api.get("/channels")).json()["channels"][0]
    live = await api.post("/playback/resolve", json={"id": channel["id"], "max_height": 480})
    assert live.status_code == 200, live.text
    assert live.json()["live"] is True and "max_height" not in (live.json()["media_info"] or {})
    if live.json()["mode"] != "direct":
        assert grant_of(api, live.json()).max_height is None
    # A room: whatever a member asks, the title is resolved without a cap.
    seen: list[int | None] = []

    async def resolve_title(*args: Any, max_height: int | None = None, **kwargs: Any) -> dict[str, Any]:
        seen.append(max_height)
        return {}

    monkeypatch.setattr(api.c.playback, "_resolve_title", resolve_title)
    scope = RoomScope(code="ABCDEF", position_ms=0)
    await api.c.playback.resolve(api.profile_id, "lib:iptv:7", client="web", max_height=480, room=scope)
    await api.c.playback.resolve(api.profile_id, "lib:iptv:7", client="web", max_height=480)
    assert seen == [None, 480]


# ---------------------------------------------------------------- sessions
async def test_the_session_key_carries_the_cap(api: Api, spawned: list) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(film())

    async def play(**body: Any) -> list[str]:
        d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", **body})).json()
        playlist = await api.client.get(local(d["url"]))
        assert playlist.status_code == 200, playlist.text
        return list(api.c.proxy._hls)

    [original] = await play()
    assert original.endswith(":vod:transcode:none:1:0:0")
    args, first = spawned[0]
    assert after(args, "-vf") == "scale=1920:-2,format=yuv420p" and after(args, "-maxrate") == "8M"
    # Another quality is another session with its own output, never the first one's segments...
    [capped] = await play(max_height=720)
    assert capped.endswith(":vod:transcode:none:1:0:720") and capped != original
    args, second = spawned[1]
    assert after(args, "-vf") == "scale=1280:720,format=yuv420p" and after(args, "-maxrate") == "2800k"
    # ...and it replaces this viewer's previous one at once, as a seek does: one transcoder, one connection.
    assert first.stopped and not second.stopped and len(spawned) == 2
    # The same quality again shares the session that is running.
    assert await play(max_height=720) == [capped] and len(spawned) == 2
    [lower] = await play(max_height=480, start_position_ms=600_000)
    assert lower.endswith(":vod:transcode:none:1:600:480") and second.stopped
    args, _ = spawned[2]
    assert after(args, "-ss") == "600" and after(args, "-vf") == "scale=854:480,format=yuv420p"
    await api.c.proxy.shutdown()
