"""Watch rooms over HTTP and WebSocket: two members, one clock (a fake one the test advances)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any

import anyio
import httpx
import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.container import Container
from app.domain.entities import StreamTarget
from app.infra.models import Profile, Title
from app.infra.settings import AppSettings
from app.main import create_app
from app.services import rooms as R
from app.services.room_hub import RoomHub, RoomTokens
from app.services.rooms import RoomRegistry
from tests.conftest import ADMIN_PASSWORD, FIXTURES

T0 = 1_700_000_000_000
FILM_URL = "http://streams.example/vod/1.mp4"


class FakeClock:
    def __init__(self) -> None:
        self.ms = T0

    def __call__(self) -> int:
        return self.ms


class Rig:
    """A TestClient (HTTP and WebSocket on one event loop) with the admin signed in and a film to watch."""

    def __init__(self, client: TestClient, c: Container, clock: FakeClock) -> None:
        self.client = client
        self.c = c
        self.clock = clock
        self.headers: dict[str, str] = {}
        self.admin_id = ""
        self.film = ""
        self.source_id = ""

    def call(self, fn: Any, *args: Any) -> Any:
        """Run ``fn`` on the application's event loop."""
        assert self.client.portal is not None
        return self.client.portal.call(fn, *args)

    def req(self, method: str, url: str, headers: dict[str, str] | None = None, **kw: Any) -> httpx.Response:
        return self.client.request(method, "/api/v1" + url, headers=headers or self.headers, **kw)

    def tick(self, advance_ms: int = 0) -> None:
        self.clock.ms += advance_ms
        self.call(self.c.rooms.tick)

    def profile(self, id: str, name: str, **fields: Any) -> dict[str, str]:
        """Create a profile and return its bearer header."""

        async def add() -> Profile:
            async with self.c.db.session() as s:
                s.add(Profile(id=id, name=name, **fields))
                await s.commit()
            return await self.c.auth.get_profile(id)

        return {"Authorization": "Bearer " + self.c.auth.issue(self.call(add))["access_token"]}

    def room(self, ref: str | None = None, headers: dict[str, str] | None = None, **body: Any) -> dict[str, Any]:
        r = self.req("POST", "/rooms", headers, json={"id": ref or self.film, **body})
        assert r.status_code == 201, r.text
        out: dict[str, Any] = r.json()
        return out

    def join_raw(self, code: str, headers: dict[str, str] | None = None, **body: Any) -> httpx.Response:
        body = {"client": "web", "recovery_generation": self.c.proxy.recovery_epoch, **body}
        return self.req("POST", f"/rooms/{code}/join", headers, json=body)

    def join(self, code: str, headers: dict[str, str] | None = None, **body: Any) -> dict[str, Any]:
        r = self.join_raw(code, headers, **body)
        assert r.status_code == 200, r.text
        out: dict[str, Any] = r.json()
        return out

    def socket(self, code: str, token: str) -> AbstractContextManager[WebSocketTestSession]:
        return self.client.websocket_connect(f"/api/v1/rooms/{code}/ws?member_token={token}")

    def members(self, code: str) -> list[str]:
        return [m["name"] for m in self.req("GET", f"/rooms/{code}").json()["members"]]

    def left(self, ws: WebSocketTestSession, code: str, remaining: list[str]) -> None:
        """Say goodbye on ``ws`` and wait until the room has noticed."""
        ws.close(1000)
        deadline = time.monotonic() + 5
        while self.members(code) != remaining:
            assert time.monotonic() < deadline, self.members(code)
            time.sleep(0.01)

    def enter(self, code: str, headers: dict[str, str] | None = None) -> AbstractContextManager[WebSocketTestSession]:
        return self.socket(code, self.join(code, headers)["member_token"])


