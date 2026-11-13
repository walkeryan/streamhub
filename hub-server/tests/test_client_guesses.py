"""What the TV client assumes about the server, pinned: each of these is relied on in the app."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from sqlalchemy import select

from app.infra.models import AuditLog, Recording
from app.infra.settings import AppSettings
from app.services.auth import ALG
from app.services.classification import classification_record, summarize
from tests import test_api_rooms
from tests.conftest import Api
from tests.people import member, owner_id, set_user
from tests.test_api_rooms import Rig
from tests.test_shared_devices import bearer, claims_of, pair, switch, user

rig = test_api_rooms.rig


@pytest.fixture
def settings(settings: AppSettings, tmp_path: Path) -> AppSettings:
    root = tmp_path / "recordings"
    root.mkdir()
    return settings.model_copy(update={"recordings_dir": str(root)})


# ---------------------------------------------------------------- POST /auth/switch
async def test_switch_accepts_a_null_pin(api: Api) -> None:
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True)
    r = await api.client.post("/api/v1/auth/switch", json={"user_id": kid["id"], "pin": None}, headers=bearer(tv))
    assert r.status_code == 200, r.text
    assert r.json()["user"]["id"] == kid["id"]


async def test_switching_to_oneself_with_a_pin_is_a_pin_check(api: Api) -> None:
    me = await owner_id(api)
    assert (await api.put(f"/users/{me}/pin", json={"pin": "1357"})).status_code == 204
    tv = await pair(api, shared_device=True)
    right = await switch(api, tv, me, "1357")
    assert right.status_code == 200, right.text
    assert right.json()["user"]["id"] == me and right.json()["user"]["current"] is True
    assert claims_of(right.json()["access_token"])["sid"] == claims_of(tv["access_token"])["sid"]
    # A wrong PIN is told apart, or the check would be no check; it changes nothing on the device.
    wrong = await switch(api, tv, me, "0000")
    assert wrong.status_code == 401 and wrong.json()["code"] == "pin_invalid"
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(tv))).status_code == 200
    # Without a PIN it stays the no-op it was: a fresh pair, nothing asked.
    assert (await switch(api, tv, me)).status_code == 200
    # Someone with no PIN has nothing to check a PIN against.
    kid = await user(api, "Kid", "kid")
    theirs = (await switch(api, right.json(), kid["id"])).json()
    assert (await switch(api, theirs, kid["id"], "4321")).status_code == 200
    async with api.c.db.session() as s:
        logged = list((await s.execute(select(AuditLog).where(AuditLog.action == "session.switch"))).scalars())
    assert [a.target_user_id for a in logged] == [kid["id"]]  # checking a PIN is not a hand-over


async def test_wrong_pins_against_oneself_share_the_switch_budget(api: Api) -> None:
    me = await owner_id(api)
    assert (await api.put(f"/users/{me}/pin", json={"pin": "1357"})).status_code == 204
    tv = await pair(api, shared_device=True)
    for _ in range(5):
        assert (await switch(api, tv, me, "0000")).json()["code"] == "pin_invalid"
    locked = await switch(api, tv, me, "1357")
    assert locked.status_code == 429 and locked.json()["code"] == "rate_limited"
    # Retry-After is whole seconds, never an HTTP date.
    assert locked.headers["retry-after"].isdigit() and 0 < int(locked.headers["retry-after"]) <= 15 * 60 + 1
    assert (await switch(api, tv, me)).status_code == 200  # asking nothing is still answered


async def test_a_dead_session_on_switch_is_not_a_pin_answer(api: Api) -> None:
    parent = await user(api, "Parent", "family", pin="2468")
    tv = await pair(api, shared_device=True)
    claims = claims_of(tv["access_token"])
    # The access token ran out: the client refreshes and tries again.
    expired = jwt.encode({**claims, "exp": int(time.time()) - 5}, api.c.auth._key, algorithm=ALG)
    r = await switch(api, {"access_token": expired}, parent["id"], "2468")
    assert r.status_code == 401 and r.json()["code"] == "token_expired"
    r = await switch(api, {"access_token": expired}, parent["id"], "0000")
    assert r.status_code == 401 and r.json()["code"] == "token_expired"  # the PIN was never looked at
    # The user's access changed since the token was minted: the same, with its own code.
    me = await owner_id(api)
    await set_user(api, me, version=claims["uv"] + 1)
    r = await switch(api, tv, parent["id"], "2468")
    assert r.status_code == 401 and r.json()["code"] == "session_stale"
    assert not {r.json()["code"]} & {"pin_required", "pin_invalid"}
    assert api.c.sessions._pin_failures == {}


async def test_adult_unlock_retry_after_is_integer_seconds(api: Api) -> None:
    me = await owner_id(api)
    assert (await api.put(f"/users/{me}/pin", json={"pin": "1357"})).status_code == 204
    for _ in range(5):
        assert (await api.post("/auth/adult-unlock", json={"pin": "0000"})).status_code == 403
    locked = await api.post("/auth/adult-unlock", json={"pin": "1357"})
    assert locked.status_code == 429 and locked.headers["retry-after"].isdigit()


# ---------------------------------------------------------------- rooms
def test_room_join_accepts_the_tv_client(rig: Rig) -> None:
    code = rig.room()["code"]
    r = rig.req("POST", f"/rooms/{code}/join", json={"client": "android_tv"})
    assert r.status_code == 200, r.text
    assert r.json()["descriptor"]["mode"] in ("direct", "proxy", "hls")
    assert rig.req("POST", f"/rooms/{code}/join", json={"client": "tvos"}).status_code == 422


# ---------------------------------------------------------------- recordings
async def recorded(api: Api, rid: str, profile_id: str, code: str | None) -> None:
    (Path(api.c.settings.recordings_dir) / f"{rid}.mkv").write_bytes(b"\x1a\x45\xdf\xa3" * 64)
    start = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)
    rating = summarize("xmltv", "programme", [classification_record("US", "US-TVPG", code)]) if code else {}
    async with api.c.db.session() as s:
        s.add(
            Recording(
                id=rid,
                profile_id=profile_id,
                channel_id="ch:gone:1",
                programme_key=rid,
                title="Evening News",
                starts_at=start,
                ends_at=start + timedelta(hours=1),
                state="done",
                path=f"{rid}.mkv",
                age_evidence=rating,
            )
        )
        await s.commit()


async def test_rec_ids_resolve_for_whoever_may_view_recordings(api: Api) -> None:
    async def play(who: Any, rid: str) -> Any:
        return await who.post("/playback/resolve", json={"id": f"rec:{rid}", "client": "android_tv"})

    # The Kid role holds recordings.view and nothing more of recordings; and a rating ceiling.
    kid = await member(api, "kid", "Kid")
    await recorded(api, "theirs", kid.profile_id, "TV-G")
    await recorded(api, "late", kid.profile_id, "TV-MA")
    r = await play(kid, "theirs")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["id"], d["mode"], d["mime"], d["live"]) == ("rec:theirs", "proxy", "video/x-matroska", False)
    assert (await api.client.get(d["url"])).status_code == 200
    r = await play(kid, "late")
    assert r.status_code == 403 and r.json()["code"] == "content_restricted"
    # Whoever sees everyone's recordings plays them.
    assert (await play(api, "theirs")).status_code == 200
    # Without the permission there is nothing to resolve, whoever the recording belongs to.
    friend = await member(api, "friend", "Friend")
    await recorded(api, "friends", friend.profile_id, None)
    r = await play(friend, "friends")
    assert r.status_code == 403 and r.json()["code"] == "recording_forbidden"
    viewer = await member(api, "friend", "Viewer", grants=["recordings.view"])
    await recorded(api, "viewers", viewer.profile_id, None)
    assert (await play(viewer, "viewers")).status_code == 200
