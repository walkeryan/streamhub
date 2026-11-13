"""Shared household devices: pairing as shared, the "Who's watching?" list, and switching users."""

from __future__ import annotations

from typing import Any

import jwt
from sqlalchemy import select

from app.infra.models import AuditLog, AuthSession
from app.infra.settings import AppSettings
from app.services import sessions as sessions_module
from tests.conftest import Api
from tests.people import Member, member, owner_id


def claims_of(token: str) -> dict[str, Any]:
    out: dict[str, Any] = jwt.decode(token, options={"verify_signature": False})
    return out


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def session_row(api: Api, sid: str) -> AuthSession:
    async with api.c.db.session() as s:
        row = await s.get(AuthSession, sid)
        assert row is not None
        return row


async def pair(api: Api, approver: dict[str, str] | None = None, **body: Any) -> dict[str, Any]:
    """Pair a TV: start, approve as ``approver`` (the owner by default), poll."""
    code = (await api.client.post("/api/v1/auth/device")).json()
    approved = await api.client.post(
        "/api/v1/auth/device/approve", json={"user_code": code["user_code"], **body}, headers=approver or api.headers
    )
    assert approved.status_code == 204, approved.text
    paired = await api.client.post("/api/v1/auth/device/poll", json={"device_code": code["device_code"]})
    assert paired.status_code == 200, paired.text
    out: dict[str, Any] = paired.json()
    return out


async def switch(api: Api, tokens: dict[str, Any], user_id: str, pin: str | None = None) -> Any:
    body: dict[str, Any] = {"user_id": user_id}
    if pin is not None:
        body["pin"] = pin
    return await api.client.post("/api/v1/auth/switch", json=body, headers=bearer(tokens))


async def user(api: Api, name: str, role_id: str = "family", pin: str | None = None, **patch: Any) -> dict[str, Any]:
    """A managed user who never signs in on their own: reached only through the picker."""
    body: dict[str, Any] = {"name": name, "role_id": role_id}
    if pin:
        body["pin"] = pin
    created = await api.post("/users", json=body)
    assert created.status_code == 201, created.text
    if patch:
        assert (await api.patch(f"/users/{created.json()['id']}", json=patch)).status_code == 200
    out: dict[str, Any] = created.json()
    return out


# ---------------------------------------------------------------- pairing and marking
async def test_pairing_as_shared_marks_the_session(api: Api) -> None:
    tv = await pair(api, shared_device=True)
    row = await session_row(api, claims_of(tv["access_token"])["sid"])
    assert (row.kind, row.shared_device, row.device_owner_user_id) == ("tv", True, await owner_id(api))
    me = (await api.client.get("/api/v1/auth/me", headers=bearer(tv))).json()
    assert me["shared_device"] is True
    listed = (await api.client.get("/api/v1/auth/sessions", headers=bearer(tv))).json()
    assert next(s for s in listed if s["current"])["shared_device"] is True
    # Unasked, a pairing stays a personal device.
    plain = await pair(api)
    row = await session_row(api, claims_of(plain["access_token"])["sid"])
    assert (row.shared_device, row.device_owner_user_id) == (False, None)
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(plain))).json()["shared_device"] is False


async def test_only_a_users_manager_pairs_a_shared_device(api: Api) -> None:
    parent = await member(api, "family", "Parent")
    code = (await api.client.post("/api/v1/auth/device")).json()
    refused = await parent.post("/auth/device/approve", json={"user_code": code["user_code"], "shared_device": True})
    assert refused.status_code == 403 and refused.json()["missing"] == ["users.manage"]
    # Nothing was approved by the refused call.
    waiting = await api.client.post("/api/v1/auth/device/poll", json={"device_code": code["device_code"]})
    assert waiting.status_code == 428
    tv = await pair(api, parent.headers, shared_device=False)
    assert (await session_row(api, claims_of(tv["access_token"])["sid"])).shared_device is False


async def test_a_manager_who_lost_the_permission_before_the_poll_pairs_a_personal_device(api: Api) -> None:
    manager = await member(api, "family", "Manager", grants=["users.manage"])
    await manager.refresh()
    code = (await api.client.post("/api/v1/auth/device")).json()
    approved = await manager.post("/auth/device/approve", json={"user_code": code["user_code"], "shared_device": True})
    assert approved.status_code == 204, approved.text
    assert (await api.patch(f"/users/{manager.id}", json={"grants": []})).status_code == 200
    paired = await api.client.post("/api/v1/auth/device/poll", json={"device_code": code["device_code"]})
    assert paired.status_code == 200
    assert (await session_row(api, claims_of(paired.json()["access_token"])["sid"])).shared_device is False