@pytest.fixture
def rig(settings: AppSettings, container: Container) -> Iterator[Rig]:
    app = create_app(settings, container=container, start_background=False)
    clock = FakeClock()
    container.rooms.clock = clock
    with TestClient(app, base_url="http://hub.test") as client:
        rig = Rig(client, container, clock)
        admin = next(p for p in client.get("/api/v1/profiles").json() if p["is_admin"])
        r = client.post("/api/v1/auth/login", json={"profile_id": admin["id"], "password": ADMIN_PASSWORD})
        rig.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        rig.admin_id = admin["id"]
        assert rig.req("POST", "/disclaimer/accept", json={"version": "1.0"}).status_code == 204
        body = {"type": "m3u", "name": "Test M3U", "config": {"file": (FIXTURES / "sample.m3u").read_text()}}
        r = rig.req("POST", "/sources", json=body)
        assert r.status_code == 201, r.text
        rig.source_id = r.json()["id"]
        rig.call(container.sync.wait_idle)
        rig.film = f"lib:{rig.source_id}:vod1"

        async def add_film() -> None:
            async with container.db.session() as s:
                s.add(
                    Title(
                        source_id=rig.source_id,
                        ext_id="vod1",
                        kind="vod",
                        title="Film",
                        norm_title="film",
                        duration_ms=6_000_000,
                        extra={"stream_url": FILM_URL},
                    )
                )
                await s.commit()

        rig.call(add_film)

        # The m3u adapter cannot play titles; give it the playback half of the provider contract.
        async def stream_for_item(item: Any) -> StreamTarget:
            return StreamTarget(url=item.stream_url, mime="video/mp4", duration_ms=item.duration_ms)

        real = container.sources.provider

        def provider(row: Any) -> Any:
            p = real(row)
            if not hasattr(p, "stream_for_item"):
                p.stream_for_item = stream_for_item
            return p

        container.sources.provider = provider  # type: ignore[method-assign]
        yield rig


def recv(ws: WebSocketTestSession, timeout: float = 5.0) -> dict[str, Any]:
    """The next server message, failing (not hanging) when none arrives."""

    async def get() -> Any:
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    message = ws.portal.call(get)
    ws._raise_on_close(message)
    out: dict[str, Any] = json.loads(message["text"])
    return out


def closed_with(ws: WebSocketTestSession) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        recv(ws)
    return exc.value.code


def quiet(ws: WebSocketTestSession, rig: Rig) -> None:
    """Nothing is queued for this socket: the answer to a ping is the very next message."""
    ws.send_json({"type": "ping", "t": -1})
    assert recv(ws) == {"type": "pong", "t": -1, "server_ms": rig.clock.ms}


def state(
    playing: bool, position_ms: int | None, as_of_ms: int, by: str | None, locked: bool = False
) -> dict[str, Any]:
    return {
        "type": "state",
        "playing": playing,
        "position_ms": position_ms,
        "as_of_ms": as_of_ms,
        "by": by,
        "controls_locked": locked,
    }


def names(message: dict[str, Any]) -> list[str]:
    assert message["type"] == "members", message
    return [m["name"] + ("*" if m["buffering"] else "") for m in message["members"]]


# ---------------------------------------------------------------------- REST
def test_create_show_and_delete_room(rig: Rig) -> None:
    room = rig.room(variant_id=rig.film, audio_track=1)
    code = room["code"]
    assert len(code) == 6 and set(code) <= set(R.CODE_ALPHABET) and room["join_path"] == f"/room/{code}"
    assert (room["id"], room["live"], room["variant_id"], room["audio_track"]) == (rig.film, False, rig.film, 1)
    assert (room["host_name"], room["is_host"], room["controls_locked"], room["members"]) == ("Admin", True, False, [])
    assert room["state"] == {"playing": False, "position_ms": 0, "as_of_ms": T0} and room["server_ms"] == T0
    assert (room["title"]["id"], room["title"]["title"], room["title"]["kind"]) == (rig.film, "Film", "vod")
    assert room["max_members"] == 8 and room["waiting_for"] is None

    guest = rig.profile("guest", "Guest")
    shown = rig.req("GET", f"/rooms/{code.lower()}", guest)
    assert shown.status_code == 200 and shown.json() == {**room, "is_host": False}
    assert rig.req("GET", f"/rooms/{code}", {"Authorization": "Bearer nope"}).status_code == 401
    assert rig.req("POST", "/rooms", {"Authorization": "Bearer nope"}, json={"id": rig.film}).status_code == 401

    # only the host or an administrator may close it
    r = rig.req("DELETE", f"/rooms/{code}", guest)
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert rig.req("DELETE", f"/rooms/{code}").status_code == 204
    for method, url in [("GET", f"/rooms/{code}"), ("DELETE", f"/rooms/{code}"), ("POST", f"/rooms/{code}/join")]:
        r = rig.req(method, url, json={} if method == "POST" else None)
        assert r.status_code == 404 and r.json()["code"] == "room_not_found", (method, r.text)
    theirs = rig.room(headers=guest)
    assert theirs["host_name"] == "Guest" and rig.req("DELETE", f"/rooms/{theirs['code']}").status_code == 204


