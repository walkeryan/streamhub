"""Sessions on streams: every grant re-validates its viewer, and a user's streams are counted.

ffmpeg is stubbed (a playlist and one segment appear); upstream HTTP is respx-mocked.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.proxy import stream_proxy
from app.services.errors import Conflict, Unauthorized
from app.services.playback import StreamGrant
from app.services.streams import RECENT_S, VALID_TTL_S, StreamRegistry, panes
from tests.conftest import Api
from tests.people import Member, member, sign_in
from tests.test_epg_integrity import add_source
from tests.test_source_busy import Process

FILM = "http://vod.example.test/film.mkv"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(api: Api) -> Clock:
    """One fake clock for the grant guard's cache and the registry's idea of recent."""
    clock = Clock()
    api.c.guard.clock = clock
    api.c.streams.clock = clock
    return clock


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[Process]:
    started: list[Process] = []

    async def spawn(*args: Any, **kwargs: Any) -> Process:
        playlist = Path(args[-1])
        playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nseg_00000.ts\n")
        (playlist.parent / "seg_00000.ts").write_bytes(b"\x47" * 188)
        started.append(Process())
        return started[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(stream_proxy, "HLS_ADMISSION_WAIT_S", 0.0)
    return started


async def channels(api: Api) -> list[dict[str, Any]]:
    """Raw MPEG-TS channels: remuxed to HLS for every client, so each resolve yields a grant."""
    await add_source(api)
    listed = (await api.get("/channels", params={"language": "all"})).json()["channels"]
    return [c for c in listed if c["name"] in ("MBC 1", "BBC One HD", "TF1 FHD")] or listed


async def resolve(who: Api | Member, ref: str, **body: Any) -> httpx.Response:
    return await who.post("/playback/resolve", json={"id": ref, "client": "android_tv", **body})


def token_of(descriptor: dict[str, Any]) -> str:
    return str(descriptor["url"]).split("/stream/", 1)[1].split("/", 1)[0]


async def hls_channel(api: Api, who: Api | Member) -> tuple[dict[str, Any], str]:
    """A channel resolved to HLS for ``who`` and its stream token."""
    for channel in await channels(api):
        r = await resolve(who, channel["id"])
        assert r.status_code == 200, r.text
        if r.json()["mode"] == "hls":
            return r.json(), token_of(r.json())
    raise AssertionError("no channel resolves to HLS")


async def segment(api: Api, token: str) -> httpx.Response:
    playlist = await api.client.get(f"/api/v1/stream/{token}/index.m3u8")
    if playlist.status_code != 200:
        return playlist
    line = next(ln for ln in playlist.text.splitlines() if ln and not ln.startswith("#"))
    return await api.client.get(line)


# ---------------------------------------------------------------- the grant names its viewer
async def test_grant_carries_the_user_and_the_version_it_was_issued_under(api: Api, ffmpeg: list[Process]) -> None:
    viewer = await member(api, "family", "Viewer")
    descriptor, token = await hls_channel(api, viewer)
    grant = api.c.tokens.verify(token)
    assert grant is not None
    assert (grant.user_id, grant.profile_id, grant.client) == (viewer.id, viewer.profile_id, "android_tv")
    signed_in = api._identity(viewer.tokens["access_token"])
    assert {k: getattr(grant, k) for k in signed_in} == signed_in and isinstance(grant.user_version, int)
    assert (await segment(api, token)).status_code == 200
    # The proxy session is tagged with the user too.
    [tagged] = api.c.proxy.user_viewers(viewer.id)
    assert (tagged.profile_id, tagged.title, tagged.client) == (viewer.profile_id, descriptor["title"], "android_tv")
    await api.c.proxy.shutdown()


async def test_grant_without_a_session_is_refused(api: Api) -> None:
    source = await add_source(api)
    bare = StreamGrant(api.profile_id, source["id"], f"{FILM}", {}, "proxy", False, None, False)
    r = await api.client.get(f"/api/v1/stream/{api.c.tokens.issue(bare)}")
    assert r.status_code == 401 and r.json()["code"] == "session_revoked"
    # A grant naming another user than the session's is no better.
    forged = StreamGrant(
        api.profile_id, source["id"], FILM, {}, "proxy", False, None, False, **{**api.identity(), "user_id": "nobody"}
    )
    assert (await api.client.get(f"/api/v1/stream/{api.c.tokens.issue(forged)}")).status_code == 401


# ---------------------------------------------------------------- revocation, suspension, staleness
async def test_revoked_session_gets_401_on_the_next_segment(api: Api, ffmpeg: list[Process], clock: Clock) -> None:
    viewer = await member(api, "family", "Viewer")
    _, token = await hls_channel(api, viewer)
    assert (await segment(api, token)).status_code == 200
    [session] = (await viewer.get("/auth/sessions")).json()
    assert (await api.delete(f"/auth/sessions/{session['sid']}")).status_code == 204
    # A positive check is reused for a few seconds, so segments stay cheap…
    assert (await segment(api, token)).status_code == 200
    # …and the next one after that is refused, playlist and segment alike.
    clock.now += VALID_TTL_S
    playlist = await api.client.get(f"/api/v1/stream/{token}/index.m3u8")
    assert playlist.status_code == 401 and playlist.json()["code"] == "session_revoked"
    sid = api.c.proxy.session_id(next(iter(api.c.proxy._hls)))
    refused = await api.client.get(f"/api/v1/stream/{token}/hls/{sid}/seg_00000.ts")
    assert refused.status_code == 401 and refused.json()["code"] == "session_revoked"
    # The owner's own stream of the same channel is untouched.
    await api.c.proxy.shutdown()


async def test_role_change_and_suspension_refuse_the_grant(api: Api, ffmpeg: list[Process], clock: Clock) -> None:
    viewer = await member(api, "family", "Viewer")
    _, token = await hls_channel(api, viewer)
    assert (await segment(api, token)).status_code == 200
    # The user's version moved on: the grant is stale until playback is resolved again.
    assert (await api.patch(f"/users/{viewer.id}", json={"role_id": "friend"})).status_code == 200
    clock.now += VALID_TTL_S
    stale = await segment(api, token)
    assert stale.status_code == 401 and stale.json()["code"] == "session_stale"
    assert (await viewer.refresh()).status_code == 200
    _, fresh = await hls_channel(api, viewer)
    assert (await segment(api, fresh)).status_code == 200
    assert (await api.post(f"/users/{viewer.id}/suspend")).status_code == 200
    clock.now += VALID_TTL_S
    suspended = await segment(api, fresh)
    assert suspended.status_code == 401 and suspended.json()["code"] in ("user_suspended", "session_revoked")
    await api.c.proxy.shutdown()


async def test_a_stale_grant_is_told_apart_from_a_revoked_session(
    api: Api, ffmpeg: list[Process], clock: Clock
) -> None:
    """What a media URL answers says whether to resolve again or to sign in again, without asking /auth/me."""
    viewer = await member(api, "family", "Viewer")
    _, token = await hls_channel(api, viewer)
    playlist = f"/api/v1/stream/{token}/index.m3u8"
    first = next(ln for ln in (await api.client.get(playlist)).text.splitlines() if ln and not ln.startswith("#"))
    urls = (playlist, first, f"/api/v1/stream/{token}")

    async def answers() -> set[tuple[int, str]]:
        clock.now += VALID_TTL_S
        return {(r.status_code, r.json()["code"]) for r in [await api.client.get(url) for url in urls]}

    # The user's access changed (their version moved on): the grant is out of date, the session is not.
    assert (await api.patch(f"/users/{viewer.id}", json={"grants": ["rooms.create"]})).status_code == 200
    assert await answers() == {(401, "session_stale")}
    assert (await viewer.get("/settings")).json()["code"] == "session_stale"  # as the API says it
    assert (await viewer.refresh()).status_code == 200 and (await viewer.get("/auth/me")).status_code == 200
    assert await answers() == {(401, "session_stale")}  # the old URL stays stale; a fresh one plays
    _, fresh = await hls_channel(api, viewer)
    assert (await segment(api, fresh)).status_code == 200

    # A grant past its lifetime is not about the session at all.
    lifetime, api.c.tokens.ttl_s = api.c.tokens.ttl_s, -1
    expired = await api.client.get(f"/api/v1/stream/{fresh}/index.m3u8")
    api.c.tokens.ttl_s = lifetime
    assert (expired.status_code, expired.json()["code"]) == (401, "unauthorized")
    assert (await viewer.get("/auth/me")).status_code == 200

    # The session itself signed out: only now is it session_revoked, on the stale URL and the fresh one alike.
    [session] = (await viewer.get("/auth/sessions")).json()
    assert (await api.delete(f"/auth/sessions/{session['sid']}")).status_code == 204
    assert await answers() == {(401, "session_revoked")}
    gone = await segment(api, fresh)
    assert (gone.status_code, gone.json()["code"]) == (401, "session_revoked")
    assert (await viewer.get("/auth/me")).json()["code"] == "session_revoked"
    await api.c.proxy.shutdown()


async def test_a_grant_of_the_user_a_shared_device_was_taken_from_is_revoked_like_their_tokens(
    api: Api, ffmpeg: list[Process], clock: Clock
) -> None:
    from tests.test_shared_devices import bearer, pair, switch, user

    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True)
    me = (await api.get("/auth/me")).json()["user"]["id"]
    _, token = await hls_channel(api, Member(api, me, api.profile_id, tv))
    assert (await segment(api, token)).status_code == 200
    handed = await switch(api, tv, kid["id"])
    assert handed.status_code == 200, handed.text
    # The device's session lives on under someone else. What was issued to the previous user
    # says what their access token says, on the media URL as on the API.
    clock.now += VALID_TTL_S
    refused = await segment(api, token)
    assert (refused.status_code, refused.json()["code"]) == (401, "session_revoked")
    old = await api.client.get("/api/v1/auth/me", headers=bearer(tv))
    assert (old.status_code, old.json()["code"]) == (401, "session_revoked")
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(handed.json()))).status_code == 200
    await api.c.proxy.shutdown()


class Endless(httpx.AsyncByteStream):
    """An upstream file that keeps sending until it is closed."""

    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> Any:
        while not self.closed:
            yield b"x" * 1024
            await asyncio.sleep(0)

    async def aclose(self) -> None:
        self.closed = True


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_suspension_stops_a_running_proxy_stream(api: Api, clock: Clock, respx_mock: respx.MockRouter) -> None:
    respx_mock.route(host="hub.test").pass_through()
    viewer = await member(api, "family", "Viewer")
    source = await add_source(api)
    body = Endless()
    respx_mock.get(FILM).mock(return_value=httpx.Response(200, stream=body, headers={"content-type": "video/mp4"}))
    identity = api._identity(viewer.tokens["access_token"])
    grant = StreamGrant(viewer.profile_id, source["id"], FILM, {}, "proxy", False, None, False, **identity)
    grant.recovery_generation = api.c.proxy.generation
    _, _, upstream = await api.c.proxy.passthrough(FILM, {}, source["id"], generation=grant.recovery_generation)
    stream = api.c.guard.guard_stream(grant, upstream)
    for _ in range(3):
        assert await anext(stream)
    assert len(api.c.proxy._http) == 1

    assert (await api.post(f"/users/{viewer.id}/suspend")).status_code == 200
    assert await anext(stream)  # inside the reuse window nothing is asked
    clock.now += VALID_TTL_S
    with pytest.raises(Unauthorized):
        await anext(stream)
    await stream.aclose()
    # The upstream connection went with it.
    assert body.closed and not api.c.proxy._http and api.c.active.count(source["id"]) == 0


# ---------------------------------------------------------------- the per-user stream limit
async def limited(api: Api) -> tuple[Member, Member, list[dict[str, Any]]]:
    """A user allowed one stream, signed in on two devices, and the channels there are."""
    listed = await channels(api)
    first = await member(api, "friend", "Guest")
    user = {"id": first.id, "profile_id": first.profile_id}
    return first, await sign_in(api, user), listed


async def test_stream_limit_names_what_is_playing_and_is_released_on_stop(api: Api, clock: Clock) -> None:
    tv, laptop, listed = await limited(api)
    assert (await api.get(f"/users/{tv.id}")).json()["limits"]["max_streams"] == 1
    playing = await resolve(tv, listed[0]["id"])
    assert playing.status_code == 200, playing.text
    refused = await resolve(laptop, listed[1]["id"])
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert (body["code"], body["limit"]) == ("user_stream_limit", 1) and "1 stream" in body["message"]
    [active] = body["active"]
    assert (active["profile_name"], active["title"], active["kind"]) == ("Guest", listed[0]["name"], "channel")
    assert set(active) == {"profile_name", "title", "kind", "since"}
    assert datetime.fromisoformat(active["since"]).tzinfo is not None
    # Even the same channel on a second device is a second stream.
    assert (await resolve(laptop, listed[0]["id"])).status_code == 409
    # Someone without a limit is never counted against it.
    assert (await resolve(api, listed[1]["id"])).status_code == 200

    stopped = {"id": listed[0]["id"], "position_ms": 0, "duration_ms": None, "state": "stopped"}
    assert (await tv.post("/playback/progress", json=stopped)).status_code == 204
    assert (await resolve(laptop, listed[1]["id"])).status_code == 200
    assert (await resolve(tv, listed[0]["id"])).status_code == 409


async def test_seek_restart_audio_change_and_retry_do_not_count(api: Api, clock: Clock) -> None:
    tv, laptop, listed = await limited(api)
    for _ in range(3):  # the same title on the same device is one stream however often it is resolved
        assert (await resolve(tv, listed[0]["id"])).status_code == 200
    web = {"client": "web", "recovery_generation": api.c.proxy.recovery_epoch}
    assert (await resolve(tv, listed[0]["id"], **web, start_position_ms=60_000)).status_code == 200
    assert len(api.c.streams.active(tv.id)) == 1
    assert (await resolve(laptop, listed[0]["id"])).status_code == 409


async def test_a_new_title_on_the_same_device_takes_the_old_ones_place(
    api: Api, ffmpeg: list[Process], clock: Clock
) -> None:
    tv, laptop, _ = await limited(api)
    first, first_token = await hls_channel(api, tv)
    assert (await segment(api, first_token)).status_code == 200
    other = next(c for c in await channels_listed(api) if c["id"] != first["id"])
    # Players ask for the next channel before they report the last one stopped: never a 409 for that.
    zapped = await resolve(tv, other["id"])
    assert zapped.status_code == 200, zapped.text
    assert [r.ref for r in api.c.streams.active(tv.id)] == [other["id"]]
    # The stream that was replaced is no longer served, so one device cannot run two.
    clock.now += VALID_TTL_S
    replaced = await segment(api, first_token)
    assert replaced.status_code == 409 and replaced.json()["code"] == "user_stream_limit"
    assert [a["title"] for a in replaced.json()["active"]] == [other["name"]]
    assert (await resolve(laptop, other["id"])).status_code == 409
    await api.c.proxy.shutdown()


async def channels_listed(api: Api) -> list[dict[str, Any]]:
    listed: list[dict[str, Any]] = (await api.get("/channels", params={"language": "all"})).json()["channels"]
    return listed


async def test_only_recent_activity_counts(api: Api, ffmpeg: list[Process], clock: Clock) -> None:
    tv, laptop, _ = await limited(api)
    descriptor, token = await hls_channel(api, tv)
    other = next(c for c in await channels_listed(api) if c["id"] != descriptor["id"])
    # Young, then kept alive by the segments it is served.
    clock.now += RECENT_S - 5
    assert (await segment(api, token)).status_code == 200
    clock.now += RECENT_S - 5
    assert (await resolve(laptop, other["id"])).status_code == 409
    # A progress report is a sign of life too (all there is for a stream played without the proxy).
    report = {"id": descriptor["id"], "position_ms": 1000, "duration_ms": None, "state": "playing"}
    assert (await tv.post("/playback/progress", json=report)).status_code == 204
    clock.now += RECENT_S - 5
    assert (await resolve(laptop, other["id"])).status_code == 409
    # Thirty seconds of silence and the stream has given its place up…
    clock.now += RECENT_S
    assert (await resolve(laptop, other["id"])).status_code == 200
    # …so it cannot come back while the other device is playing.
    late = await segment(api, token)
    assert late.status_code == 409 and late.json()["code"] == "user_stream_limit"
    await api.c.proxy.shutdown()


async def test_now_playing_reflects_reality(api: Api, ffmpeg: list[Process], clock: Clock) -> None:
    tv, _, _ = await limited(api)

    async def now_playing() -> list[dict[str, Any]]:
        listed = (await api.get("/users")).json()
        detail = (await api.get(f"/users/{tv.id}")).json()
        mine = next(u for u in listed if u["id"] == tv.id)["now_playing"]
        assert mine == detail["now_playing"]
        return list(mine)

    assert await now_playing() == []
    descriptor, token = await hls_channel(api, tv)
    [playing] = await now_playing()
    assert (playing["id"], playing["title"], playing["kind"]) == (descriptor["id"], descriptor["title"], "channel")
    assert playing["client"] == "android_tv" and datetime.fromisoformat(playing["since"]).tzinfo is not None
    assert await api.c.sessions.now_playing(tv.id) == api.c.streams.now_playing(tv.id)
    # Still listed while segments flow; gone thirty seconds after they stop.
    clock.now += RECENT_S - 1
    assert (await segment(api, token)).status_code == 200
    clock.now += RECENT_S - 1
    assert len(await now_playing()) == 1
    clock.now += 2
    assert await now_playing() == []
    # Playing again, then stopped by the player.
    assert (await segment(api, token)).status_code == 200
    assert len(await now_playing()) == 1
    stopped = {"id": descriptor["id"], "position_ms": 0, "duration_ms": None, "state": "stopped"}
    assert (await tv.post("/playback/progress", json=stopped)).status_code == 204
    assert await now_playing() == []
    await api.c.proxy.shutdown()


# ---------------------------------------------------------------- the registry alone
class G:
    """The fields of a grant the registry reads."""

    def __init__(self, session_id: str, ref: str) -> None:
        self.profile_id, self.session_id, self.content_ref = "p", session_id, ref
        self.title, self.kind, self.client = ref.upper(), "movie", "web"


def admit(streams: StreamRegistry, session: str, ref: str, limit: int | None) -> None:
    streams.admit("u", "p", session, ref, title=ref.upper(), kind="movie", client="web", limit=limit)


async def test_registry_counts_other_devices_and_replaces_on_the_same_one() -> None:
    clock = Clock()
    streams = StreamRegistry(clock=clock)
    admit(streams, "tv", "a", 2)
    admit(streams, "laptop", "b", 2)
    await streams.require_slot("u", "tv", 2)
    with pytest.raises(Conflict) as refused:
        await streams.require_slot("u", "phone", 2)
    assert refused.value.code == "user_stream_limit" and refused.value.extra is not None
    assert [a["title"] for a in refused.value.extra["active"]] == ["A", "B"] and refused.value.extra["limit"] == 2
    # A third stream on the tv replaces the tv's own; the laptop's is untouched.
    clock.now += 1
    admit(streams, "tv", "c", 2)
    assert sorted(r.ref for r in streams.active("u")) == ["b", "c"]
    assert streams.touch("u", G("tv", "a"), 2) is not None  # replaced: not served while the limit is full
    assert streams.touch("u", G("laptop", "b"), 2) is None
    streams.release("u", "laptop", "b")
    assert streams.touch("u", G("tv", "a"), 2) is None  # room again: it may come back
    assert sorted(r.ref for r in streams.active("u")) == ["a", "c"]
    assert streams.active("someone-else") == []


async def test_registry_lets_two_panes_play_side_by_side() -> None:
    streams = StreamRegistry(clock=Clock())
    with panes(2):
        with pytest.raises(Conflict):
            await streams.require_slot("u", "tv", 1)
        await streams.require_slot("u", "tv", 2)
        admit(streams, "tv", "left", 2)
        admit(streams, "tv", "right", 2)
    assert sorted(r.ref for r in streams.active("u")) == ["left", "right"]
    with pytest.raises(Conflict):
        await streams.require_slot("u", "laptop", 2)


async def test_registry_external_streams_count() -> None:
    streams = StreamRegistry(clock=Clock())
    admit(streams, "tv", "a", None)
    [mine] = streams.active("u")
    streams.release("u", "tv", "a")
    streams.external.append(lambda user_id: [mine] if user_id == "u" else [])
    with pytest.raises(Conflict):
        await streams.require_slot("u", "laptop", 1)
    await streams.require_slot("other", "laptop", 1)