async def test_patch_session_marks_unmarks_and_relabels(api: Api) -> None:
    parent = await member(api, "family", "Parent")
    sid = claims_of(parent.tokens["access_token"])["sid"]
    # A manager marks anyone's session.
    marked = await api.patch(f"/auth/sessions/{sid}", json={"shared_device": True, "label": "Den TV"})
    assert marked.status_code == 200, marked.text
    body = marked.json()
    assert (body["sid"], body["shared_device"], body["label"], body["current"]) == (sid, True, "Den TV", False)
    assert body["user_id"] == parent.id
    row = await session_row(api, sid)
    assert (row.shared_device, row.device_owner_user_id, row.label) == (True, await owner_id(api), "Den TV")
    assert (await parent.get("/auth/me")).json()["shared_device"] is True
    assert (await parent.get("/auth/switchable")).status_code == 200
    # Its user may relabel it but not change what kind of device it is.
    relabelled = await parent.req("PATCH", f"/auth/sessions/{sid}", json={"label": "  Living room  "})
    assert relabelled.status_code == 200 and relabelled.json()["label"] == "Living room"
    assert relabelled.json()["shared_device"] is True and relabelled.json()["current"] is True
    refused = await parent.req("PATCH", f"/auth/sessions/{sid}", json={"shared_device": False})
    assert refused.status_code == 403 and refused.json()["missing"] == ["users.manage"]
    assert (await session_row(api, sid)).shared_device is True
    unmarked = await api.patch(f"/auth/sessions/{sid}", json={"shared_device": False})
    assert unmarked.status_code == 200 and unmarked.json()["shared_device"] is False
    row = await session_row(api, sid)
    assert (row.shared_device, row.device_owner_user_id, row.label) == (False, None, "Living room")
    assert (await parent.get("/auth/switchable")).status_code == 403
    async with api.c.db.session() as s:
        logged = [
            (r.actor_user_id, r.target_user_id, r.detail)
            for r in (await s.execute(select(AuditLog).where(AuditLog.action == "session.update"))).scalars()
        ]
    assert [d["shared_device"] for a, _, d in logged if "shared_device" in d and a == await owner_id(api)] == [
        True,
        False,
    ]
    assert all(target == parent.id for _, target, _ in logged)


async def test_patch_session_guards(api: Api) -> None:
    parent = await member(api, "family", "Parent")
    other = await member(api, "family", "Other")
    theirs = claims_of(other.tokens["access_token"])["sid"]
    mine = claims_of(api.headers["Authorization"].split()[1])["sid"]
    # Someone else's session does not exist for a caller without users.manage.
    assert (await parent.req("PATCH", f"/auth/sessions/{theirs}", json={"label": "x"})).status_code == 404
    assert (await api.patch("/auth/sessions/unknown", json={"label": "x"})).status_code == 404
    assert (await api.patch(f"/auth/sessions/{theirs}", json={"label": "x" * 129})).status_code == 422
    # An owner's device is only ever changed by an owner.
    manager = await member(api, "family", "Manager", grants=["users.manage"])
    await manager.refresh()
    refused = await manager.req("PATCH", f"/auth/sessions/{mine}", json={"shared_device": True})
    assert refused.status_code == 403 and refused.json()["code"] == "owner_required"
    assert (await manager.req("PATCH", f"/auth/sessions/{theirs}", json={"shared_device": True})).status_code == 200
    # A revoked session is gone.
    assert (await api.delete(f"/auth/sessions/{theirs}")).status_code == 204
    assert (await api.patch(f"/auth/sessions/{theirs}", json={"label": "x"})).status_code == 404


