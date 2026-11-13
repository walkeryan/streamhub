"""Finished recordings play as ``rec:<id>``: the file with Range for the TV, probe + HLS for a browser."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.infra.models import Recording
from app.infra.settings import AppSettings
from app.services import recording_policy
from app.services.streams import VALID_TTL_S
from tests.conftest import Api
from tests.people import member
from tests.test_source_busy import Process
from tests.test_stream_sessions import Clock
from tests.test_web_vod import FakeProbe, probe

BODY = bytes(range(256)) * 40  # 10 240 bytes that say where they are


@pytest.fixture
def settings(settings: AppSettings, tmp_path: Path) -> AppSettings:
    root = tmp_path / "recordings"
    root.mkdir()
    return settings.model_copy(update={"recordings_dir": str(root)})


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    runs: list[list[str]] = []

    async def spawn(*args: Any, **kwargs: Any) -> Process:
        runs.append(list(args))
        playlist = Path(args[-1])
        playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.000000,\nseg_00000.ts\n")
        (playlist.parent / "seg_00000.ts").write_bytes(b"\x47" * 188)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return runs


async def recorded(api: Api, rid: str = "rec1", *, profile_id: str | None = None, **fields: Any) -> Recording:
    """A recording row and, unless ``path`` says otherwise, its file."""
    start = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)
    path = fields.pop("path", f"Evening News ({rid}).mkv")
    row = Recording(
        id=rid,
        profile_id=profile_id or api.profile_id,
        channel_id="ch:gone:1",
        channel_name="News One",
        programme_key=f"gone:1@{rid}",
        title=fields.pop("title", "Evening News"),
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        state=fields.pop("state", "done"),
        path=path,
        size_bytes=len(BODY) if path else None,
        **fields,
    )
    if path:
        target = Path(api.c.settings.recordings_dir) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(BODY)
    async with api.c.db.session() as s:
        s.add(row)
        await s.commit()
    return row


async def resolve(who: Any, rid: str = "rec1", client: str = "android_tv", **body: Any) -> httpx.Response:
    return await who.post("/playback/resolve", json={"id": f"rec:{rid}", "client": client, **body})


# ---------------------------------------------------------------- the TV: the file, with Range
async def test_tv_gets_the_file_through_the_proxy_with_range(api: Api) -> None:
    await recorded(api)
    r = await resolve(api)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["id"], d["mode"], d["mime"], d["live"]) == ("rec:rec1", "proxy", "video/x-matroska", False)
    assert (d["title"], d["subtitle"], d["resume_position_ms"]) == ("Evening News", "News One", 0)
    assert "/api/v1/stream/" in d["url"] and "recordings" not in d["url"] and d["audio_language_required"] is False
    grant = api.c.tokens.verify(d["url"].split("/stream/", 1)[1])
    assert grant is not None and (grant.content_ref, grant.kind, grant.url) == ("rec:rec1", "recording", "rec:rec1")

    whole = await api.client.get(d["url"])
    assert whole.status_code == 200 and whole.content == BODY
    assert whole.headers["accept-ranges"] == "bytes" and whole.headers["content-length"] == str(len(BODY))
    assert whole.headers["content-type"] == "video/x-matroska"
    part = await api.client.get(d["url"], headers={"Range": "bytes=1000-1999"})
    assert part.status_code == 206 and part.content == BODY[1000:2000]
    assert part.headers["content-range"] == f"bytes 1000-1999/{len(BODY)}"
    rest = await api.client.get(d["url"], headers={"Range": "bytes=10000-"})
    assert rest.status_code == 206 and rest.content == BODY[10000:]
    tail = await api.client.get(d["url"], headers={"Range": "bytes=-16"})
    assert tail.status_code == 206 and tail.content == BODY[-16:]
    beyond = await api.client.get(d["url"], headers={"Range": "bytes=999999-"})
    assert beyond.status_code == 416 and beyond.headers["content-range"] == f"bytes */{len(BODY)}"
    # It is the owner's stream like any other while it plays.
    owner = (await api.get("/auth/me")).json()["user"]["id"]
    assert [(p["id"], p["kind"]) for p in await api.c.sessions.now_playing(owner)] == [("rec:rec1", "recording")]


async def test_progress_is_kept_per_profile(api: Api) -> None:
    await recorded(api)
    report = {"id": "rec:rec1", "position_ms": 600_000, "duration_ms": 3_600_000, "state": "paused"}
    assert (await api.post("/playback/progress", json=report)).status_code == 204
    assert (await resolve(api)).json()["resume_position_ms"] == 600_000


# ---------------------------------------------------------------- a browser: probe, then HLS
async def test_browser_gets_hls_planned_from_a_probe_of_the_file(api: Api, ffmpeg: list[list[str]]) -> None:
    await recorded(api)
    api.c.playback.probe = FakeProbe(probe())  # HEVC with AC-3: nothing a browser decodes
    web = {"recovery_generation": api.c.proxy.recovery_epoch}
    r = await resolve(api, client="web", **web)
    assert r.status_code == 200, r.text
    d = r.json()
    path = str(Path(api.c.settings.recordings_dir) / "Evening News (rec1).mkv")
    assert api.c.playback.probe.calls == [(path, {})]
    assert (d["mode"], d["mime"], d["start_position_ms"], d["duration_ms"]) == (
        *("hls", "application/x-mpegURL"),
        *(0, 5_400_500),
    )
    assert [t["language"] for t in d["audio_tracks"]] == ["fra", "eng"] and d["audio_track"] == 1
    playlist = await api.client.get(d["url"])
    assert playlist.status_code == 200 and "#EXT-X-START:TIME-OFFSET=0" in playlist.text
    [run] = ffmpeg
    # ffmpeg reads the file from disk: no upstream, no reconnecting, nothing pinned.
    assert run[run.index("-i") + 1] == path and "-reconnect" not in run and not api.c.proxy._pins
    assert "libx264" in run and "0:a:1?" in run
    segment = next(line for line in playlist.text.splitlines() if line and not line.startswith("#"))
    assert (await api.client.get(segment)).content == b"\x47" * 188
    # The probe is kept for the file; a seek starts a new session at the offset.
    again = await resolve(api, client="web", **web, start_position_ms=1_200_000, audio_track=0)
    assert again.json()["start_position_ms"] == 1_200_000 and len(api.c.playback.probe.calls) == 1
    assert (await api.client.get(again.json()["url"])).status_code == 200
    assert ffmpeg[1][ffmpeg[1].index("-ss") + 1] == "1200" and "0:a:0?" in ffmpeg[1]

    # H.264 is copied; only the audio is encoded.
    await recorded(api, "rec2")
    api.c.playback.probe = FakeProbe(probe(video="h264", pix_fmt="yuv420p"))
    copied = (await resolve(api, "rec2", client="web", **web)).json()
    assert (await api.client.get(copied["url"])).status_code == 200
    assert ffmpeg[2][ffmpeg[2].index("-c:v") + 1] == "copy"
    await api.c.proxy.shutdown()


async def test_browser_without_a_probe_gets_a_transcode(api: Api, ffmpeg: list[list[str]]) -> None:
    await recorded(api)
    d = (await resolve(api, client="web", recovery_generation=api.c.proxy.recovery_epoch)).json()
    assert (d["mode"], d["audio_tracks"], d["duration_ms"]) == ("hls", [], None)
    assert (await api.client.get(d["url"])).status_code == 200 and "libx264" in ffmpeg[0]
    await api.c.proxy.shutdown()


# ---------------------------------------------------------------- who may play what
async def test_only_what_the_recordings_list_shows_can_be_played(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = await member(api, "family", "Viewer")
    await recorded(api, "mine")
    await recorded(api, "theirs", profile_id=viewer.profile_id)
    listed = [r["id"] for r in (await viewer.get("/recordings")).json()["recordings"]]
    assert listed == ["theirs"]
    assert (await resolve(viewer, "theirs")).status_code == 200
    hidden = await resolve(viewer, "mine")
    assert hidden.status_code == 404 and hidden.json()["message"] == "recording not found"
    # The owner sees everyone's recordings, and so plays them.
    assert (await resolve(api, "theirs")).status_code == 200
    assert (await resolve(api, "nope")).status_code == 404
    assert (await api.post("/playback/resolve", json={"id": "rec:", "client": "android_tv"})).status_code == 404

    monkeypatch.setattr(recording_policy, "can_view", lambda p: p.is_admin)
    refused = await resolve(viewer, "theirs")
    assert refused.status_code == 403 and refused.json()["code"] == "recording_forbidden"


async def test_a_recording_needs_a_file_to_play(api: Api) -> None:
    await recorded(api, "soon", state="scheduled", path=None)
    await recorded(api, "lost")
    (Path(api.c.settings.recordings_dir) / "Evening News (lost).mkv").unlink()
    await recorded(api, "partial", state="failed", error="The capture stopped before the programme ended.")
    await recorded(api, "outside", path="../outside.mkv")
    assert (await resolve(api, "soon")).status_code == 404
    assert (await resolve(api, "lost")).status_code == 404
    assert (await resolve(api, "outside")).status_code == 404
    # What a failed capture kept can still be watched.
    assert (await resolve(api, "partial")).status_code == 200
    # A file deleted after the grant was issued is gone for the grant too.
    d = (await resolve(api, "partial")).json()
    (Path(api.c.settings.recordings_dir) / "Evening News (partial).mkv").unlink()
    assert (await api.client.get(d["url"])).status_code == 404


async def test_has_file_says_which_recordings_play(api: Api) -> None:
    """``has_file`` is the answer ``rec:<id>`` playback would give, row by row."""
    await recorded(api, "soon", state="scheduled", path=None)
    await recorded(api, "running", state="recording")  # being written: not served yet
    await recorded(api, "done")
    await recorded(api, "partial", state="failed", error="The capture stopped before the programme ended.")
    await recorded(api, "nothing", state="failed", path=None, error="The channel never started.")
    await recorded(api, "missed", state="missed", path=None)
    await recorded(api, "lost")  # a size, and no file any more
    (Path(api.c.settings.recordings_dir) / "Evening News (lost).mkv").unlink()
    await recorded(api, "outside", path="../outside.mkv")
    listed = {r["id"]: r for r in (await api.get("/recordings")).json()["recordings"]}
    assert {rid: r["has_file"] for rid, r in listed.items()} == {
        "soon": False,
        "running": False,
        "done": True,
        "partial": True,
        "nothing": False,
        "missed": False,
        "lost": False,
        "outside": False,
    }
    # Size alone does not say it: a lost file keeps its size, and a capture in progress has one.
    assert listed["lost"]["size_bytes"] == len(BODY) and listed["running"]["size_bytes"] == len(BODY)
    for rid, row in listed.items():
        assert ((await resolve(api, rid)).status_code == 200) is row["has_file"], rid


async def test_recording_grants_are_revalidated_like_any_other(api: Api) -> None:
    clock = Clock()
    api.c.guard.clock = clock
    viewer = await member(api, "family", "Viewer")
    await recorded(api, "theirs", profile_id=viewer.profile_id)
    d = (await resolve(viewer, "theirs")).json()
    assert (await api.client.get(d["url"])).status_code == 200
    assert (await api.post(f"/users/{viewer.id}/suspend")).status_code == 200
    clock.now += VALID_TTL_S
    assert (await api.client.get(d["url"])).status_code == 401