def test_room_needs_a_title_that_exists(rig: Rig) -> None:
    for ref in ("lib:nope:1", "nonsense", "trailer:" + rig.film):
        r = rig.req("POST", "/rooms", json={"id": ref})
        assert r.status_code == 404, (ref, r.text)
    assert rig.req("POST", "/rooms", json={}).status_code == 422
    assert len(rig.c.rooms.registry) == 0


def test_join_gives_a_member_token_and_a_descriptor(rig: Rig) -> None:
    code = rig.room()["code"]
    resolved = rig.req(
        "POST", "/playback/resolve", json={"id": rig.film, "recovery_generation": rig.c.proxy.recovery_epoch}
    ).json()
    joined = rig.join(code)
    assert joined["descriptor"] == resolved and joined["descriptor"]["url"] == FILM_URL
    assert joined["ws_path"] == f"/api/v1/rooms/{code}/ws" and joined["expires_in_seconds"] == 7200
    shown = rig.req("GET", f"/rooms/{code}").json()
    assert shown["members"] == [{"id": joined["member_id"], "name": "Admin", "host": True, "buffering": False}]

    # the member token opens this room's socket and nothing else
    r = rig.req("GET", "/favorites", {"Authorization": f"Bearer {joined['member_token']}"})
    assert r.status_code == 401
    # the same playback rules as /playback/resolve: an outdated web client is told to reload
    r = rig.req("POST", f"/rooms/{code}/join", json={"client": "web"})
    assert r.status_code == 403 and r.json()["code"] == "playback_reload_required"
    assert rig.req("POST", f"/rooms/{code}/join", json={"client": "android_tv"}).status_code == 200


def test_join_starts_the_member_at_the_room_position(rig: Rig) -> None:
    code = rig.room()["code"]
    with rig.enter(code) as host:
        host.send_json({"type": "seek", "position_ms": 600_000})
        host.send_json({"type": "play"})
        assert [recv(host)["type"] for _ in range(4)] == ["members", "state", "state", "state"]
        rig.clock.ms += 30_000
        late = rig.join(code, rig.profile("guest", "Guest"))
        assert late["descriptor"]["resume_position_ms"] == 630_000
        # their own history does not decide where they start; the paused room does
        host.send_json({"type": "pause"})
        assert [recv(host)["type"] for _ in range(2)] == ["members", "state"]
        rig.clock.ms += 30_000
        assert rig.join(code)["descriptor"]["resume_position_ms"] == 630_000


def test_resolver_refusals_reach_the_member_unchanged(rig: Rig) -> None:
    resolve = {"id": rig.film, "recovery_generation": rig.c.proxy.recovery_epoch}
    # 404: the room's variant is not one of the title's
    code = rig.room(variant_id="lib:other:zzz")["code"]
    expected = rig.req("POST", "/playback/resolve", json={**resolve, "variant_id": "lib:other:zzz"})
    r = rig.join_raw(code)
    assert (r.status_code, r.json()) == (404, expected.json())
    # 409: this profile's audio preference cannot be verified for the title
    code = rig.room()["code"]
    assert rig.req("PATCH", f"/profiles/{rig.admin_id}", json={"languages": ["fr"]}).status_code == 200
    expected = rig.req("POST", "/playback/resolve", json=resolve)
    r = rig.join_raw(code)
    assert (r.status_code, r.json()) == (409, expected.json()) and r.json()["code"] == "audio_selection_required"
    assert rig.req("GET", f"/rooms/{code}").json()["members"] == []


