"""Web playback of a VOD file the browser cannot decode: probe, decide, transcode, seek.

ffprobe and ffmpeg are stubbed here (canned JSON, argument assertions); the one test that runs
the real binaries lives in ``test_web_vod_ffmpeg.py``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.domain.entities import CatalogItem, MediaKind
from app.infra.models import Profile, Source, Title
from app.proxy.stream_proxy import StreamProxy, VodPlan, vod_ffmpeg_attempts
from app.services import media_probe
from app.services.audio_selection import audio_info, default_track, preferred_track, probe_tracks
from app.services.media_probe import MediaProbe, cached_probe, ffprobe_args, parse_probe
from app.services.playback import web_vod_plan
from app.services.sources import LAZY_GEN, item_row, upsert_titles
from tests.conftest import Api

MANAGER = "http://manager.example.test"
FILM_URL = f"{MANAGER}/movie/alice/s3cret/7.mkv"

# Shape of ``ffprobe -of json -show_entries …`` for an MKV: HEVC Main 10, AC-3 5.1 and AC-3 stereo.
FFPROBE_MKV: dict[str, Any] = {
    "programs": [],
    "streams": [
        {
            "codec_name": "hevc",
            "profile": "Main 10",
            "codec_type": "video",
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv420p10le",
            "disposition": {"default": 1, "attached_pic": 0},
            "tags": {},
        },
        {
            "codec_name": "mjpeg",
            "codec_type": "video",
            "width": 600,
            "height": 900,
            "pix_fmt": "yuvj420p",
            "disposition": {"default": 0, "attached_pic": 1},
        },
        {
            "codec_name": "ac3",
            "codec_type": "audio",
            "channels": 6,
            "channel_layout": "5.1(side)",
            "disposition": {"default": 0, "attached_pic": 0},
            "tags": {"language": "fre", "title": "Français 5.1"},
        },
        {
            "codec_name": "eac3",
            "codec_type": "audio",
            "channels": 2,
            "channel_layout": "stereo",
            "disposition": {"default": 1, "attached_pic": 0},
            "tags": {"LANGUAGE": "eng"},
        },
        {"codec_name": "subrip", "codec_type": "subtitle", "disposition": {"default": 0}, "tags": {"language": "eng"}},
    ],
    "format": {"format_name": "matroska,webm", "duration": "5400.500000"},
}


def probe(video: str = "hevc", pix_fmt: str = "yuv420p10le", container: str = "matroska,webm", **kw: Any) -> dict:
    return {
        "video": {"codec": video, "width": 1920, "height": 1080, "pix_fmt": pix_fmt},
        "audio": [
            {"index": 0, "language": "fra", "codec": "ac3", "channels": 6, "title": None, "default": False},
            {"index": 1, "language": "eng", "codec": "eac3", "channels": 2, "title": None, "default": True},
        ],
        "container": container,
        "duration_ms": 5_400_500,
        "probed_at": datetime.now(UTC).isoformat(),
        **kw,
    }


class FakeProbe:
    available = True

    def __init__(self, result: dict | None) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def run(self, url: str, headers: dict[str, str]) -> dict | None:
        self.calls.append((url, headers))
        return self.result


async def add_film(api: Api, *, ext: str = "mkv", extra: dict | None = None, **config: Any) -> None:
    await api.accept_disclaimer()
    cfg = {"server_url": MANAGER, "username": "alice", "password": "s3cret", **config}
    async with api.c.db.session() as session:
        session.add(Source(id="iptv", type="xtream", name="Manager", config_enc=api.c.crypto.encrypt_json(cfg)))
        session.add(
            Title(source_id="iptv", ext_id="7", kind="movie", title="The Mill", extra={"ext": ext, **(extra or {})})
        )
        await session.commit()


async def film_row(api: Api) -> Title:
    async with api.c.db.session() as session:
        return (await session.execute(select(Title).where(Title.source_id == "iptv"))).scalar_one()


async def set_languages(api: Api, languages: list[str]) -> None:
    async with api.c.db.session() as session:
        profile = await session.get(Profile, api.profile_id)
        assert profile is not None
        profile.languages = languages
        await session.commit()


def grant_of(api: Api, descriptor: dict) -> Any:
    token = descriptor["url"].split("/stream/", 1)[1].split("/", 1)[0]
    grant = api.c.tokens.verify(token)
    assert grant is not None
    return grant


# ---------------------------------------------------------------- probe
def test_parse_probe_keeps_what_playback_needs() -> None:
    parsed = parse_probe(FFPROBE_MKV)
    assert parsed is not None
    assert parsed["video"] == {"codec": "hevc", "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le"}
    assert parsed["audio"] == [
        {"index": 0, "language": "fra", "codec": "ac3", "channels": 6, "title": "Français 5.1", "default": False},
        {"index": 1, "language": "eng", "codec": "eac3", "channels": 2, "title": None, "default": True},
    ]
    assert parsed["container"] == "matroska,webm" and parsed["duration_ms"] == 5_400_500
    assert cached_probe({"probe": parsed}) == parsed
    assert parse_probe({"streams": [], "format": {}}) is None and parse_probe("nope") is None


def test_cached_probe_expires_after_thirty_days() -> None:
    old = probe(probed_at=(datetime.now(UTC) - timedelta(days=31)).isoformat())
    assert cached_probe({"probe": old}) is None
    assert cached_probe({"probe": probe()}) is not None
    assert cached_probe({"probe": {"probed_at": "garbage"}}) is None and cached_probe({}) is None


def test_ffprobe_arguments_are_bounded_and_carry_the_proxy_headers() -> None:
    args = ffprobe_args("ffprobe", FILM_URL, {"User-Agent": "Player/1", "Referer": "http://r.example.test/"})
    assert args[0] == "ffprobe" and args[-2:] == ["-i", FILM_URL]
    assert args[args.index("-of") + 1] == "json"
    assert int(args[args.index("-analyzeduration") + 1]) <= 5_000_000
    assert args[args.index("-rw_timeout") + 1] == "10000000"
    assert args[args.index("-user_agent") + 1] == "Player/1"
    assert args[args.index("-headers") + 1] == "Referer: http://r.example.test/\r\n"
    # The format's filename is the upstream URL; it is never requested back.
    assert "-show_format" not in args and "filename" not in args[args.index("-show_entries") + 1]


class FakeProcess:
    def __init__(self, out: bytes = b"", code: int = 0, hang: bool = False) -> None:
        self.out, self.returncode, self.hang, self.killed = out, None if hang else code, hang, False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.hang:
            await asyncio.Event().wait()
        return self.out, b""

    def kill(self) -> None:
        self.killed, self.returncode = True, -9

    async def wait(self) -> int | None:
        return self.returncode


async def test_media_probe_runs_ffprobe_and_survives_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[tuple[Any, ...]] = []
    procs = [FakeProcess(json.dumps(FFPROBE_MKV).encode()), FakeProcess(b"", code=1), FakeProcess(hang=True)]

    async def spawn(*args: Any, **kwargs: Any) -> FakeProcess:
        spawned.append(args)
        assert kwargs["stderr"] == asyncio.subprocess.DEVNULL  # stderr names the URL
        return procs[len(spawned) - 1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(media_probe, "PROBE_TIMEOUT_S", 0.05)
    runner = MediaProbe("ffprobe")
    runner.available = True
    found = await runner.run(FILM_URL, {})
    assert found is not None and found["video"]["codec"] == "hevc" and len(found["audio"]) == 2
    assert spawned[0][0] == "ffprobe" and spawned[0][-1] == FILM_URL
    assert await runner.run(FILM_URL, {}) is None  # ffprobe failed
    assert await runner.run(FILM_URL, {}) is None and procs[2].killed  # ten-second cap


async def test_missing_ffprobe_disables_probing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = MediaProbe(str(tmp_path / "absent"))
    assert runner.available is False


# ---------------------------------------------------------------- audio evidence
def test_probe_is_audio_evidence_and_offers_the_preferred_track() -> None:
    extra = {"probe": probe()}
    info = audio_info(extra)
    assert info["audio_evidence"] == "media_probe" and info["audio_verified"]
    assert info["audio_languages"] == ["eng", "fra"] and info["default_audio_language"] == "eng"
    tracks = probe_tracks(extra)
    assert [t["index"] for t in tracks] == [0, 1]
    assert preferred_track(tracks, ["deu", "fra"]) == 0 and preferred_track(tracks, ["deu"]) is None
    assert default_track(tracks) == 1 and default_track([]) is None
    # Tracks the source itself reported keep precedence over a probe.
    provider = {"audio_tracks": [{"language": "deu", "default": True}], "audio_evidence": "provider_audio_tracks"}
    assert audio_info({**extra, **provider})["audio_languages"] == ["deu"]


# ---------------------------------------------------------------- sync keeps the probe
async def test_sync_upsert_keeps_the_cached_probe(api: Api) -> None:
    async with api.c.db.session() as session:
        session.add(Source(id="panel", name="Panel", type="xtream", config_enc="unused"))
        await session.commit()

    async def store(gen: int, **extra: Any) -> Title:
        item = CatalogItem(ext_id="7", kind=MediaKind.VOD, title="The Mill", extra=extra)
        await upsert_titles(api.c.db, [item_row("panel", item, gen)])
        async with api.c.db.session() as session:
            return (await session.execute(select(Title).where(Title.source_id == "panel"))).scalar_one()

    assert (await store(1, ext="mkv")).extra == {"ext": "mkv"}
    cached = probe()
    async with api.c.db.session() as session:
        row = (await session.execute(select(Title).where(Title.source_id == "panel"))).scalar_one()
        row.extra = {**row.extra, "probe": cached, "stale": True}
        await session.commit()
    # A sync still replaces everything the source says, and only that.
    row = await store(2, ext="mp4", raw_category="EN - NEW RELEASE")
    assert row.extra == {"ext": "mp4", "raw_category": "EN - NEW RELEASE", "probe": cached}
    row = await store(LAZY_GEN, ext="mp4")
    assert row.extra == {"ext": "mp4", "probe": cached}


# ---------------------------------------------------------------- decision
def test_web_vod_plan_matrix() -> None:
    mp4 = "mov,mp4,m4a,3gp,3g2,mj2"
    aac = [{"index": 0, "language": "eng", "codec": "aac", "channels": 2, "title": None, "default": True}]
    playable = probe("h264", "yuv420p", mp4, audio=aac)
    assert web_vod_plan(playable, 0) is None  # the browser decodes it: direct or passthrough as before
    assert web_vod_plan(probe("h264", "yuv420p", mp4, audio=[{**aac[0], "codec": "mp3"}]), 0) is None
    # H.264 the browser can decode is copied; only the audio is encoded.
    assert web_vod_plan(probe("h264", "yuv420p"), 1) == "copy"  # MKV container
    assert web_vod_plan(probe("h264", "yuv420p", mp4), 1) == "copy"  # AC-3 audio
    assert web_vod_plan(probe("h264", "yuv420p", mp4, audio=[*aac, {**aac[0], "index": 1}]), 1) == "copy"
    # Anything else is transcoded: HEVC, 10-bit H.264, or a file that could not be probed.
    assert web_vod_plan(probe("hevc"), 1) == "transcode"
    assert web_vod_plan(probe("h264", "yuv420p10le", mp4, audio=aac), 0) == "transcode"
    assert web_vod_plan(None, None) == "transcode"


# ---------------------------------------------------------------- ffmpeg arguments
def attempts(tmp_path: Path, **plan: Any) -> list[tuple[str, list[str]]]:
    return vod_ffmpeg_attempts(
        "ffmpeg", FILM_URL, {"User-Agent": "Player/1"}, tmp_path, VodPlan(**plan), vaapi_device="/dev/dri/renderD128"
    )


def after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_copy_video_encodes_only_the_chosen_audio_track(tmp_path: Path) -> None:
    [(label, args)] = attempts(tmp_path, video="copy", hw="nvenc", codec="h264", audio_track=1, start_s=600)
    assert label == "copy" and "-hwaccel" not in args
    assert args.index("-ss") < args.index("-i") and after(args, "-ss") == "600"
    assert after(args, "-i") == FILM_URL and after(args, "-user_agent") == "Player/1"
    assert [args[i + 1] for i, a in enumerate(args) if a == "-map"] == ["0:V:0?", "0:a:1?"]
    assert after(args, "-c:v") == "copy"
    assert (after(args, "-c:a"), after(args, "-ac"), after(args, "-b:a")) == ("aac", "2", "160k")
    assert after(args, "-f") == "hls" and after(args, "-hls_playlist_type") == "event"
    assert after(args, "-hls_list_size") == "0" and "delete_segments" not in after(args, "-hls_flags")
    assert args[-1] == str(tmp_path / "index.m3u8")
    assert after(args, "-readrate_initial_burst") == "30" and "-reconnect_at_eof" not in args
    [(_, start)] = attempts(tmp_path, video="copy")
    assert "-ss" not in start and "0:a:0?" in start


def test_nvenc_transcode_falls_back_to_software_decode_then_libx264(tmp_path: Path) -> None:
    chain = attempts(tmp_path, video="transcode", hw="nvenc", codec="hevc", width=3840)
    assert [label for label, _ in chain] == ["nvenc+cuda", "nvenc", "libx264"]
    cuda, nvenc, software = (args for _, args in chain)
    assert after(cuda, "-hwaccel") == "cuda" and cuda.index("-hwaccel") < cuda.index("-i")
    assert "-hwaccel" not in nvenc and "-hwaccel" not in software
    for args in (cuda, nvenc):
        assert after(args, "-c:v") == "h264_nvenc" and after(args, "-forced-idr") == "1"
    assert (after(software, "-c:v"), after(software, "-preset")) == ("libx264", "veryfast")
    for args in (cuda, nvenc, software):
        # 10-bit and 4K input becomes 8-bit 1080p H.264 with a keyframe at every segment boundary.
        assert after(args, "-vf") == "scale=1920:-2,format=yuv420p"
        assert after(args, "-force_key_frames") == "expr:gte(t,n_forced*4)"
        assert (after(args, "-c:a"), after(args, "-ac")) == ("aac", "2")
    # A codec the GPU cannot decode skips straight to software decoding.
    assert [label for label, _ in attempts(tmp_path, video="transcode", hw="nvenc", codec="theora")] == [
        "nvenc",
        "libx264",
    ]


def test_transcode_without_hardware_is_software_only(tmp_path: Path) -> None:
    [(label, args)] = attempts(tmp_path, video="transcode", hw="none", codec="hevc", width=1280)
    assert label == "libx264" and after(args, "-vf") == "format=yuv420p"
    assert [label for label, _ in attempts(tmp_path, video="transcode", hw="vaapi", codec="hevc")] == [
        "vaapi",
        "libx264",
    ]
    assert [label for label, _ in attempts(tmp_path, video="transcode", hw="qsv", codec="hevc")] == ["qsv", "libx264"]


# ---------------------------------------------------------------- options
async def test_options_probe_once_and_list_the_audio_tracks(api: Api) -> None:
    await add_film(api)
    await set_languages(api, ["eng"])
    fake = api.c.playback.probe = FakeProbe(probe())
    response = await api.get("/playback/options", params={"id": "lib:iptv:7"})
    assert response.status_code == 200, response.text
    [option] = response.json()["options"]
    assert fake.calls == [(FILM_URL, {})]
    assert option["audio_evidence"] == "media_probe" and option["audio_languages"] == ["eng", "fra"]
    assert [(t["index"], t["language"], t["codec"], t["channels"]) for t in option["audio_tracks"]] == [
        (0, "fra", "ac3", 6),
        (1, "eng", "eac3", 2),
    ]
    assert option["preferred_audio_track"] == 1 and option["preferred_audio_available"]
    assert response.json()["automatic_variant_id"] == "lib:iptv:7"
    assert (await film_row(api)).extra["probe"]["video"]["codec"] == "hevc"
    # Cached for thirty days: opening the title again does not touch the source.
    await api.get("/playback/options", params={"id": "lib:iptv:7"})
    assert len(fake.calls) == 1
    async with api.c.db.session() as session:
        row = (await session.execute(select(Title).where(Title.source_id == "iptv"))).scalar_one()
        stale = {**row.extra["probe"], "probed_at": (datetime.now(UTC) - timedelta(days=31)).isoformat()}
        row.extra = {**row.extra, "probe": stale}
        await session.commit()
    await api.get("/playback/options", params={"id": "lib:iptv:7"})
    assert len(fake.calls) == 2


async def test_a_non_default_track_in_the_preferred_language_needs_no_confirmation(api: Api) -> None:
    await add_film(api)
    await set_languages(api, ["fra"])
    api.c.playback.probe = FakeProbe(probe())
    data = (await api.get("/playback/options", params={"id": "lib:iptv:7"})).json()
    assert data["options"][0]["preferred_audio_track"] == 0 and not data["selection_required"]
    await set_languages(api, ["deu"])
    data = (await api.get("/playback/options", params={"id": "lib:iptv:7"})).json()
    assert data["options"][0]["preferred_audio_track"] is None and data["selection_required"]


async def test_options_do_not_probe_sources_that_report_audio_or_forbid_proxying(api: Api) -> None:
    provider = {"audio_tracks": [{"language": "eng", "default": True}], "audio_evidence": "provider_audio_tracks"}
    await add_film(api, extra=provider)
    fake = api.c.playback.probe = FakeProbe(probe())
    [option] = (await api.get("/playback/options", params={"id": "lib:iptv:7"})).json()["options"]
    assert fake.calls == [] and option["audio_evidence"] == "audio_track_metadata" and option["audio_tracks"] == []


async def test_failed_probe_leaves_audio_unverified_and_is_not_retried_at_once(api: Api) -> None:
    await add_film(api)
    fake = api.c.playback.probe = FakeProbe(None)
    for _ in range(2):
        [option] = (await api.get("/playback/options", params={"id": "lib:iptv:7"})).json()["options"]
        assert not option["audio_verified"] and option["audio_tracks"] == []
    assert len(fake.calls) == 1
    assert "probe" not in (await film_row(api)).extra


# ---------------------------------------------------------------- resolve
async def test_web_resolves_an_undecodable_film_to_hls_with_the_preferred_track(api: Api) -> None:
    await add_film(api)
    await set_languages(api, ["fra"])
    fake = api.c.playback.probe = FakeProbe(probe())
    response = await api.post("/playback/resolve", json={"id": "lib:iptv:7", "client": "web"})
    assert response.status_code == 200, response.text
    d = response.json()
    assert d["mode"] == "hls" and d["url"].endswith("/index.m3u8") and d["mime"] == "application/x-mpegURL"
    assert d["duration_ms"] == 5_400_500 and d["start_position_ms"] == 0
    assert d["audio_track"] == 0 and [t["language"] for t in d["audio_tracks"]] == ["fra", "eng"]
    assert d["default_audio_language"] == "fra" and d["audio_languages"] == ["fra"]
    assert d["audio_evidence"] == "media_probe" and d["audio_verified"]
    grant = grant_of(api, d)
    assert (grant.vod_video, grant.vod_codec, grant.audio_track, grant.start_s) == ("transcode", "hevc", 0, 0)
    assert grant.url == FILM_URL and not grant.live
    assert len(fake.calls) == 1  # resolve probed because nothing was cached; the next one reuses it
    again = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", "audio_track": 1})).json()
    assert again["audio_track"] == 1 and again["default_audio_language"] == "eng"
    assert again["audio_language_required"] is False  # an explicit track is an explicit choice
    assert grant_of(api, again).audio_track == 1 and len(fake.calls) == 1
    bad = await api.post("/playback/resolve", json={"id": "lib:iptv:7", "audio_track": 5})
    assert bad.status_code == 400 and bad.json()["code"] == "audio_track_invalid"


async def test_web_copies_h264_and_plays_browser_ready_files_as_before(api: Api) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(probe("h264", "yuv420p"))
    d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    assert d["mode"] == "hls" and grant_of(api, d).vod_video == "copy" and d["audio_track"] == 1
    aac = [{"index": 0, "language": "eng", "codec": "aac", "channels": 2, "title": None, "default": True}]
    api.c.playback.probe = FakeProbe(probe("h264", "yuv420p", "mov,mp4,m4a,3gp,3g2,mj2", audio=aac))
    async with api.c.db.session() as session:
        row = (await session.execute(select(Title).where(Title.source_id == "iptv"))).scalar_one()
        row.extra = {"ext": "mp4"}
        await session.commit()
    d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    assert d["mode"] == "direct" and d["url"].endswith("/7.mp4")
    assert d["start_position_ms"] is None and d["audio_track"] is None and len(d["audio_tracks"]) == 1


async def test_start_position_restarts_the_session_at_a_five_second_step(api: Api) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(probe())
    d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", "start_position_ms": 1_234_567})).json()
    assert d["start_position_ms"] == 1_230_000 and grant_of(api, d).start_s == 1230
    # Without one, playback starts where this profile stopped.
    report = {"id": "lib:iptv:7", "position_ms": 62_000, "duration_ms": 5_400_500, "state": "paused"}
    assert (await api.post("/playback/progress", json=report)).status_code == 204
    d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    assert d["resume_position_ms"] == 62_000 and d["start_position_ms"] == 60_000
    past_the_end = {"id": "lib:iptv:7", "start_position_ms": 9_999_999_000}
    assert (await api.post("/playback/resolve", json=past_the_end)).json()["start_position_ms"] == 0


async def test_unprobeable_film_is_transcoded_and_android_tv_is_untouched(api: Api) -> None:
    await add_film(api)
    fake = api.c.playback.probe = FakeProbe(None)
    d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    grant = grant_of(api, d)
    assert d["mode"] == "hls" and (grant.vod_video, grant.vod_codec, grant.audio_track) == ("transcode", None, None)
    assert d["audio_tracks"] == [] and d["audio_track"] is None and d["start_position_ms"] == 0
    calls = len(fake.calls)
    tv = await api.post("/playback/resolve", json={"id": "lib:iptv:7", "client": "android_tv", "audio_track": 1})
    assert tv.status_code == 200, tv.text
    assert tv.json()["mode"] == "direct" and tv.json()["url"] == FILM_URL and tv.json()["start_position_ms"] is None
    assert len(fake.calls) == calls


async def test_never_proxy_policy_keeps_the_owner_decision(api: Api) -> None:
    await add_film(api, proxy="never")
    fake = api.c.playback.probe = FakeProbe(probe())
    d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    assert d["mode"] == "direct" and fake.calls == []


# ---------------------------------------------------------------- HLS sessions
class Process:
    def __init__(self, code: int | None = None) -> None:
        self.returncode = code
        self.stderr = asyncio.StreamReader()
        self.stopped = False
        if code is not None:
            self.stderr.feed_eof()

    def terminate(self) -> None:
        if self.returncode is None:
            self.stopped = True
            self.returncode = -15
            self.stderr.feed_eof()

    kill = terminate

    async def wait(self) -> int | None:
        return self.returncode


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


def local(url: str) -> str:
    return url.split("hub.test", 1)[1]


async def test_hls_session_is_keyed_by_audio_track_and_start(api: Api, spawned: list) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(probe())
    await api.patch("/settings", json={"transcode_hw": "nvenc"})

    async def play(**body: Any) -> tuple[str, str]:
        d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", **body})).json()
        playlist = await api.client.get(local(d["url"]))
        assert playlist.status_code == 200, playlist.text
        return d["url"], playlist.text

    url, text = await play()
    args, first = spawned[0]
    # No per-source transcode flag: the codec demands it and the setting picks the encoder.
    assert args[args.index("-c:v") + 1] == "h264_nvenc" and "0:a:1?" in args and "-ss" not in args
    assert "#EXT-X-START:TIME-OFFSET=0" in text
    segment = next(line for line in text.splitlines() if line and not line.startswith("#"))
    assert segment.startswith("/api/v1/stream/") and segment.endswith("/seg_00000.ts")
    await api.client.get(local(url))
    assert len(spawned) == 1  # polling the same playlist shares the session

    await play(audio_track=0, start_position_ms=600_000)
    args, second = spawned[1]
    assert "0:a:0?" in args and args[args.index("-ss") + 1] == "600"
    # The viewer's previous session is replaced at once; it does not wait out the switch grace.
    assert first.stopped and len(api.c.proxy._hls) == 1 and not second.stopped
    await api.c.proxy.shutdown()


async def test_finished_transcode_stays_served_and_frees_its_connection(api: Api, spawned: list) -> None:
    await add_film(api, max_connections=1)
    api.c.playback.probe = FakeProbe(probe("h264", "yuv420p"))
    d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    text = (await api.client.get(local(d["url"]))).text
    _, proc = spawned[0]
    [session] = api.c.proxy._hls.values()
    (session.workdir / "seg_00000.ts").write_bytes(b"\x47" * 188)
    assert api.c.proxy._source_use("iptv") == 1
    proc.returncode = 0  # ffmpeg reached the end of the film
    proc.stderr.feed_eof()
    assert api.c.proxy._source_use("iptv") == 0
    segment = next(line for line in text.splitlines() if line and not line.startswith("#"))
    assert (await api.client.get(segment)).content == b"\x47" * 188
    assert (await api.client.get(local(d["url"]))).status_code == 200 and len(spawned) == 1
    await api.c.proxy.shutdown()


async def test_failed_hardware_encoder_falls_back_and_logs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    labels: list[str] = []

    async def spawn(*args: Any, **kwargs: Any) -> Process:
        encoder = args[args.index("-c:v") + 1]
        hw_decode = "-hwaccel" in args
        labels.append(f"{encoder}{'+cuda' if hw_decode else ''}")
        if encoder == "h264_nvenc":
            return Process(code=234)  # no usable GPU: ffmpeg exits before writing a segment
        Path(args[-1]).write_text("#EXTM3U\n#EXTINF:4.000000,\nseg_00000.ts\n")
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    proxy = StreamProxy(lambda **kwargs: None, tmp_path)
    plan = VodPlan(video="transcode", hw="nvenc", codec="hevc", family="viewer")
    with caplog.at_level("WARNING"):
        session = await proxy.hls_session("film", FILM_URL, {}, live=False, transcode="none", vod=plan)
    assert labels == ["h264_nvenc+cuda", "h264_nvenc", "libx264"]
    assert session.label == "libx264" and (session.workdir / "index.m3u8").exists()
    assert [r.message for r in caplog.records].count("transcode fallback") == 2
    assert FILM_URL not in caplog.text and "s3cret" not in caplog.text
    await proxy.shutdown()


async def test_upstream_refusal_is_not_retried_with_another_encoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[Process] = []

    async def spawn(*args: Any, **kwargs: Any) -> Process:
        proc = Process()
        proc.stderr.feed_data(f"Error opening input file {FILM_URL}\nServer returned 403 Forbidden\n".encode())
        proc.returncode = 1
        proc.stderr.feed_eof()
        runs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    proxy = StreamProxy(lambda **kwargs: None, tmp_path)
    plan = VodPlan(video="transcode", hw="nvenc", codec="hevc", family="viewer")
    with pytest.raises(ConnectionError) as failure:
        await proxy.hls_session("film", FILM_URL, {}, live=False, transcode="none", vod=plan)
    assert len(runs) == 1 and "s3cret" not in str(failure.value)
    await proxy.shutdown()
