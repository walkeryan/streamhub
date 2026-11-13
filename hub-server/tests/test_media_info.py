"""``media_info`` on a playback descriptor: what the file is and how the server delivers it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.api import schemas
from app.infra.models import Title
from app.services.media_info import dynamic_range, encoder_kind, frame_rate, media_info, output_size
from app.services.media_probe import ENTRIES, parse_probe
from tests.conftest import Api
from tests.test_web_vod import FFPROBE_MKV, FILM_URL, FakeProbe, add_film, probe

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
# HEVC HDR10 in Matroska at 23.976 fps, 18 Mbit/s: what a probe of such a file now keeps.
UHD = {"codec": "hevc", "width": 3840, "height": 1600, "pix_fmt": "yuv420p10le", "fps": 23.976, "transfer": "smpte2084"}
UHD_FILM = {**probe(), "video": UHD, "bit_rate": 18_250_000}


def info(**kw: Any) -> dict[str, Any]:
    found = media_info(
        **{
            "source_name": "Manager",
            "probe": None,
            "audio_track": None,
            "mode": "hls",
            "video_plan": None,
            "hw": "none",
            **kw,
        }
    )
    assert found is not None
    # As a client receives it: the schema leaves out what is unknown.
    return schemas.MediaInfo(**found).model_dump(mode="json")


# ---------------------------------------------------------------- probe
def test_probe_keeps_frame_rate_transfer_and_bit_rate_when_the_file_says_them() -> None:
    assert "avg_frame_rate" in ENTRIES and "color_transfer" in ENTRIES and "bit_rate" in ENTRIES
    assert "filename" not in ENTRIES  # still nothing that names the upstream URL
    raw = json.loads(json.dumps(FFPROBE_MKV))
    raw["streams"][0].update(avg_frame_rate="24000/1001", color_transfer="smpte2084")
    raw["format"]["bit_rate"] = "18250000"
    parsed = parse_probe(raw)
    assert parsed is not None
    assert parsed["video"] == {
        "codec": "hevc",
        "width": 3840,
        "height": 2160,
        "pix_fmt": "yuv420p10le",
        "fps": 23.976,
        "transfer": "smpte2084",
    }
    assert parsed["bit_rate"] == 18_250_000
    # A file that says none of it is stored as before: nothing is guessed.
    plain = parse_probe(FFPROBE_MKV)
    assert (
        plain is not None and "bit_rate" not in plain and set(plain["video"]) == {"codec", "width", "height", "pix_fmt"}
    )


def test_frame_rate_and_dynamic_range_read_what_ffprobe_prints() -> None:
    assert frame_rate("24000/1001") == 23.976 and frame_rate("25/1") == 25.0 and frame_rate("50") == 50.0
    assert frame_rate("0/0") is None and frame_rate("N/A") is None and frame_rate(None) is None
    assert dynamic_range("smpte2084") == "hdr10" and dynamic_range("arib-std-b67") == "hlg"
    assert dynamic_range("bt709") == "sdr" and dynamic_range("smpte170m") == "sdr"
    assert dynamic_range(None) is None and dynamic_range("unknown") is None


def test_output_size_follows_the_transcode_scale_rule() -> None:
    assert output_size(1920, 1080) == (1920, 1080) and output_size(1280, 536) == (1280, 536)
    # Wider than 1080p is scaled to 1920 wide, the height kept in shape and even (ffmpeg's scale=1920:-2).
    assert output_size(3840, 2160) == (1920, 1080) and output_size(3840, 1600) == (1920, 800)
    assert output_size(4096, 1716) == (1920, 804)
    assert output_size(None, 1080) is None and output_size(1920, None) is None


def test_encoder_kind_names_who_encodes_only_for_a_transcode() -> None:
    assert encoder_kind("transcode", "nvenc") == "hardware" and encoder_kind("transcode", "vaapi") == "hardware"
    assert encoder_kind("transcode", "qsv") == "hardware" and encoder_kind("transcode", "none") == "software"
    assert encoder_kind("remux", "nvenc") is None and encoder_kind("direct", "nvenc") is None


# ---------------------------------------------------------------- the descriptor field
def test_media_info_for_a_transcoded_film() -> None:
    found = info(probe=UHD_FILM, audio_track=0, video_plan="transcode", hw="nvenc")
    assert found == {
        "source_name": "Manager",
        "container": "matroska",
        "bitrate_kbps": 18_250,
        "video": {"codec": "hevc", "width": 3840, "height": 1600, "frame_rate": 23.976, "dynamic_range": "hdr10"},
        "audio": {"codec": "ac3", "channels": 6, "language": "fra"},
        "delivery": "transcode",
        "encoder": "hardware",
        "output_width": 1920,
        "output_height": 800,
    }
    assert info(probe=UHD_FILM, video_plan="transcode", hw="none")["encoder"] == "software"


def test_media_info_for_copied_video_and_for_the_file_as_it_is() -> None:
    h264 = probe("h264", "yuv420p")
    remux = info(probe=h264, audio_track=1, video_plan="copy", hw="nvenc")
    # The video is not encoded, so nothing encodes it and it arrives at its own size.
    assert remux["delivery"] == "remux" and "encoder" not in remux
    assert (remux["output_width"], remux["output_height"]) == (1920, 1080)
    assert remux["audio"] == {"codec": "eac3", "channels": 2, "language": "eng"}
    direct = info(probe=h264, mode="direct")
    assert direct["delivery"] == "direct" and "encoder" not in direct
    assert direct["audio"]["language"] == "eng"  # nothing was chosen: the file's default track
    assert info(probe=h264, mode="proxy")["delivery"] == "direct"


def test_media_info_leaves_out_what_nobody_knows() -> None:
    # A channel has no probe: the source, and how its feed is delivered.
    assert info(mode="hls") == {"source_name": "Manager", "delivery": "remux"}
    assert info(mode="proxy") == {"source_name": "Manager", "delivery": "direct"}
    assert info(mode="hls", live_transcode=True, hw="vaapi") == {
        "source_name": "Manager",
        "delivery": "transcode",
        "encoder": "hardware",
    }
    # A film that could not be probed is transcoded blind.
    assert info(video_plan="transcode") == {"source_name": "Manager", "delivery": "transcode", "encoder": "software"}
    # A probe that knows little says little, and never null.
    sparse = {"video": {"codec": "h264"}, "audio": [{"codec": "aac", "language": "und"}], "container": None}
    assert info(probe=sparse, mode="direct", source_name=None) == {
        "video": {"codec": "h264"},
        "audio": {"codec": "aac"},
        "delivery": "direct",
    }
    assert media_info(source_name="x", probe=None, audio_track=None, mode="handoff", video_plan=None, hw="none") is None


def test_published_media_info_matches_the_backend() -> None:
    yaml = pytest.importorskip("yaml")
    published = yaml.safe_load(CONTRACT.read_text())["components"]["schemas"]
    spec = published["MediaInfo"]
    generated = schemas.MediaInfo.model_json_schema()
    assert set(spec["properties"]) == set(generated["properties"])
    assert spec["required"] == generated["required"] == ["delivery"]
    assert spec["properties"]["delivery"]["enum"] == ["direct", "remux", "transcode"]
    assert spec["properties"]["encoder"]["enum"] == ["hardware", "software"]
    for part, model in (("video", schemas.MediaInfoVideo), ("audio", schemas.MediaInfoAudio)):
        assert set(spec["properties"][part]["properties"]) == set(model.model_json_schema()["properties"])
    assert spec["properties"]["video"]["properties"]["dynamic_range"]["enum"] == ["sdr", "hdr10", "hlg"]
    field = published["PlaybackDescriptor"]["properties"]["media_info"]
    assert field["nullable"] is True and field["allOf"] == [{"$ref": "#/components/schemas/MediaInfo"}]
    assert "media_info" in schemas.PlaybackDescriptor.model_fields


# ---------------------------------------------------------------- through the API
async def test_resolve_describes_the_stream_without_naming_the_upstream(api: Api) -> None:
    await add_film(api)
    await api.patch("/settings", json={"transcode_hw": "nvenc"})
    api.c.playback.probe = FakeProbe(UHD_FILM)
    response = await api.post("/playback/resolve", json={"id": "lib:iptv:7", "client": "web"})
    assert response.status_code == 200, response.text
    found = response.json()["media_info"]
    assert found == {
        "source_name": "Manager",
        "container": "matroska",
        "bitrate_kbps": 18_250,
        "video": {"codec": "hevc", "width": 3840, "height": 1600, "frame_rate": 23.976, "dynamic_range": "hdr10"},
        "audio": {"codec": "eac3", "channels": 2, "language": "eng"},
        "delivery": "transcode",
        "encoder": "hardware",
        "output_width": 1920,
        "output_height": 800,
    }
    # Neutral facts only: nothing of the upstream URL, its host or its credentials.
    text = json.dumps(found)
    for secret in ("manager.example.test", "alice", "s3cret", "http", FILM_URL):
        assert secret not in text


async def test_resolve_reports_remux_direct_and_an_unprobed_file(api: Api) -> None:
    await add_film(api)
    api.c.playback.probe = FakeProbe(probe("h264", "yuv420p"))
    copied = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    assert copied["mode"] == "hls" and copied["media_info"]["delivery"] == "remux"
    assert "encoder" not in copied["media_info"] and copied["media_info"]["output_height"] == 1080
    # The TV plays the file itself: the cached probe still says what the file is, and nothing is probed for it.
    calls = len(api.c.playback.probe.calls)
    tv = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", "client": "android_tv"})).json()
    assert tv["mode"] == "direct" and tv["media_info"]["delivery"] == "direct"
    assert tv["media_info"]["video"]["codec"] == "h264" and len(api.c.playback.probe.calls) == calls
    # A file nobody could probe: only how it is delivered is known.
    async with api.c.db.session() as session:
        row = (await session.execute(select(Title).where(Title.source_id == "iptv"))).scalar_one()
        row.extra = {"ext": "mkv"}
        await session.commit()
    api.c.playback._probe_retry.clear()
    api.c.playback.probe = FakeProbe(None)
    blind = (await api.post("/playback/resolve", json={"id": "lib:iptv:7"})).json()
    assert blind["media_info"] == {"source_name": "Manager", "delivery": "transcode", "encoder": "software"}