def test_member_who_may_not_watch_the_title_is_refused(rig: Rig) -> None:
    from tests.test_auth_policy_sessions import enroll

    version = rig.call(enroll, SimpleNamespace(c=rig.c, profile_id=rig.admin_id))
    owner = rig.call(rig.c.auth.get_profile, rig.admin_id)
    rig.headers = {"Authorization": "Bearer " + rig.c.auth.issue(owner, owner_pin_version=version)["access_token"]}
    policy = {"restricted": True, "allowed_movie_codes": ["G", "PG"], "allowed_tv_codes": ["TV-G"]}
    child = rig.profile("child", "Child", kids=True, content_policy=policy)

    code = rig.room()["code"]  # no rating evidence: fine for the owner, blocked for the restricted profile
    assert rig.join_raw(code).status_code == 200
    expected = rig.req("POST", "/playback/resolve", child, json={"id": rig.film, "client": "android_tv"})
    r = rig.join_raw(code, child)
    assert (r.status_code, r.json()) == (403, expected.json()) and r.json()["code"] == "content_restricted"
    r = rig.req("GET", f"/rooms/{code}", child)  # the join screen must not describe it either
    assert r.status_code == 403 and r.json()["code"] == "content_restricted"
    r = rig.req("POST", "/rooms", child, json={"id": rig.film})
    assert r.status_code == 403 and r.json()["code"] == "content_restricted"
    assert [m["name"] for m in rig.req("GET", f"/rooms/{code}").json()["members"]] == ["Admin"]


def test_title_that_opens_in_another_app_cannot_be_watched_in_a_room(rig: Rig) -> None:
    code = rig.room()["code"]

    async def handoff(*args: Any, **kw: Any) -> dict[str, Any]:
        return {"id": rig.film, "mode": "handoff", "title": "Film"}

    rig.c.playback.resolve = handoff  # type: ignore[method-assign]
    r = rig.join_raw(code)
    assert r.status_code == 409 and r.json()["code"] == "room_unsupported"
    assert rig.members(code) == []


def test_at_most_twenty_rooms(rig: Rig) -> None:
    codes = [rig.room()["code"] for _ in range(20)]
    r = rig.req("POST", "/rooms", json={"id": rig.film})
    assert r.status_code == 409 and r.json()["code"] == "room_limit"
    assert rig.req("DELETE", f"/rooms/{codes[0]}").status_code == 204
    rig.room()


def test_at_most_eight_members(rig: Rig) -> None:
    code = rig.room()["code"]
    for _ in range(8):
        rig.join(code)
    r = rig.join_raw(code)
    assert r.status_code == 409 and r.json()["code"] == "room_full"
    assert len(rig.req("GET", f"/rooms/{code}").json()["members"]) == 8
    rig.tick(20_000)  # none of them ever opened the socket
    assert rig.req("GET", f"/rooms/{code}").json()["members"] == []
    rig.join(code)


