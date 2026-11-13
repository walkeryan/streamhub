"""A member's seat is theirs to refresh: the descriptor by member token, and a join that keeps its seat.

The rig of ``test_api_rooms`` and the stubbed ffmpeg and panel of ``test_room_upstream``.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.infra.models import Progress
from app.services import rooms as R
from tests import test_api_rooms, test_room_upstream
from tests.test_api_rooms import FILM_URL, Rig, closed_with, recv
from tests.test_room_upstream import Ffmpeg, limit, use, wait_for
from tests.test_upstream_session_pin import Panel

rig = test_api_rooms.rig
ffmpeg = test_room_upstream.ffmpeg
panel = test_room_upstream.panel


def descriptor(rig: Rig, code: str, token: str, **params: Any) -> httpx.Response:
    """Asked the way the socket is opened: the member token in the query, and no bearer."""
    query = {"member_token": token, "client": "web", "recovery_generation": rig.c.proxy.recovery_epoch, **params}
    return rig.client.get(f"/api/v1/rooms/{code}/descriptor", params=query)


def watched(rig: Rig, profile_id: str, position_ms: int) -> None:
    """The profile's own history with the film: somewhere the room is not."""

    async def add() -> None:
        async with rig.c.db.session() as s:
            s.add(Progress(profile_id=profile_id, ref_id=rig.film, position_ms=position_ms, duration_ms=6_000_000))
            await s.commit()

    rig.call(add)


# ---------------------------------------------------------------------- the descriptor
def test_descriptor_of_a_shared_stream_is_the_same_url_at_the_current_epoch(
    rig: Rig, ffmpeg: Ffmpeg, panel: Panel
) -> None:
    limit(rig)
    code = rig.room()["code"]
    joined = rig.join(code)
    first = joined["descriptor"]
    r = descriptor(rig, code, joined["member_token"])
    assert r.status_code == 200, r.text
    assert r.json() == first and (first["mode"], first["stream_epoch"]) == ("hls", 1)
    with rig.socket(code, joined["member_token"]) as ws:
        assert [recv(ws)["type"] for _ in range(2)] == ["members", "state"]
        ws.send_json({"type": "seek", "position_ms": 1_802_000})
        told = recv(ws)
        assert (told["stream_epoch"], told["stream_start_ms"], told["position_ms"]) == (2, 1_800_000, 1_802_000)
        again = descriptor(rig, code, joined["member_token"], client="android_tv").json()
        # The playlist URL does not change; what the client reloads it for does.
        assert again["url"] == first["url"]
        assert (again["stream_epoch"], again["start_position_ms"], again["resume_position_ms"]) == (
            2,
            1_800_000,
            1_802_000,
        )
        # Nobody took a seat, opened a stream or used a connection to ask.
        assert rig.members(code) == ["Admin"] and len(ffmpeg.runs) == 2 and use(rig) == 1


def test_descriptor_of_a_library_title_is_fresh_and_at_the_room_position(rig: Rig) -> None:
    code = rig.room()["code"]  # no connection limit: every member plays their own
    watched(rig, rig.admin_id, 300_000)
    joined = rig.join(code)
    assert joined["descriptor"]["resume_position_ms"] == 0  # the room's, not the 5 minutes of their own
    with rig.socket(code, joined["member_token"]) as ws:
        assert [recv(ws)["type"] for _ in range(2)] == ["members", "state"]
        ws.send_json({"type": "seek", "position_ms": 1_200_000})
        ws.send_json({"type": "play"})
        seek, play = recv(ws), recv(ws)
        # No shared stream: the state says everything there is to know.
        assert "stream_epoch" not in seek and "stream_start_ms" not in play
        rig.clock.ms += 15_000
        for client in ("web", "android_tv"):
            d = descriptor(rig, code, joined["member_token"], client=client).json()
            assert (d["url"], d["mode"], d["stream_epoch"]) == (FILM_URL, "direct", None)
            assert d["resume_position_ms"] == 1_215_000
        assert rig.members(code) == ["Admin"]