# ---------------------------------------------------------------- who's watching
async def test_switchable_lists_the_household_in_order(api: Api) -> None:
    me = await owner_id(api)
    zed = await user(api, "Zed", "family", pin="1234")
    amy = await user(api, "amy", "family")
    kid = await user(api, "Kid", "kid")
    await user(api, "Guest", "friend")  # Friend is off shared devices by default
    visitor = await user(api, "Visitor", "friend", on_shared_devices=True)
    await user(api, "Hidden", "family", on_shared_devices=False)
    gone = await user(api, "Gone", "family")
    assert (await api.post(f"/users/{gone['id']}/suspend")).status_code == 200

    tv = await pair(api, shared_device=True)
    r = await api.client.get("/api/v1/auth/switchable", headers=bearer(tv))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    users = r.json()["users"]
    assert [u["id"] for u in users] == [me, amy["id"], zed["id"], kid["id"], visitor["id"]]
    assert set(users[0]) == {"id", "display_name", "avatar", "picture", "role_name", "has_pin", "current"}
    assert [u["current"] for u in users] == [True, False, False, False, False]
    assert [u["has_pin"] for u in users] == [False, False, True, False, False]
    assert [u["role_name"] for u in users] == ["Owner", "Family", "Family", "Kid", "Friend"]
    assert users[1]["display_name"] == "amy" and users[1]["avatar"] is None and users[1]["picture"] is None


async def test_switchable_and_switch_need_a_shared_device(api: Api) -> None:
    kid = await user(api, "Kid", "kid")
    r = await api.get("/auth/switchable")
    assert r.status_code == 403 and r.json()["code"] == "not_shared_device"
    r = await api.post("/auth/switch", json={"user_id": kid["id"]})
    assert r.status_code == 403 and r.json()["code"] == "not_shared_device"
    plain = await pair(api)
    assert (await switch(api, plain, kid["id"])).json()["code"] == "not_shared_device"
    assert (await api.client.get("/api/v1/auth/switchable")).status_code == 401


# ---------------------------------------------------------------- switching
async def test_switch_rebinds_the_same_session_and_kills_the_old_tokens(api: Api) -> None:
    me = await owner_id(api)
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True)
    sid = claims_of(tv["access_token"])["sid"]

    r = await switch(api, tv, kid["id"])  # owner -> kid: narrower, no PIN anywhere
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    body = r.json()
    assert set(body) == {"access_token", "refresh_token", "profile", "user"}
    assert body["profile"]["id"] == kid["profile_id"] and body["profile"]["name"] == "Kid"
    assert (body["user"]["id"], body["user"]["display_name"], body["user"]["role_name"]) == (kid["id"], "Kid", "Kid")
    assert body["user"]["has_pin"] is False and body["user"]["current"] is True
    # A picker entry and the /auth/me user in one: a client may read it as either.
    assert {"id", "display_name", "avatar", "picture", "role_name", "has_pin", "current"} <= set(body["user"])
    assert (body["user"]["kind"], body["user"]["role_id"]) == ("managed", "kid")
    assert (body["user"]["status"], body["user"]["profile_id"]) == ("active", kid["profile_id"])
    claims = claims_of(body["access_token"])
    assert (claims["sid"], claims["sub"], claims["uid"]) == (sid, kid["profile_id"], kid["id"])

    # One device, one row: re-bound, still shared, still paired by the owner.
    row = await session_row(api, sid)
    assert (row.user_id, row.profile_id, row.shared_device, row.device_owner_user_id) == (
        kid["id"],
        kid["profile_id"],
        True,
        me,
    )
    async with api.c.db.session() as s:
        assert len(list((await s.execute(select(AuthSession).where(AuthSession.kind == "tv"))).scalars())) == 1
        logged = list((await s.execute(select(AuditLog).where(AuditLog.action == "session.switch"))).scalars())
    assert [(a.actor_user_id, a.target_user_id) for a in logged] == [(me, kid["id"])]
    assert "pin" not in str(logged[0].detail)

    # The kid's own access applies at once; the owner's tokens for this device are dead.
    mine = (await api.client.get("/api/v1/auth/me", headers=bearer(body))).json()
    assert mine["user"]["id"] == kid["id"] and mine["shared_device"] is True
    assert mine["limits"]["rating_ceiling_movie"] == "PG" and "users.manage" not in mine["permissions"]
    assert (await api.client.get("/api/v1/users", headers=bearer(body))).status_code == 403
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(tv))).status_code == 401
    stale = await api.client.post("/api/v1/auth/refresh", json={"refresh_token": tv["refresh_token"]})
    assert stale.status_code == 401
    # The new pair refreshes on the same session.
    fresh = await api.client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert fresh.status_code == 200 and claims_of(fresh.json()["access_token"])["sid"] == sid
    # The owner's other sessions are untouched.
    assert (await api.get("/auth/me")).status_code == 200