# ---------------------------------------------------------------------- WebSocket
def test_two_members_play_pause_and_seek_together(rig: Rig) -> None:
    code = rig.room()["code"]
    guest = rig.profile("guest", "Guest")
    with rig.enter(code) as a:
        assert names(recv(a)) == ["Admin"]
        assert recv(a) == state(False, 0, T0, None)
        joined = rig.join(code, guest)
        assert names(recv(a)) == ["Admin", "Guest"]  # joining is announced before the socket opens
        with rig.socket(code, joined["member_token"]) as b:
            assert names(recv(b)) == ["Admin", "Guest"]
            assert recv(b) == state(False, 0, T0, None)

            rig.clock.ms += 1_000
            a.send_json({"type": "play"})
            for ws in (a, b):
                assert recv(ws) == state(True, 0, T0 + 1_000, "Admin")
            rig.clock.ms += 4_000
            b.send_json({"type": "pause"})
            for ws in (a, b):
                assert recv(ws) == state(False, 4_000, T0 + 5_000, "Guest")
            b.send_json({"type": "seek", "position_ms": 90_000})
            for ws in (a, b):
                assert recv(ws) == state(False, 90_000, T0 + 5_000, "Guest")
            a.send_json({"type": "play"})
            a.send_json({"type": "play"})  # already playing: nothing to tell
            for ws in (a, b):
                assert recv(ws) == state(True, 90_000, T0 + 5_000, "Admin")

            # clock sync is private to the member who asked
            rig.clock.ms += 250
            b.send_json({"type": "ping", "t": 12345.5})
            assert recv(b) == {"type": "pong", "t": 12345.5, "server_ms": T0 + 5_250}
            quiet(a, rig)

            shown = rig.req("GET", f"/rooms/{code}").json()
            assert shown["state"] == {"playing": True, "position_ms": 90_000, "as_of_ms": T0 + 5_000}
            assert shown["server_ms"] == T0 + 5_250
            b.close(1000)  # a goodbye: the guest has left, and the room says so at once
            assert names(recv(a)) == ["Admin"]


def test_lock_leaves_the_controls_to_the_host(rig: Rig) -> None:
    code = rig.room()["code"]
    guest = rig.profile("guest", "Guest")
    with rig.enter(code) as a, rig.enter(code, guest) as b:
        assert [recv(a)["type"] for _ in range(3)] == ["members", "state", "members"]
        assert [recv(b)["type"] for _ in range(2)] == ["members", "state"]

        b.send_json({"type": "lock", "on": True})
        assert recv(b)["code"] == "host_only"
        a.send_json({"type": "lock", "on": True})
        for ws in (a, b):
            assert recv(ws) == state(False, 0, T0, "Admin", locked=True)
        for command in ({"type": "play"}, {"type": "pause"}, {"type": "seek", "position_ms": 5_000}):
            b.send_json(command)
            refused = recv(b)
            assert (refused["type"], refused["code"]) == ("error", "controls_locked")
        quiet(a, rig)
        assert rig.req("GET", f"/rooms/{code}").json()["controls_locked"] is True

        a.send_json({"type": "play"})
        for ws in (a, b):
            assert recv(ws) == state(True, 0, T0, "Admin", locked=True)
        a.send_json({"type": "lock", "on": False})
        for ws in (a, b):
            assert recv(ws) == state(True, 0, T0, "Admin")
        b.send_json({"type": "pause"})
        for ws in (a, b):
            assert recv(ws) == state(False, 0, T0, "Guest")


def playing_pair(rig: Rig, a: WebSocketTestSession, b: WebSocketTestSession) -> None:
    """Drain both welcomes and start the room playing at T0."""
    assert [recv(a)["type"] for _ in range(3)] == ["members", "state", "members"]
    assert [recv(b)["type"] for _ in range(2)] == ["members", "state"]
    a.send_json({"type": "play"})
    for ws in (a, b):
        assert recv(ws) == state(True, 0, T0, "Admin")


def test_room_waits_for_a_buffering_member_until_they_recover(rig: Rig) -> None:
    code = rig.room()["code"]
    with rig.enter(code) as a, rig.enter(code, rig.profile("guest", "Guest")) as b:
        playing_pair(rig, a, b)
        rig.clock.ms += 1_000
        b.send_json({"type": "buffering", "on": True})
        for ws in (a, b):
            assert names(recv(ws)) == ["Admin", "Guest*"]
        rig.tick(1_900)  # 1.9 s of buffering: not yet
        quiet(a, rig)
        rig.tick(100)
        for ws in (a, b):
            assert recv(ws) == state(False, 3_000, T0 + 3_000, None)
            assert recv(ws) == {"type": "waiting", "name": "Guest"}
        assert rig.req("GET", f"/rooms/{code}").json()["waiting_for"] == "Guest"

        rig.clock.ms += 4_000
        b.send_json({"type": "buffering", "on": False})
        for ws in (a, b):
            assert names(recv(ws)) == ["Admin", "Guest"]
            assert recv(ws) == state(True, 3_000, T0 + 7_000, None)