def test_descriptor_is_authorized_by_the_member_token_alone(rig: Rig) -> None:
    code, other = rig.room()["code"], rig.room()["code"]
    guest = rig.profile("guest", "Guest")
    joined = rig.join(code, guest)
    token = joined["member_token"]
    assert descriptor(rig, code, token).status_code == 200
    assert descriptor(rig, code.lower(), token).status_code == 200
    # The playback rules of a join: an outdated web client is told to reload.
    r = rig.client.get(f"/api/v1/rooms/{code}/descriptor", params={"member_token": token})
    assert r.status_code == 403 and r.json()["code"] == "playback_reload_required"
    assert rig.client.get(f"/api/v1/rooms/{code}/descriptor").status_code == 422
    for bad in ("nope", rig.headers["Authorization"].split()[1], rig.join(other, guest)["member_token"]):
        r = descriptor(rig, code, bad)
        assert r.status_code == 401 and r.json()["code"] == "room_unauthorized", r.text
    # A seat the room took back is not a seat.
    rig.tick(R.SILENT_MS)
    r = descriptor(rig, code, token)
    assert r.status_code == 404 and r.json()["code"] == "not_a_member", r.text
    # The session behind the token must still stand.
    fresh = rig.join(code, guest)["member_token"]
    sid = rig.c.auth.principal_from_access(guest["Authorization"].split()[1]).session_id
    rig.call(rig.c.auth.revoke_session, sid)
    r = descriptor(rig, code, fresh)
    assert r.status_code == 401 and r.json()["code"] == "session_revoked"
    mine = rig.join(code)["member_token"]
    assert rig.req("DELETE", f"/rooms/{code}").status_code == 204
    r = descriptor(rig, code, mine)
    assert r.status_code == 404 and r.json()["code"] == "room_not_found"


def test_descriptor_follows_what_the_member_may_do_now(rig: Rig) -> None:
    from tests.test_api_rooms import member_of

    code = rig.room()["code"]
    created = rig.req("POST", "/users", json={"name": "Friend", "role_id": "friend", "pin": "1234"}).json()
    login = rig.client.post("/api/v1/auth/login", json={"profile_id": created["profile_id"], "pin": "1234"})
    friend = {"Authorization": f"Bearer {login.json()['access_token']}"}
    token = rig.join(code, friend)["member_token"]
    assert descriptor(rig, code, token).status_code == 200
    # Away from home without remote.access: the same answer the API gives their session.
    assert rig.req("PATCH", f"/users/{created['id']}", json={"denials": ["remote.access"]}).status_code == 200
    assert descriptor(rig, code, token).status_code == 200  # the rig's peer is on the home network
    rig.c.app_settings._cache["home_networks"] = "198.51.100.0/24"
    r = descriptor(rig, code, token)
    assert r.status_code == 403 and r.json()["code"] == "remote_access_denied"
    rig.c.app_settings._cache.pop("home_networks")
    # Losing the permission to be in rooms, or to watch this kind of title, reaches the next call.
    assert rig.req("PATCH", f"/users/{created['id']}", json={"denials": ["rooms.join"]}).status_code == 200
    r = descriptor(rig, code, token)
    assert r.status_code == 403 and r.json()["missing"] == ["rooms.join"]
    assert rig.req("PATCH", f"/users/{created['id']}", json={"denials": ["watch.vod"]}).status_code == 200
    r = descriptor(rig, code, token)
    assert r.status_code == 403 and r.json()["missing"] == ["watch.vod"]
    assert member_of  # the helper this suite's users are modelled on


def test_join_and_socket_keep_a_user_without_remote_access_at_home(rig: Rig) -> None:
    code = rig.room()["code"]
    created = rig.req("POST", "/users", json={"name": "Friend", "role_id": "friend", "pin": "1234"}).json()
    assert rig.req("PATCH", f"/users/{created['id']}", json={"denials": ["remote.access"]}).status_code == 200
    login = rig.client.post("/api/v1/auth/login", json={"profile_id": created["profile_id"], "pin": "1234"})
    friend = {"Authorization": f"Bearer {login.json()['access_token']}"}
    seat = rig.join(code, friend)  # at home, as the rig's peer is

    rig.c.app_settings._cache["home_networks"] = "198.51.100.0/24"
    # Away from home the join is refused as every call of theirs is, and takes no seat.
    r = rig.join_raw(code, friend)
    assert r.status_code == 403 and r.json()["code"] == "remote_access_denied", r.text
    r = rig.join_raw(code, friend, member_token=seat["member_token"])
    assert r.status_code == 403 and r.json()["code"] == "remote_access_denied", r.text
    # The socket carries no bearer: the seat's own user is held to the same rule, by the same name.
    with rig.socket(code, seat["member_token"]) as ws:
        assert recv(ws) == {"type": "closed", "reason": "remote_access_denied"}
        assert closed_with(ws) == 4401
    assert rig.members(code) == ["Friend"]  # refused, not removed: the seat is theirs when they are home
    # Someone who holds remote.access is at home anywhere.
    mine = rig.join(code)
    with rig.socket(code, mine["member_token"]) as ws:
        assert recv(ws)["type"] == "members"

    rig.c.app_settings._cache.pop("home_networks")
    with rig.socket(code, seat["member_token"]) as ws:
        assert [recv(ws)["type"] for _ in range(2)] == ["members", "state"]


