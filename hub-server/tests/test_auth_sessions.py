"""Sessions are rows: created with the tokens, listable, revocable, and checked on every request."""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest
from sqlalchemy import select, update

from app.infra.models import AuthSession, RevokedSession, User, utcnow
from app.services.errors import Unauthorized
from tests.conftest import Api
from tests.people import audit_actions, member, owner_id

BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def sid_of(token: str) -> str:
    return str(jwt.decode(token, options={"verify_signature": False})["sid"])


async def rows(api: Api) -> dict[str, AuthSession]:
    async with api.c.db.session() as s:
        return {r.sid: r for r in (await s.execute(select(AuthSession))).scalars()}


async def test_sign_in_creates_one_row_and_refresh_keeps_it(api: Api) -> None:
    login = await api.client.post(
        "/api/v1/auth/login",
        json={"profile_id": api.profile_id, "password": "test-admin-pw"},
        # Through the trusted local proxy: its appended hop is the client, whatever was written before it.
        # (A home address: signing in with a profile is for the home network.)
        headers={"User-Agent": BROWSER, "X-Forwarded-For": "192.0.2.1, 192.168.1.20"},
    )
    tokens = login.json()
    sid = sid_of(tokens["access_token"])
    row = (await rows(api))[sid]
    assert (row.kind, row.user_agent, row.ip, row.shared_device) == ("browser", BROWSER, "192.168.1.20", False)
    assert row.label == "Chrome on Linux"
    assert row.user_id == await owner_id(api) and row.profile_id == api.profile_id and row.revoked_at is None
    before = len(await rows(api))
    refreshed = await api.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}, headers={"User-Agent": BROWSER}
    )
    assert refreshed.status_code == 200 and sid_of(refreshed.json()["access_token"]) == sid
    assert len(await rows(api)) == before

    headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    listed = (await api.client.get("/api/v1/auth/sessions", headers=headers)).json()
    mine = next(s for s in listed if s["sid"] == sid)
    assert mine["current"] is True and mine["kind"] == "browser" and mine["user_agent"] == BROWSER
    assert [s["current"] for s in listed].count(True) == 1
    # A client that is not a browser is recorded as such.
    script = await api.client.post(
        "/api/v1/auth/login", json={"profile_id": api.profile_id, "password": "test-admin-pw"}
    )
    scripted = (await rows(api))[sid_of(script.json()["access_token"])]
    assert (scripted.kind, scripted.label) == ("api", None)


async def test_paired_tv_gets_a_tv_session(api: Api) -> None:
    code = (await api.client.post("/api/v1/auth/device")).json()
    assert (await api.post("/auth/device/approve", json={"user_code": code["user_code"]})).status_code == 204
    paired = await api.client.post("/api/v1/auth/device/poll", json={"device_code": code["device_code"]})
    assert paired.status_code == 200
    tv = (await rows(api))[sid_of(paired.json()["access_token"])]
    assert (tv.kind, tv.label) == ("tv", "TV")


def test_device_labels() -> None:
    from app.api.deps import device_label

    edge = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0"
    safari = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/604.1"
    firefox = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0"
    assert device_label("browser", edge) == "Edge on Windows"
    assert device_label("browser", safari) == "Safari on iPhone"
    assert device_label("browser", firefox) == "Firefox on macOS"
    assert device_label("browser", "Mozilla/5.0 (Unknown) Other/1.0") is None
    assert device_label("api", edge) is None and device_label("tv", "") == "TV"


async def test_revoke_ends_the_session_on_the_next_call(api: Api) -> None:
    user = await member(api, "family")
    second = await api.client.post("/api/v1/auth/login", json={"profile_id": user.profile_id})
    keep = {"Authorization": f"Bearer {second.json()['access_token']}"}
    sid = sid_of(user.tokens["access_token"])
    assert len((await user.get("/auth/sessions")).json()) == 2

    assert (await user.req("DELETE", f"/auth/sessions/{sid}")).status_code == 204
    for response in (await user.get("/settings"), await user.get("/auth/me"), await user.refresh()):
        assert response.status_code == 401 and response.json()["code"] == "session_revoked", response.text
    # The stream and image paths check the same session through the legacy chokepoint.
    with pytest.raises(Unauthorized):
        await api.c.auth.session_valid(user.profile_id, 0, sid)
    async with api.c.db.session() as s:
        assert await s.get(RevokedSession, sid) is not None
    # The other device is untouched and no longer lists the revoked one.
    remaining = (await api.client.get("/api/v1/auth/sessions", headers=keep)).json()
    assert [s["sid"] for s in remaining] == [sid_of(second.json()["access_token"])]
    assert (await api.client.delete(f"/api/v1/auth/sessions/{sid}", headers=keep)).status_code == 404
    assert "session.revoke" in await audit_actions(api)