def test_room_stops_waiting_after_fifteen_seconds(rig: Rig) -> None:
    code = rig.room()["code"]
    with rig.enter(code) as a, rig.enter(code, rig.profile("guest", "Guest")) as b:
        playing_pair(rig, a, b)
        b.send_json({"type": "buffering", "on": True})
        for ws in (a, b):
            assert names(recv(ws)) == ["Admin", "Guest*"]
        rig.tick(2_000)
        for ws in (a, b):
            assert recv(ws) == state(False, 2_000, T0 + 2_000, None)
            assert recv(ws) == {"type": "waiting", "name": "Guest"}
        rig.tick(14_999)
        quiet(a, rig)
        quiet(b, rig)
        rig.tick(1)
        for ws in (a, b):
            assert recv(ws) == state(True, 2_000, T0 + 17_000, None)
        rig.tick(5_000)  # still buffering, but the room has moved on
        quiet(a, rig)


def test_silent_member_is_dropped_and_must_join_again(rig: Rig) -> None:
    code = rig.room()["code"]
    guest = rig.profile("guest", "Guest")
    token = rig.join(code, guest)["member_token"]
    with rig.enter(code) as a, rig.socket(code, token) as b:
        assert [recv(a)["type"] for _ in range(2)] == ["members", "state"]
        assert [recv(b)["type"] for _ in range(2)] == ["members", "state"]
        rig.clock.ms += 15_000
        quiet(a, rig)  # the host keeps talking, the guest does not
        rig.tick(4_999)
        quiet(a, rig)
        rig.tick(1)
        assert recv(b) == {"type": "closed", "reason": "silent"}
        assert closed_with(b) == 4000
        assert names(recv(a)) == ["Admin"]
        rig.left(a, code, [])
    # the old token no longer names a member of the room
    with rig.socket(code, token) as b:
        assert recv(b) == {"type": "closed", "reason": "not_a_member"}
        assert closed_with(b) == 4404
    with rig.enter(code, guest) as b:
        assert names(recv(b)) == ["Guest"]


def test_lost_connection_can_come_back_but_leaving_is_immediate(rig: Rig) -> None:
    code = rig.room()["code"]
    token = rig.join(code, rig.profile("guest", "Guest"))["member_token"]
    with rig.enter(code) as a:
        assert [recv(a)["type"] for _ in range(2)] == ["members", "state"]
        a.send_json({"type": "play"})
        assert recv(a) == state(True, 0, T0, "Admin")
        with rig.socket(code, token) as b:
            assert [recv(b)["type"] for _ in range(2)] == ["members", "state"]
            b.close(1006)  # the network went away: no goodbye
        rig.clock.ms += 10_000
        quiet(a, rig)  # nobody was told the guest left, because they have not
        with rig.socket(code, token) as b:  # same token, resynchronised
            assert names(recv(b)) == ["Guest", "Admin"]
            assert recv(b) == state(True, 0, T0, None)
            # a second socket for the same member takes over from the first
            with rig.socket(code, token) as b2:
                assert recv(b) == {"type": "closed", "reason": "replaced"}
                assert closed_with(b) == 4000
                assert [recv(b2)["type"] for _ in range(2)] == ["members", "state"]
                b2.send_json({"type": "pause"})
                assert recv(b2) == state(False, 10_000, T0 + 10_000, "Guest")
                assert recv(a) == state(False, 10_000, T0 + 10_000, "Guest")
                b2.close(1000)
                assert names(recv(a)) == ["Admin"]


