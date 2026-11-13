"""One upstream per watch room on a source that limits connections.

The rig of ``test_api_rooms`` with its film's source limited to one connection. ffmpeg is stubbed
(a playlist and one segment appear, the arguments are kept); the upstream is respx-mocked and
behaves like a panel that hands out a per-viewer session on redirect.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.infra.models import Source, Title
from app.proxy import stream_proxy
from app.proxy.stream_proxy import HlsSourceBusy, Viewer, VodPlan
from app.services.playback import StreamGrant
from app.services.room_hub import Connection, RoomHub, RoomTokens
from app.services.rooms import RoomRegistry
from tests import test_api_rooms, test_hls_admission
from tests.test_api_rooms import FILM_URL, T0, Rig, names, recv
from tests.test_upstream_session_pin import Panel, Process
from tests.test_web_vod import FakeProbe, probe

OTHER_URL = "http://streams.example/vod/2.mkv"

# Fixtures of the suites this one builds on.
rig = test_api_rooms.rig
harness = test_hls_admission.harness


class Ffmpeg:
    def __init__(self) -> None:
        self.runs: list[list[str]] = []
        self.procs: list[Process] = []

    @property
    def running(self) -> int:
        return sum(p.returncode is None for p in self.procs)

    def arg(self, run: int, flag: str) -> str | None:
        args = self.runs[run]
        return args[args.index(flag) + 1] if flag in args else None

    async def spawn(self, *args: Any, **kwargs: Any) -> Process:
        self.runs.append(list(args))
        playlist = Path(args[-1])
        playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.000000,\nseg_00000.ts\n")
        (playlist.parent / "seg_00000.ts").write_bytes(b"\x47" * 188)
        self.procs.append(Process())
        return self.procs[-1]


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> Ffmpeg:
    stub = Ffmpeg()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", stub.spawn)
    monkeypatch.setattr(stream_proxy, "HLS_ADMISSION_WAIT_S", 0.0)
    return stub


@pytest.fixture
def panel() -> Iterator[Panel]:
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        yield Panel(router, FILM_URL)


def limit(rig: Rig, connections: int | None = 1, **config: Any) -> None:
    async def apply() -> None:
        async with rig.c.db.session() as s:
            row = await s.get(Source, rig.source_id)
            assert row is not None
            row.hints = {**(row.hints or {}), "max_connections": connections}
            if config:
                stored = rig.c.crypto.decrypt_json(row.config_enc)
                row.config_enc = rig.c.crypto.encrypt_json({**stored, **config})
            await s.commit()

    rig.call(apply)


def playlist(rig: Rig, joined: dict[str, Any]) -> httpx.Response:
    return rig.client.get(joined["descriptor"]["url"])


def segment_url(text: str) -> str:
    return next(line for line in text.splitlines() if line and not line.startswith("#"))


def wait_for(check: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.01)


def use(rig: Rig) -> int:
    return int(rig.c.proxy._source_use(rig.source_id))


# ---------------------------------------------------------------------- one session for everyone
def test_two_joins_are_one_admission_and_one_upstream_open(rig: Rig, ffmpeg: Ffmpeg, panel: Panel) -> None:
    limit(rig)
    code = rig.room()["code"]
    guest = rig.profile("guest", "Guest")
    first = rig.join(code)
    second = rig.join(code, guest, client="android_tv")
    one, two = first["descriptor"], second["descriptor"]
    assert (one["mode"], two["mode"]) == ("hls", "hls") and one["url"] != two["url"]
    for d in (one, two):
        assert (d["start_position_ms"], d["stream_epoch"], d["resume_position_ms"]) == (0, 1, 0)
        assert d["mime"] == "application/x-mpegURL" and FILM_URL not in d["url"]
    # One ffmpeg, one of the source's connections, one upstream session for probe and play alike.
    assert len(ffmpeg.runs) == 1 and ffmpeg.running == 1 and use(rig) == 1
    assert list(rig.c.proxy._hls) == [f"room:{code}"]
    assert rig.c.proxy._pins[f"room:{code}"][0] == f"{FILM_URL}?session_id=s1"
    assert ffmpeg.arg(0, "-i") == f"{FILM_URL}?session_id=s1" and panel.unpinned == 1
    # Both members read the same session through their own grant.
    a, b = playlist(rig, first), playlist(rig, second)
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert "#EXT-X-START:TIME-OFFSET=0" in a.text
    assert segment_url(a.text).rsplit("/hls/", 1)[1] == segment_url(b.text).rsplit("/hls/", 1)[1]
    assert rig.client.get(segment_url(a.text)).content == b"\x47" * 188
    assert rig.client.get(segment_url(b.text)).status_code == 200
    assert len(ffmpeg.runs) == 1 and use(rig) == 1
    grants = [rig.c.tokens.verify(d["url"].split("/stream/", 1)[1].split("/", 1)[0]) for d in (one, two)]
    assert [g.room for g in grants] == [code, code] and grants[0].profile_id != grants[1].profile_id

    # Anyone else is told the room holds the connection.
    other = StreamGrant(
        rig.admin_id, rig.source_id, OTHER_URL, {}, "hls", False, None, False, **rig.call(identity, rig)
    )
    busy = rig.client.get(f"/api/v1/stream/{rig.c.tokens.issue(other)}/index.m3u8")
    assert busy.status_code == 409 and busy.json()["code"] == "source_busy", busy.text
    assert [(a["profile_name"], a["title"], a["kind"]) for a in busy.json()["active"]] == [
        ("Admin", "Watch room: Film", "vod")
    ]


async def identity(rig: Rig) -> dict[str, Any]:
    p = rig.c.auth.principal_from_access(rig.headers["Authorization"].removeprefix("Bearer "))
    return {
        "profile_version": p.token_version,
        "session_id": p.session_id,
        "user_id": p.user_id,
        "user_version": p.user_version,
    }


def test_seek_restarts_the_session_once_before_anyone_is_told(rig: Rig, ffmpeg: Ffmpeg, panel: Panel) -> None:
    limit(rig)
    code = rig.room()["code"]
    host = rig.join(code)
    guest = rig.join(code, rig.profile("guest", "Guest"))
    before = playlist(rig, host).text
    with rig.socket(code, host["member_token"]) as a, rig.socket(code, guest["member_token"]) as b:
        for ws in (a, b):
            assert names(recv(ws)) == ["Admin", "Guest"]
            opened = recv(ws)
            assert (opened["type"], opened["stream_epoch"], opened["stream_start_ms"]) == ("state", 1, 0)

        b.send_json({"type": "seek", "position_ms": 1_803_000})
        for ws in (a, b):
            told = recv(ws)
            assert (told["position_ms"], told["by"]) == (1_803_000, "Guest")
            # By the time anyone hears of the seek, the new session is what the playlist serves.
            assert (told["stream_epoch"], told["stream_start_ms"]) == (2, 1_800_000)
        assert len(ffmpeg.runs) == 2 and ffmpeg.running == 1 and use(rig) == 1
        assert ffmpeg.procs[0].returncode is not None and ffmpeg.arg(1, "-ss") == "1800"
        # Same upstream session, same playlist URL for each member, new segments.
        assert ffmpeg.arg(1, "-i") == f"{FILM_URL}?session_id=s1" and panel.unpinned == 1
        after = playlist(rig, host).text
        assert segment_url(after) != segment_url(before)
        assert rig.client.get(segment_url(after)).status_code == 200
        assert rig.client.get(segment_url(before)).status_code == 403
        assert segment_url(playlist(rig, guest).text).rsplit("/hls/", 1)[1] == segment_url(after).rsplit("/hls/", 1)[1]

        # A seek the running session already starts at costs nothing.
        a.send_json({"type": "seek", "position_ms": 1_801_000})
        for ws in (a, b):
            told = recv(ws)
            assert (told["position_ms"], told["stream_epoch"], told["stream_start_ms"]) == (1_801_000, 2, 1_800_000)
        a.send_json({"type": "play"})
        for ws in (a, b):
            assert recv(ws)["stream_epoch"] == 2
        assert len(ffmpeg.runs) == 2

        # Someone arriving later is seated on the session as it is now.
        rig.clock.ms += 20_000
        late = rig.join(code, rig.profile("late", "Late"))["descriptor"]
        assert (late["stream_epoch"], late["start_position_ms"], late["resume_position_ms"]) == (
            2,
            1_800_000,
            1_821_000,
        )
        assert len(ffmpeg.runs) == 2 and use(rig) == 1


def test_first_join_starts_at_the_room_position_and_honours_its_audio_track(
    rig: Rig,
    ffmpeg: Ffmpeg,
    panel: Panel,
) -> None:
    limit(rig)
    rig.c.playback.probe = FakeProbe(probe())
    code = rig.room(variant_id=rig.film, audio_track=0)["code"]
    d = rig.join(code, client="android_tv")["descriptor"]
    # The probe went through the room's upstream session; the film's first track is not its default.
    assert rig.c.playback.probe.calls[0][0] == f"{FILM_URL}?session_id=s1" and panel.unpinned == 1
    assert (d["audio_track"], d["audio_languages"], len(d["audio_tracks"])) == (0, ["fra"], 2)
    assert "0:a:0?" in ffmpeg.runs[0] and "libx264" in ffmpeg.runs[0]  # HEVC: transcoded once for everyone
    rig.req("DELETE", f"/rooms/{code}")
    wait_for(lambda: use(rig) == 0)

    # A room that is already an hour in starts its session there.
    room = rig.c.rooms.registry.get(rig.room()["code"])
    assert room is not None
    room.state.position_ms = 3_602_000
    d = rig.join(room.code)["descriptor"]
    assert (d["start_position_ms"], d["resume_position_ms"], d["audio_track"]) == (3_600_000, 3_602_000, 1)
    assert ffmpeg.arg(1, "-ss") == "3600" and "0:a:1?" in ffmpeg.runs[1]


def test_last_leave_and_room_close_release_the_session(rig: Rig, ffmpeg: Ffmpeg, panel: Panel) -> None:
    limit(rig)
    code = rig.room()["code"]
    guest = rig.profile("guest", "Guest")
    with rig.enter(code) as a:
        with rig.enter(code, guest) as b:
            assert [recv(a)["type"] for _ in range(3)] == ["members", "state", "members"]
            rig.left(b, code, ["Admin"])
        # One member is still watching.
        assert ffmpeg.running == 1 and use(rig) == 1 and rig.c.room_playback.session(code) is not None
        rig.left(a, code, [])
    wait_for(lambda: rig.c.room_playback.session(code) is None and not rig.c.proxy._hls)
    assert ffmpeg.running == 0 and use(rig) == 0 and f"room:{code}" not in rig.c.proxy._pins
    assert f"room:{code}" not in rig.c.proxy._kept

    # The room is still there: whoever comes back starts a new session, and closing the room ends it.
    again = rig.join(code)
    assert again["descriptor"]["stream_epoch"] == 1 and len(ffmpeg.runs) == 2 and use(rig) == 1
    assert rig.req("DELETE", f"/rooms/{code}").status_code == 204
    wait_for(lambda: use(rig) == 0)
    assert ffmpeg.running == 0 and rig.c.room_playback.session(code) is None
    gone = playlist(rig, again)
    assert gone.status_code == 404 and gone.json()["code"] == "room_not_found"
    assert len(ffmpeg.runs) == 2


def test_members_who_never_connect_do_not_hold_the_connection(rig: Rig, ffmpeg: Ffmpeg, panel: Panel) -> None:
    limit(rig)
    code = rig.room()["code"]
    rig.join(code)
    assert use(rig) == 1
    rig.tick(20_000)  # silent for twenty seconds: dropped, and the room is empty
    wait_for(lambda: use(rig) == 0)
    assert rig.c.room_playback.session(code) is None


def test_library_title_is_resolved_per_member_without_admission(rig: Rig, ffmpeg: Ffmpeg, panel: Panel) -> None:
    code = rig.room()["code"]  # the source has no connection limit
    resolved = rig.req(
        "POST", "/playback/resolve", json={"id": rig.film, "recovery_generation": rig.c.proxy.recovery_epoch}
    ).json()
    joined = rig.join(code)
    assert joined["descriptor"] == resolved and joined["descriptor"]["url"] == FILM_URL
    assert joined["descriptor"]["stream_epoch"] is None
    with rig.socket(code, joined["member_token"]) as ws:
        assert names(recv(ws)) == ["Admin"]
        assert "stream_epoch" not in recv(ws)
        ws.send_json({"type": "seek", "position_ms": 60_000})
        assert "stream_epoch" not in recv(ws)
    assert ffmpeg.runs == [] and use(rig) == 0 and not rig.c.proxy._pins
    assert rig.c.room_playback.session(code) is None and panel.seen == []


def test_always_proxied_source_shares_one_session_too(rig: Rig, ffmpeg: Ffmpeg, panel: Panel) -> None:
    limit(rig, None, proxy="always")
    code = rig.room()["code"]
    rig.join(code)
    rig.join(code, rig.profile("guest", "Guest"))
    assert len(ffmpeg.runs) == 1 and list(rig.c.proxy._hls) == [f"room:{code}"]


def test_member_without_access_is_refused_while_the_other_keeps_playing(
    rig: Rig,
    ffmpeg: Ffmpeg,
    panel: Panel,
) -> None:
    limit(rig)
    code = rig.room()["code"]
    host = rig.join(code)
    created = rig.req("POST", "/users", json={"name": "No films", "role_id": "family"})
    assert created.status_code == 201, created.text
    patched = rig.req("PATCH", f"/users/{created.json()['id']}", json={"denials": ["watch.vod"]})
    assert patched.status_code == 200, patched.text
    login = rig.client.post("/api/v1/auth/login", json={"profile_id": created.json()["profile_id"]})
    refused = rig.join_raw(code, {"Authorization": f"Bearer {login.json()['access_token']}"})
    assert refused.status_code == 403 and refused.json()["code"] == "forbidden", refused.text
    assert refused.json()["missing"] == ["watch.vod"]
    assert rig.members(code) == ["Admin"]
    # Nothing happened to the room's session.
    assert len(ffmpeg.runs) == 1 and ffmpeg.running == 1 and use(rig) == 1
    assert playlist(rig, host).status_code == 200


def test_busy_source_refuses_the_first_join_with_the_usual_answer(rig: Rig, ffmpeg: Ffmpeg, panel: Panel) -> None:
    limit(rig)

    async def add_other() -> None:
        async with rig.c.db.session() as s:
            s.add(Title(source_id=rig.source_id, ext_id="vod2", kind="vod", title="Other", norm_title="other"))
            await s.commit()

    rig.call(add_other)
    watching = StreamGrant(
        rig.admin_id,
        rig.source_id,
        OTHER_URL,
        {},
        "hls",
        True,
        None,
        False,
        title="Evening News",
        kind="channel",
        **rig.call(identity, rig),
    )
    assert rig.client.get(f"/api/v1/stream/{rig.c.tokens.issue(watching)}/index.m3u8").status_code == 200
    code = rig.room()["code"]
    refused = rig.join_raw(code)
    assert refused.status_code == 409 and refused.json()["code"] == "source_busy", refused.text
    assert [a["title"] for a in refused.json()["active"]] == ["Evening News"]
    # Nothing is left behind for a room that never started.
    assert rig.c.room_playback.session(code) is None and f"room:{code}" not in rig.c.proxy._kept
    assert rig.members(code) == [] and use(rig) == 1


# ---------------------------------------------------------------------- the proxy keeps what a room holds
async def test_kept_session_is_neither_evicted_for_another_viewer_nor_named_twice(harness: Any) -> None:
    proxy, clock, processes, _ = harness
    plan = VodPlan(video="copy", start_s=0, family="room:ABC")
    room = Viewer("host", "Watch room: Film", "movie", "lib:s:1")

    async def seat(sid: str = "epoch-1") -> Any:
        return await proxy.hls_session(
            "room:ABC", FILM_URL, {}, live=False, transcode="none", source_id="s", max_connections=1, vod=plan,
            viewer=room, sid=sid,
        )  # fmt: skip

    first = await seat()
    proxy.keep("room:ABC")
    assert await seat() is first and first.sid == "epoch-1" and len(processes) == 1
    # Nobody has polled it for a long time: another viewer still does not get its connection.
    clock.now += 120
    with pytest.raises(HlsSourceBusy):
        await proxy.hls_session("other", OTHER_URL, {}, live=True, transcode="none", source_id="s", max_connections=1)
    assert proxy.hls_running("room:ABC") is first and not processes[0].stopped
    # …and whoever is turned away is still told who holds it.
    assert [v.title for v in proxy.active_viewers("s")] == ["Watch room: Film"]
    assert await seat() is first
    # Stopped and started again under the same key, it is a new session with a new id.
    await proxy.stop_hls("room:ABC")
    assert processes[0].stopped and proxy.hls_running("room:ABC") is None
    second = await seat("epoch-2")
    assert second is not first and second.sid == "epoch-2" and second.workdir != first.workdir
    await proxy.stop_hls("room:ABC", forget=True)
    assert not proxy._kept and not proxy._hls
    await proxy.hls_session("other", OTHER_URL, {}, live=True, transcode="none", source_id="s", max_connections=1)
    await proxy.shutdown()


# ---------------------------------------------------------------------- the hub keeps the room's order
class Outbox:
    def __init__(self, hub: RoomHub, room: Any, member: Any) -> None:
        self.conn: Connection = hub.attach(room, member)

    def drain(self) -> list[dict[str, Any]]:
        out = []
        while not self.conn._outbox.empty():
            item = self.conn._outbox.get_nowait()
            assert isinstance(item, dict)
            out.append(item)
        return out


async def test_events_wait_behind_a_seek_that_is_still_being_prepared(container: Any) -> None:
    clock = {"ms": T0}
    hub = RoomHub(RoomRegistry(), RoomTokens(container.crypto), clock=lambda: clock["ms"])
    room = hub.create("lib:s:1", host_profile_id="p1", host_name="Host")
    member = hub.join(room, "p1", "Host")
    box = Outbox(hub, room, member)
    box.drain()
    ready = asyncio.Event()
    prepared: list[int] = []

    async def prepare() -> None:
        await ready.wait()
        prepared.append(room.state.position_ms)

    hub.before_seek = lambda _room: prepare()
    hub.state_extra = lambda _room: {"stream_epoch": len(prepared) + 1}
    hub.receive(box.conn, '{"type": "seek", "position_ms": 50000}')
    hub.receive(box.conn, '{"type": "play"}')
    hub.receive(box.conn, '{"type": "seek", "position_ms": 90000}')
    await asyncio.sleep(0)
    assert box.drain() == []  # nothing is said while the first seek is being prepared
    hub.receive(box.conn, '{"type": "ping", "t": 1}')  # a ping is not the room's business
    assert [m["type"] for m in box.drain()] == ["pong"]
    ready.set()
    for _ in range(5):
        await asyncio.sleep(0)
    told = box.drain()
    assert [(m["type"], m["position_ms"], m["playing"]) for m in told] == [
        ("state", 50_000, False),
        ("state", 50_000, True),
        ("state", 90_000, True),
    ]
    assert [m["stream_epoch"] for m in told] == [2, 2, 3] and prepared == [90_000, 90_000]
    # With nothing waiting, events go out at once again.
    hub.before_seek = None
    hub.receive(box.conn, '{"type": "pause"}')
    assert [m["type"] for m in box.drain()] == ["state"]
    await hub.shutdown()


async def test_a_failed_preparation_still_announces_the_seek(container: Any) -> None:
    hub = RoomHub(RoomRegistry(), RoomTokens(container.crypto), clock=lambda: T0)
    room = hub.create("lib:s:1", host_profile_id="p1", host_name="Host")
    box = Outbox(hub, room, hub.join(room, "p1", "Host"))
    box.drain()

    async def fail() -> None:
        raise ConnectionError("upstream gone")

    hub.before_seek = lambda _room: fail()
    hub.receive(box.conn, '{"type": "seek", "position_ms": 50000}')
    for _ in range(5):
        await asyncio.sleep(0)
    assert [(m["type"], m["position_ms"]) for m in box.drain()] == [("state", 50_000)]
    await hub.shutdown()