async def test_wider_target_needs_a_pin(api: Api) -> None:
    parent = await user(api, "Parent", "family")
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True)
    as_kid = (await switch(api, tv, kid["id"])).json()

    # No PIN on the parent: a kid may not step into it at all.
    r = await switch(api, as_kid, parent["id"])
    assert r.status_code == 409 and r.json()["code"] == "pin_setup_required"
    assert (await api.put(f"/users/{parent['id']}/pin", json={"pin": "2468"})).status_code == 204

    r = await switch(api, as_kid, parent["id"])
    assert r.status_code == 401 and r.json()["code"] == "pin_required"
    r = await switch(api, as_kid, parent["id"], "0000")
    assert r.status_code == 401 and r.json()["code"] == "pin_invalid"
    # A refused switch changes nothing: the kid is still the kid.
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(as_kid))).json()["user"]["id"] == kid["id"]

    r = await switch(api, as_kid, parent["id"], "2468")
    assert r.status_code == 200, r.text
    as_parent = r.json()
    assert as_parent["user"]["id"] == parent["id"] and as_parent["user"]["has_pin"] is True
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(as_kid))).status_code == 401
    # Parent -> kid: narrower and the kid has no PIN, so none is asked for.
    assert (await switch(api, as_parent, kid["id"])).status_code == 200


async def test_a_target_with_a_pin_always_needs_it(api: Api) -> None:
    kid = await user(api, "Kid", "kid", pin="1111")
    tv = await pair(api, shared_device=True)
    r = await switch(api, tv, kid["id"])  # the owner, stepping down
    assert r.status_code == 401 and r.json()["code"] == "pin_required"
    assert (await switch(api, tv, kid["id"], "1111")).status_code == 200


async def test_returning_to_a_user_does_not_revive_their_old_tokens(api: Api) -> None:
    kid = await user(api, "Kid", "kid")
    other = await user(api, "Sibling", "kid")
    tv = await pair(api, shared_device=True, profile_id=kid["profile_id"])
    first = tv
    as_other = (await switch(api, first, other["id"])).json()
    back = await switch(api, as_other, kid["id"])
    assert back.status_code == 200
    # Same user, same session row again: the tokens from before the first switch stay dead.
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(first))).status_code == 401
    assert (
        await api.client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    ).status_code == 401
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(back.json()))).status_code == 200


async def test_switch_to_yourself_is_a_no_op(api: Api) -> None:
    me = await owner_id(api)
    tv = await pair(api, shared_device=True)
    r = await switch(api, tv, me)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["id"] == me and r.json()["profile"]["id"] == api.profile_id
    assert claims_of(r.json()["access_token"])["sid"] == claims_of(tv["access_token"])["sid"]
    # Nothing was consumed and nothing was logged.
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(tv))).status_code == 200
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(r.json()))).status_code == 200
    async with api.c.db.session() as s:
        assert not list((await s.execute(select(AuditLog).where(AuditLog.action == "session.switch"))).scalars())


async def test_only_listed_users_can_be_switched_to(api: Api) -> None:
    guest = await user(api, "Guest", "friend")
    gone = await user(api, "Gone", "family")
    assert (await api.post(f"/users/{gone['id']}/suspend")).status_code == 200
    tv = await pair(api, shared_device=True)
    for target in (guest["id"], gone["id"], "nobody"):
        r = await switch(api, tv, target)
        assert r.status_code == 404, r.text
    assert (await api.client.post("/api/v1/auth/switch", json={}, headers=bearer(tv))).status_code == 422
    assert (await switch(api, tv, guest["id"], "12")).status_code == 422


async def test_five_wrong_pins_lock_the_target(api: Api) -> None:
    parent = await user(api, "Parent", "family", pin="2468")
    other = await user(api, "Other", "family", pin="1357")
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True, profile_id=kid["profile_id"])
    for _ in range(5):
        assert (await switch(api, tv, parent["id"], "0000")).json()["code"] == "pin_invalid"
    locked = await switch(api, tv, parent["id"], "2468")  # even the right PIN waits
    assert locked.status_code == 429 and locked.json()["code"] == "rate_limited"
    assert 0 < int(locked.headers["retry-after"]) <= 15 * 60 + 1
    assert (await switch(api, tv, parent["id"])).status_code == 429
    # The lock is per target, whichever device asks: someone else's PIN still works.
    tv2 = await pair(api, shared_device=True, profile_id=kid["profile_id"])
    assert (await switch(api, tv2, parent["id"], "2468")).status_code == 429
    assert (await switch(api, tv2, other["id"], "1357")).status_code == 200
    # Fifteen minutes on, the target opens again.
    failures = api.c.sessions._pin_failures
    for key, times in failures.items():
        failures[key] = [t - sessions_module.PIN_WINDOW_S - 1 for t in times]
    assert (await switch(api, tv, parent["id"], "2468")).status_code == 200