def test_empty_room_expires_after_thirty_minutes(rig: Rig) -> None:
    code = rig.room()["code"]
    with rig.enter(code) as a:
        assert [recv(a)["type"] for _ in range(2)] == ["members", "state"]
        for _ in range(4):  # an occupied room outlives any timer as long as its members are heard from
            rig.clock.ms += 15_000 * 60
            quiet(a, rig)
            rig.tick()
        assert rig.req("GET", f"/rooms/{code}").status_code == 200
        rig.left(a, code, [])
    rig.tick(R.EMPTY_TTL_MS - 1)
    assert rig.req("GET", f"/rooms/{code}").status_code == 200
    rig.tick(1)
    assert rig.req("GET", f"/rooms/{code}").status_code == 404
    assert len(rig.c.rooms.registry) == 0


def test_deleting_a_room_tells_its_members(rig: Rig) -> None:
    code = rig.room()["code"]
    token = rig.join(code)["member_token"]
    with rig.socket(code, token) as a:
        assert [recv(a)["type"] for _ in range(2)] == ["members", "state"]
        assert rig.req("DELETE", f"/rooms/{code}").status_code == 204
        assert recv(a) == {"type": "closed", "reason": "deleted"}
        assert closed_with(a) == 4000
    with rig.socket(code, token) as a:
        assert recv(a) == {"type": "closed", "reason": "not_found"}
        assert closed_with(a) == 4404


def test_socket_refuses_anything_but_this_rooms_member_token(rig: Rig) -> None:
    code, other = rig.room()["code"], rig.room()["code"]
    joined = rig.join(code)
    stale = RoomTokens(rig.c.crypto, ttl_s=-60)
    principal = rig.call(rig.c.auth.resolve_principal, rig.headers["Authorization"].removeprefix("Bearer "))
    refused = [
        "",
        "garbage",
        rig.headers["Authorization"].removeprefix("Bearer "),  # an access token is not a member token
        rig.join(other)["member_token"],  # another room's
        stale.issue(code, joined["member_id"], principal),  # expired
    ]
    for token in refused:
        with rig.socket(code, token) as ws:
            assert recv(ws) == {"type": "closed", "reason": "unauthorized"}, token
            assert closed_with(ws) == 4401

    # a revoked session cannot keep steering the room with a token minted before
    async def revoke() -> None:
        async with rig.c.db.session() as s:
            profile = await s.get(Profile, rig.admin_id)
            assert profile is not None
            profile.token_version += 1
            await s.commit()

    rig.call(revoke)
    with rig.socket(code, joined["member_token"]) as ws:
        assert recv(ws) == {"type": "closed", "reason": "unauthorized"}
        assert closed_with(ws) == 4401


def test_live_channel_room_has_no_position(rig: Rig) -> None:
    channels = rig.req("GET", "/channels", params={"language": "all"}).json()["channels"]
    bbc = next(c for c in channels if c["name"] == "BBC One HD")
    room = rig.room(bbc["id"])
    assert room["live"] is True and room["state"] == {"playing": True, "position_ms": None, "as_of_ms": T0}
    assert room["title"]["kind"] == "channel"
    joined = rig.join(room["code"])
    assert joined["descriptor"]["live"] is True and joined["descriptor"]["resume_position_ms"] == 0
    with rig.socket(room["code"], joined["member_token"]) as a:
        assert names(recv(a)) == ["Admin"]
        assert recv(a) == state(True, None, T0, None)
        a.send_json({"type": "seek", "position_ms": 1_000})
        assert recv(a)["code"] == "live_no_seek"
        a.send_json({"type": "pause"})
        assert recv(a) == state(False, None, T0, "Admin")