# ---------------------------------------------------------------------- joining again
def test_join_with_a_member_token_keeps_the_seat(rig: Rig, ffmpeg: Ffmpeg, panel: Panel) -> None:
    limit(rig)
    code = rig.room()["code"]
    guest = rig.profile("guest", "Guest")
    first = rig.join(code)
    theirs = rig.join(code, guest)
    again = rig.join(code, member_token=first["member_token"])
    assert again["member_id"] == first["member_id"] and again["descriptor"] == first["descriptor"]
    assert again["ws_path"] == first["ws_path"] and again["expires_in_seconds"] == 7200
    assert rig.members(code) == ["Admin", "Guest"] and len(ffmpeg.runs) == 1
    # The token it comes back with is this seat's too.
    with rig.socket(code, again["member_token"]) as ws:
        assert [m["id"] for m in recv(ws)["members"]] == [first["member_id"], theirs["member_id"]]
        # Without the field a join is a new seat, as it always was.
        assert rig.join(code)["member_id"] != first["member_id"]
        assert rig.members(code) == ["Admin", "Guest", "Admin"]


def test_a_seat_can_be_kept_in_a_full_room_but_not_taken(rig: Rig) -> None:
    code = rig.room()["code"]
    seats = [rig.join(code) for _ in range(R.MAX_MEMBERS)]
    guest = rig.profile("guest", "Guest")
    r = rig.join_raw(code, member_token=seats[0]["member_token"])
    assert r.status_code == 200 and r.json()["member_id"] == seats[0]["member_id"]
    # Somebody else's token is not theirs to present; neither is one that no longer names a seat.
    for token in (seats[0]["member_token"], "nope"):
        r = rig.join_raw(code, guest, member_token=token)
        assert r.status_code == 409 and r.json()["code"] == "room_full"
    assert len(rig.req("GET", f"/rooms/{code}").json()["members"]) == R.MAX_MEMBERS


def test_a_lapsed_seat_is_simply_joined_again(rig: Rig) -> None:
    code = rig.room()["code"]
    first = rig.join(code)
    rig.tick(R.SILENT_MS)  # dropped for silence
    assert rig.members(code) == []
    again = rig.join(code, member_token=first["member_token"])
    assert again["member_id"] != first["member_id"] and rig.members(code) == ["Admin"]


# ---------------------------------------------------------------------- where a member starts
def test_every_kind_of_room_starts_a_member_at_the_room_position(rig: Rig, ffmpeg: Ffmpeg, panel: Panel) -> None:
    guest = rig.profile("guest", "Guest")
    watched(rig, "guest", 300_000)

    def room_at(position_ms: int) -> str:
        room = rig.c.rooms.registry.get(rig.room()["code"])
        assert room is not None
        room.state.position_ms = position_ms
        return room.code

    # A library title each member plays for themselves, on either client.
    code = room_at(2_402_000)
    for client in ("web", "android_tv"):
        d = rig.join(code, guest, client=client)["descriptor"]
        # Their own stream, through this server: a guest does not manage sources, so is not handed the source's URL.
        assert (d["mode"], d["resume_position_ms"], d["stream_epoch"]) == ("proxy", 2_402_000, None)
        mine = rig.join(code, None, client=client)["descriptor"]
        assert (mine["mode"], mine["resume_position_ms"], mine["stream_epoch"]) == ("direct", 2_402_000, None)
    # The same title as one shared stream.
    limit(rig)
    code = room_at(2_402_000)
    d = rig.join(code, guest, client="android_tv")["descriptor"]
    assert (d["mode"], d["resume_position_ms"], d["start_position_ms"]) == ("hls", 2_402_000, 2_400_000)
    assert d["stream_epoch"] == 1 and ffmpeg.arg(0, "-ss") == "2400"
    # A room that has not started is at zero, whatever the member watched before.
    assert rig.req("DELETE", f"/rooms/{code}").status_code == 204
    wait_for(lambda: use(rig) == 0)
    d = rig.join(room_at(0), guest)["descriptor"]
    assert (d["resume_position_ms"], d["start_position_ms"]) == (0, 0)