async def test_parallel_guesses_share_the_budget(api: Api) -> None:
    import asyncio

    parent = await user(api, "Parent", "family", pin="2468")
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True, profile_id=kid["profile_id"])
    guesses = await asyncio.gather(*(switch(api, tv, parent["id"], f"{n:04d}") for n in range(12)))
    codes = [r.json()["code"] for r in guesses]
    assert codes.count("pin_invalid") == 5 and codes.count("rate_limited") == 7


async def test_a_correct_pin_clears_earlier_failures(api: Api) -> None:
    parent = await user(api, "Parent", "family", pin="2468")
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True, profile_id=kid["profile_id"])
    current = tv
    for _ in range(3):
        for _ in range(4):
            assert (await switch(api, current, parent["id"], "0000")).status_code == 401
        current = (await switch(api, current, parent["id"], "2468")).json()
        current = (await switch(api, current, kid["id"])).json()


async def test_switch_is_closed_under_the_legacy_owner_pin_regime(settings: AppSettings, api: Api) -> None:
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True)
    api.c.settings.content_policy_enabled = True
    try:
        r = await switch(api, tv, kid["id"])
    finally:
        api.c.settings.content_policy_enabled = False
    assert r.status_code in (403, 409)
    assert (await session_row(api, claims_of(tv["access_token"])["sid"])).user_id == await owner_id(api)


async def test_member_helper_sessions_are_personal(api: Api) -> None:
    parent: Member = await member(api, "family", "Parent")
    assert (await parent.get("/auth/switchable")).json()["code"] == "not_shared_device"


def test_revision_0011_upgrades_live_sessions_in_place(tmp_path: Any) -> None:
    import sqlite3

    from alembic import command

    from tests.test_users_migration import NOW, config

    path = tmp_path / "upgrade.db"
    cfg = config(path)
    command.upgrade(cfg, "0010")
    db = sqlite3.connect(path)
    db.execute("INSERT INTO role (id, name, builtin, permissions, limits, on_shared_devices, updated_at) "
               "VALUES ('r', 'R', 0, '[]', '{}', 1, ?)", (NOW,))  # fmt: skip
    db.execute(
        "INSERT INTO user (id, kind, display_name, status, role_id, grants, denials, limits, version, "
        "plex_account_linked, created_at) VALUES ('u', 'managed', 'U', 'active', 'r', '[]', '[]', '{}', 0, 0, ?)",
        (NOW,),
    )
    db.execute(
        "INSERT INTO profile (id, name, is_admin, theme_id, languages, kids, content_policy, token_version, prefs, "
        "created_at, user_id) VALUES ('p', 'U', 0, 'midnight', '[]', 0, '{}', 0, '{}', ?, 'u')",
        (NOW,),
    )
    db.execute(
        "INSERT INTO auth_session (sid, user_id, profile_id, kind, shared_device, created_at, last_seen_at) "
        "VALUES ('s', 'u', 'p', 'tv', 0, ?, ?)",
        (NOW, NOW),
    )
    db.execute(
        "INSERT INTO device_code (device_code, user_code, expires_at, consumed) VALUES ('d', 'ABC234', ?, 0)", (NOW,)
    )
    db.commit()
    db.close()
    command.upgrade(cfg, "head")
    db = sqlite3.connect(path)
    assert db.execute("SELECT generation FROM auth_session WHERE sid = 's'").fetchone() == (0,)
    assert db.execute("SELECT shared_device, approved_by_user_id FROM device_code").fetchone() == (0, None)
    db.close()
    command.downgrade(cfg, "0010")
    db = sqlite3.connect(path)
    assert "generation" not in {r[1] for r in db.execute("PRAGMA table_info(auth_session)")}
    assert db.execute("SELECT sid FROM auth_session").fetchone() == ("s",)
    db.close()