def test_malformed_messages_are_answered_not_fatal(rig: Rig) -> None:
    code = rig.room()["code"]
    with rig.enter(code) as a:
        assert [recv(a)["type"] for _ in range(2)] == ["members", "state"]
        bad: list[Any] = [
            "not json",
            json.dumps([1, 2]),
            json.dumps({"type": "dance"}),
            json.dumps({"type": "seek"}),
            json.dumps({"type": "seek", "position_ms": "soon"}),
            json.dumps({"type": "seek", "position_ms": -1}),
            json.dumps({"type": "seek", "position_ms": True}),
            json.dumps({"type": "lock", "on": "yes"}),
            json.dumps({"type": "buffering"}),
            json.dumps({"type": "play", "pad": "x" * 2_000}),
        ]
        for text in bad:
            a.send_text(text)
            answer = recv(a)
            assert (answer["type"], answer["code"]) == ("error", "bad_message"), text
        a.send_bytes(b"\x00\x01")
        assert recv(a)["code"] == "bad_message"
        a.send_json({"type": "ping"})
        assert recv(a) == {"type": "pong", "t": None, "server_ms": T0}
        assert rig.req("GET", f"/rooms/{code}").json()["state"]["playing"] is False


# ---------------------------------------------------------------------- the tick loop
async def wait_for(predicate: Any, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_ticker_runs_only_while_rooms_exist_and_stops_on_shutdown(container: Container) -> None:
    clock = FakeClock()
    hub = RoomHub(RoomRegistry(), RoomTokens(container.crypto), clock=clock, interval_s=0.01)
    hub.create("lib:s:1", host_profile_id="host", host_name="Host")
    assert not hub.ticking  # never started (tests, tools): nothing runs in the background
    hub.start()
    assert hub.ticking
    clock.ms += R.EMPTY_TTL_MS  # the loop itself notices the expiry ...
    await wait_for(lambda: len(hub.registry) == 0)
    await wait_for(lambda: not hub.ticking)  # ... and with no rooms left it ends

    room = hub.create("lib:s:2", host_profile_id="host", host_name="Host")
    assert hub.ticking  # the next room starts it again
    await hub.shutdown()
    assert not hub.ticking and len(hub.registry) == 0 and room.closed
    hub.create("lib:s:3", host_profile_id="host", host_name="Host")
    assert not hub.ticking  # and nothing restarts it after shutdown
    await hub.shutdown()


async def test_ticker_survives_a_failing_tick(container: Container) -> None:
    hub = RoomHub(RoomRegistry(), RoomTokens(container.crypto), clock=FakeClock(), interval_s=0.01)
    calls = 0

    def boom(now_ms: int) -> list[Any]:
        nonlocal calls
        calls += 1
        raise RuntimeError("tick failed")

    hub.start()
    hub.create("lib:s:1", host_profile_id="host", host_name="Host")
    hub.registry.sweep = boom  # type: ignore[method-assign]
    await wait_for(lambda: calls >= 3)
    assert hub.ticking
    await hub.shutdown()


async def test_socket_that_stops_reading_is_dropped_not_buffered_for() -> None:
    from app.services.room_hub import CLOSE_ROOM, MAX_QUEUED, Connection
    from app.services.rooms import Room

    room = Room("ABCDEF", "lib:s:1", host_profile_id="host", host_name="Host", now_ms=T0)
    member, _ = room.join("host", "Host", T0)
    conn = Connection(room, member)
    for n in range(MAX_QUEUED + 5):
        conn.send({"type": "pong", "t": n, "server_ms": T0})
    assert conn.closing
    assert await conn.next() == {"type": "closed", "reason": "slow"}
    assert await conn.next() == CLOSE_ROOM


async def test_app_lifespan_starts_and_stops_the_room_ticker(settings: AppSettings, container: Container) -> None:
    app = create_app(settings, container=container, start_background=True)
    async with app.router.lifespan_context(app):
        container.rooms.create("lib:s:1", host_profile_id="host", host_name="Host")
        assert container.rooms.ticking
    assert not container.rooms.ticking and len(container.rooms.registry) == 0


def test_member_token_is_kept_out_of_the_logs() -> None:
    from app.infra.logging import redact_text, redact_url

    line = 'WebSocket /api/v1/rooms/ABCDEF/ws?member_token=eyJhbGciOi.payload.sig" [accepted]'
    assert "eyJ" not in redact_text(line) and "ABCDEF" in redact_text(line)
    assert "eyJ" not in (redact_url("ws://hub.test/api/v1/rooms/ABCDEF/ws?member_token=eyJhbGciOi.payload.sig") or "")