async def test_only_a_user_manager_reaches_other_peoples_sessions(api: Api) -> None:
    user = await member(api, "family")
    other = await member(api, "family", name="Other")
    sid = sid_of(other.tokens["access_token"])
    # Without users.manage another user's session does not exist.
    assert (await user.req("DELETE", f"/auth/sessions/{sid}")).status_code == 404
    assert (await user.get("/auth/sessions", params={"user_id": other.id})).status_code == 403
    assert (await other.get("/settings")).status_code == 200
    # Asking for one's own by id is fine.
    assert (await user.get("/auth/sessions", params={"user_id": user.id})).status_code == 200

    listed = await api.get("/auth/sessions", params={"user_id": other.id})
    assert [s["sid"] for s in listed.json()] == [sid] and listed.json()[0]["current"] is False
    assert (await api.delete(f"/auth/sessions/{sid}")).status_code == 204
    assert (await other.get("/settings")).status_code == 401
    detail = (await api.get(f"/users/{other.id}")).json()
    assert detail["sessions"] == []


async def test_last_seen_is_written_at_most_once_a_minute(api: Api) -> None:
    user = await member(api, "family")
    sid = sid_of(user.tokens["access_token"])
    past = utcnow() - timedelta(hours=1)

    async def rewind() -> None:
        async with api.c.db.session() as s:
            await s.execute(update(AuthSession).where(AuthSession.sid == sid).values(last_seen_at=past))
            await s.execute(update(User).where(User.id == user.id).values(last_seen_at=past))
            await s.commit()

    api.c.sessions._touched.clear()
    await rewind()
    assert (await user.get("/settings")).status_code == 200
    first = (await rows(api))[sid].last_seen_at
    assert first > past
    await rewind()
    assert (await user.get("/settings")).status_code == 200
    assert (await rows(api))[sid].last_seen_at == past  # inside the minute: a read stays a read
    api.c.sessions._touched[sid] -= 61
    assert (await user.get("/settings")).status_code == 200
    assert (await rows(api))[sid].last_seen_at > past
    assert (await api.get(f"/users/{user.id}")).json()["last_seen_at"] is not None


async def test_legacy_session_consumption_revokes_the_row(api: Api) -> None:
    user = await member(api, "family")
    sid = sid_of(user.tokens["access_token"])
    await api.c.auth.consume_session(user.profile_id, 0, sid)
    assert (await rows(api))[sid].revoked_at is not None
    assert (await user.get("/settings")).status_code == 401
    await api.c.auth.revoke_session(sid_of(api.headers["Authorization"][7:]))
    assert (await api.get("/settings")).status_code == 401


async def test_expired_rows_are_pruned_and_removing_a_user_removes_sessions(api: Api) -> None:
    user = await member(api, "family")
    sid = sid_of(user.tokens["access_token"])
    async with api.c.db.session() as s:
        await s.execute(
            update(AuthSession)
            .where(AuthSession.sid == sid)
            .values(last_seen_at=utcnow() - timedelta(seconds=api.c.auth.refresh_ttl + 60))
        )
        await s.commit()
    other = await member(api, "family", name="Other")  # any new session prunes
    assert sid not in await rows(api)
    assert (await api.delete(f"/users/{other.id}")).status_code == 204
    assert sid_of(other.tokens["access_token"]) not in await rows(api)
    assert (await other.get("/settings")).status_code == 401


async def test_now_playing_is_an_empty_list_until_streams_are_tagged(api: Api) -> None:
    assert await api.c.sessions.now_playing(await owner_id(api)) == []
    assert all(u["now_playing"] == [] for u in (await api.get("/users")).json())
