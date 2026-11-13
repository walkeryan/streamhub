"""The users, roles and audit API, and every guard on who may change whom."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.infra.models import AuditLog, Favorite, Profile, Progress, User
from app.services.errors import Conflict
from app.services.permissions import BUILTIN_ROLES, PERMISSIONS
from tests.conftest import Api
from tests.people import Member, audit_actions, member, owner_id, set_user


async def pending_user(api: Api, name: str = "Newcomer") -> dict[str, Any]:
    """A user as Google sign-in leaves them in approval mode: no role, waiting."""
    created = (await api.post("/users", json={"name": name})).json()
    await set_user(
        api, created["id"], status="pending", role_id=None, kind="google", email=f"{name.lower()}@example.test"
    )
    return {**created, "status": "pending"}


async def audit_rows(api: Api) -> list[AuditLog]:
    async with api.c.db.session() as s:
        return list((await s.execute(select(AuditLog).order_by(AuditLog.id))).scalars())


async def test_list_and_detail(api: Api) -> None:
    kid = await member(api, "kid", name="Small", pin="4321")
    me = await owner_id(api)
    users = {u["id"]: u for u in (await api.get("/users")).json()}
    assert set(users) == {me, kid.id}
    owner = users[me]
    assert (owner["role_id"], owner["role_name"], owner["kind"], owner["status"]) == (
        "owner",
        "Owner",
        "managed",
        "active",
    )
    assert owner["profile_id"] == api.profile_id and owner["now_playing"] == [] and owner["last_seen_at"]
    small = users[kid.id]
    assert small["display_name"] == "Small" and small["has_pin"] and small["on_shared_devices"] is True
    assert [u["id"] for u in (await api.get("/users", params={"status": "pending"})).json()] == []
    assert (await api.get("/users", params={"status": "nope"})).status_code == 422

    async with api.c.db.session() as s:
        s.add(Progress(profile_id=kid.profile_id, ref_id="tmdb:movie:11", position_ms=5000, duration_ms=9000))
        await s.commit()
    detail = (await api.get(f"/users/{kid.id}")).json()
    assert detail["permissions"] == sorted(BUILTIN_ROLES["kid"].permissions)
    assert detail["limits"]["rating_ceiling_movie"] == "PG" and detail["limits"]["max_streams"] == 1
    assert detail["grants"] == detail["denials"] == [] and detail["limit_overrides"] == {}
    assert len(detail["sessions"]) == 1 and detail["sessions"][0]["kind"] in ("api", "browser")
    assert [(h["ref_id"], h["position_ms"]) for h in detail["history"]] == [("tmdb:movie:11", 5000)]
    assert (await api.get("/users/unknown")).status_code == 404


async def test_create_managed_user(api: Api) -> None:
    r = await api.post("/users", json={"name": " Robin ", "role_id": "kid", "avatar": "fox", "pin": "2468"})
    assert r.status_code == 201, r.text
    user = r.json()
    assert (user["display_name"], user["kind"], user["status"], user["role_id"], user["avatar"]) == (
        "Robin",
        "managed",
        "active",
        "kid",
        "fox",
    )
    assert user["has_pin"] and user["approved_at"]
    async with api.c.db.session() as s:
        profile = await s.get(Profile, user["profile_id"])
        assert profile is not None and profile.kids and profile.user_id == user["id"] and not profile.is_admin
        assert "2468" not in (profile.pin_hash or "")
    # More than the twelve profiles the old model allowed.
    for n in range(13):
        assert (await api.post("/users", json={"name": f"User {n}"})).status_code == 201
    assert (await api.post("/users", json={"name": "X", "role_id": "nope"})).status_code == 404
    assert (await api.post("/users", json={"name": ""})).status_code == 422
    assert (await api.post("/users", json={"name": "X", "pin": "12"})).status_code == 422
    assert "user.create" in await audit_actions(api)


async def test_patch_role_overrides_limits_and_profile_fields(api: Api) -> None:
    user = await member(api, "kid")
    r = await api.patch(
        f"/users/{user.id}",
        json={
            "name": "Renamed",
            "avatar": "owl",
            "role_id": "friend",
            "grants": ["recordings.view"],
            "denials": ["rooms.create"],
            "limits": {"max_streams": 3, "rating_ceiling_movie": "PG-13"},
            "on_shared_devices": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["display_name"], body["avatar"], body["role_id"]) == ("Renamed", "owl", "friend")
    expected = (BUILTIN_ROLES["friend"].permissions | {"recordings.view"}) - {"rooms.create"}
    assert body["permissions"] == sorted(expected)
    assert body["limits"] == {
        "max_streams": 3,
        "rating_ceiling_movie": "PG-13",
        "rating_ceiling_tv": None,
        "allow_unrated": True,
    }
    assert body["limit_overrides"] == {"max_streams": 3, "rating_ceiling_movie": "PG-13"}
    assert body["on_shared_devices"] is True and body["on_shared_devices_override"] is True
    async with api.c.db.session() as s:
        profile = await s.get(Profile, user.profile_id)
        assert profile is not None and profile.name == "Renamed" and not profile.kids
    # null lifts one limit; an empty object clears every override.
    lifted = (await api.patch(f"/users/{user.id}", json={"limits": {"max_streams": None}})).json()
    assert lifted["limits"]["max_streams"] is None and lifted["limit_overrides"] == {"max_streams": None}
    cleared = (await api.patch(f"/users/{user.id}", json={"limits": {}, "on_shared_devices": None})).json()
    assert cleared["limits"]["max_streams"] == 1 and cleared["on_shared_devices"] is False
    for bad in (
        {"grants": ["watch.everything"]},
        {"limits": {"max_streams": 0}},
        {"limits": {"rating_ceiling_movie": "TV-MA"}},
    ):
        assert (await api.patch(f"/users/{user.id}", json=bad)).status_code == 400, bad
    assert (await api.patch(f"/users/{user.id}", json={"limits": {"streams": 2}})).status_code == 422
    assert (await api.patch(f"/users/{user.id}", json={"role_id": "nope"})).status_code == 404


async def test_approve_and_deny(api: Api) -> None:
    waiting = await pending_user(api)
    assert [u["id"] for u in (await api.get("/users", params={"status": "pending"})).json()] == [waiting["id"]]
    assert (await api.patch(f"/users/{waiting['id']}", json={"role_id": "kid"})).status_code == 409
    assert (await api.post(f"/users/{waiting['id']}/approve", json={})).status_code == 422
    assert (await api.post(f"/users/{waiting['id']}/approve", json={"role_id": "nope"})).status_code == 404
    me = await owner_id(api)
    approved = await api.post(f"/users/{waiting['id']}/approve", json={"role_id": "friend"})
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert (body["status"], body["role_id"]) == ("active", "friend") and body["approved_at"]
    async with api.c.db.session() as s:
        row = await s.get(User, waiting["id"])
        assert row is not None and row.approved_by_user_id == me and row.version == 1
    again = await api.post(f"/users/{waiting['id']}/approve", json={"role_id": "friend"})
    assert again.status_code == 409 and again.json()["code"] == "not_pending"
    assert (await api.post(f"/users/{waiting['id']}/deny")).status_code == 409

    refused = await pending_user(api, "Stranger")
    assert (await api.post(f"/users/{refused['id']}/deny")).status_code == 204
    assert (await api.get(f"/users/{refused['id']}")).status_code == 404
    async with api.c.db.session() as s:
        assert await s.get(Profile, refused["profile_id"]) is None
    actions = await audit_actions(api)
    assert "user.approve" in actions and "user.deny" in actions


async def test_suspend_reinstate_and_delete(api: Api) -> None:
    user = await member(api, "family")
    async with api.c.db.session() as s:
        s.add(Favorite(profile_id=user.profile_id, kind="vod", ref_id="tmdb:movie:5"))
        await s.commit()
    assert (await api.post(f"/users/{user.id}/reinstate")).status_code == 409
    assert (await api.post(f"/users/{user.id}/suspend")).json()["status"] == "suspended"
    assert (await api.post(f"/users/{user.id}/suspend")).status_code == 409
    assert (await user.get("/settings")).status_code == 401
    assert (await api.post(f"/users/{user.id}/reinstate")).json()["status"] == "active"
    assert (await api.delete(f"/users/{user.id}")).status_code == 204
    assert (await api.delete(f"/users/{user.id}")).status_code == 404
    async with api.c.db.session() as s:
        assert await s.get(Profile, user.profile_id) is None
        assert (await s.execute(select(Favorite).where(Favorite.profile_id == user.profile_id))).first() is None
    assert (await user.get("/settings")).status_code == 401


# ---------------------------------------------------------------- guards
async def test_the_last_active_owner_stays_an_owner(api: Api) -> None:
    me = await owner_id(api)

    def last_owner(r: Any) -> bool:
        return bool(r.status_code == 409 and r.json()["code"] == "last_owner")

    # Alone, the owner can neither suspend nor remove themselves, by either route.
    assert last_owner(await api.post(f"/users/{me}/suspend"))
    assert last_owner(await api.delete(f"/users/{me}"))
    assert last_owner(await api.delete(f"/profiles/{api.profile_id}"))

    second = await member(api, "owner", name="Second owner")
    # Two owners: one may suspend the other. A suspended owner no longer counts as one.
    assert (await second.post(f"/users/{me}/suspend")).status_code == 200
    assert last_owner(await second.post(f"/users/{second.id}/suspend"))
    assert last_owner(await second.req("DELETE", f"/users/{second.id}"))
    assert (await second.post(f"/users/{me}/reinstate")).status_code == 200
    # Both active again: one may demote the other, and is then the last.
    assert (await second.req("PATCH", f"/users/{me}", json={"role_id": "family"})).status_code == 200
    assert last_owner(await second.post(f"/users/{second.id}/suspend"))
    # Demotion is guarded as well, underneath the rules that already keep anyone from trying.
    async with api.c.db.session() as s:
        row = await s.get(User, second.id)
        assert row is not None
        with pytest.raises(Conflict) as refused:
            await api.c.users._keep_an_owner(s, row, "family", "active")
        assert refused.value.code == "last_owner"
        await api.c.users._keep_an_owner(s, row, "owner", "active")


async def test_nobody_edits_their_own_access(api: Api) -> None:
    me = await owner_id(api)
    manager = await member(api, "family", grants=["users.manage"])
    for user, user_id in ((api, me), (manager, manager.id)):
        for body in (
            {"role_id": "friend"},
            {"grants": ["watch.adult"]},
            {"denials": []},
            {"limits": {"max_streams": 9}},
        ):
            r = await user.req("PATCH", f"/users/{user_id}", json=body)
            assert r.status_code == 403 and r.json()["code"] == "self_edit", body
    # Their own name is theirs to change.
    assert (await manager.req("PATCH", f"/users/{manager.id}", json={"name": "Me"})).status_code == 200
    await manager.refresh()
    # Nor may they widen themselves through the role they hold.
    r = await manager.req("PATCH", "/roles/family", json={"permissions": sorted(PERMISSIONS)})
    assert r.status_code == 403 and r.json()["code"] == "self_edit"


async def test_only_an_owner_hands_out_management_access(api: Api) -> None:
    me = await owner_id(api)
    manager = await member(api, "family", name="Manager", grants=["users.manage"])
    target = await member(api, "friend", name="Target")

    def refused(r: Any) -> bool:
        return bool(r.status_code == 403 and r.json()["code"] == "owner_required")

    for permission in ("users.manage", "sources.manage", "system.manage"):
        assert refused(await manager.req("PATCH", f"/users/{target.id}", json={"grants": [permission]})), permission
        assert refused(await manager.post("/roles", json={"name": "Sneaky", "permissions": [permission]}))
        assert refused(await manager.req("PATCH", "/roles/friend", json={"permissions": [permission]}))
    assert refused(await manager.req("PATCH", f"/users/{target.id}", json={"role_id": "owner"}))
    assert refused(await manager.post("/users", json={"name": "Second", "role_id": "owner"}))
    waiting = await pending_user(api)
    assert refused(await manager.post(f"/users/{waiting['id']}/approve", json={"role_id": "owner"}))
    # An owner's account is beyond a manager's reach entirely.
    for method, path, body in (
        ("PATCH", f"/users/{me}", {"name": "Hijacked"}),
        ("POST", f"/users/{me}/suspend", None),
        ("DELETE", f"/users/{me}", None),
    ):
        assert refused(await manager.req(method, path, json=body)), (method, path)
    # A role an owner gave management access to cannot be assigned by a manager either.
    created = await api.post("/roles", json={"name": "Operators", "permissions": ["watch.live", "sources.manage"]})
    assert created.status_code == 201, created.text
    assert refused(await manager.req("PATCH", f"/users/{target.id}", json={"role_id": created.json()["id"]}))
    assert refused(await manager.post(f"/users/{waiting['id']}/approve", json={"role_id": created.json()["id"]}))
    # What a manager may do: ordinary permissions and ordinary roles.
    ok = await manager.req("PATCH", f"/users/{target.id}", json={"role_id": "family", "grants": ["requests.approve"]})
    assert ok.status_code == 200, ok.text
    assert (await manager.post(f"/users/{waiting['id']}/approve", json={"role_id": "kid"})).status_code == 200
    # And the owner may do all of it.
    assert (await api.patch(f"/users/{target.id}", json={"grants": ["system.manage"]})).status_code == 200
    assert (await api.patch(f"/users/{target.id}", json={"role_id": "owner"})).status_code == 200
    detail = (await api.get(f"/users/{target.id}")).json()
    assert detail["grants"] == [] and set(detail["permissions"]) == PERMISSIONS  # Owner carries no overrides
    narrowed = await api.patch(f"/users/{target.id}", json={"denials": ["watch.adult"]})
    assert narrowed.status_code == 400 and narrowed.json()["code"] == "owner_fixed"


async def test_a_manager_cannot_become_someone_with_more_access(api: Api) -> None:
    """A PIN, a password and a paired device each let the setter act as that user."""
    me = await owner_id(api)
    manager = await member(api, "family", name="Manager", grants=["users.manage"])
    operator = await member(api, "friend", name="Operator", grants=["system.manage"])
    kid = await member(api, "kid", name="Small")
    owner_sid = (await api.get("/auth/sessions")).json()[0]["sid"]

    async def pair(target_profile: str) -> tuple[Any, Any]:
        code = (await api.client.post("/api/v1/auth/device")).json()
        approved = await manager.post(
            "/auth/device/approve", json={"user_code": code["user_code"], "profile_id": target_profile}
        )
        return approved, await api.client.post("/api/v1/auth/device/poll", json={"device_code": code["device_code"]})

    def refused(r: Any, code: str) -> bool:
        return bool(r.status_code == 403 and r.json()["code"] == code)

    # An owner: beyond any manager, by every route.
    assert refused(await manager.req("PUT", f"/users/{me}/pin", json={"pin": "1111"}), "owner_required")
    assert refused(await manager.req("PUT", f"/profiles/{api.profile_id}/pin", json={"pin": "1111"}), "owner_required")
    assert refused(
        await manager.req("PATCH", f"/profiles/{api.profile_id}", json={"password": "taken-over"}), "owner_required"
    )
    approved, polled = await pair(api.profile_id)
    assert refused(approved, "owner_required") and polled.status_code == 428
    assert refused(await manager.req("DELETE", f"/auth/sessions/{owner_sid}"), "owner_required")
    login = await api.client.post("/api/v1/auth/login", json={"profile_id": api.profile_id, "password": "taken-over"})
    assert login.status_code == 401
    assert (await api.get("/settings")).status_code == 200

    # Someone who is not an owner but holds what the manager lacks.
    assert refused(await manager.req("PUT", f"/users/{operator.id}/pin", json={"pin": "1111"}), "wider_access")
    assert refused(
        await manager.req("PUT", f"/profiles/{operator.profile_id}/pin", json={"pin": "1111"}), "wider_access"
    )
    approved, polled = await pair(operator.profile_id)
    assert refused(approved, "wider_access") and polled.status_code == 428

    # Someone with no more access than the manager: all of it is ordinary user management.
    assert (await manager.req("PUT", f"/users/{kid.id}/pin", json={"pin": "1111"})).status_code == 204
    approved, polled = await pair(kid.profile_id)
    assert approved.status_code == 204 and polled.json()["profile"]["id"] == kid.profile_id
    kid_sid = (await manager.get("/auth/sessions", params={"user_id": kid.id})).json()[0]["sid"]
    assert (await manager.req("DELETE", f"/auth/sessions/{kid_sid}")).status_code == 204
    # Without users.manage nobody pairs a device to anyone but themselves.
    code = (await api.client.post("/api/v1/auth/device")).json()
    r = await kid.post("/auth/device/approve", json={"user_code": code["user_code"], "profile_id": manager.profile_id})
    assert r.status_code == 403 and r.json()["missing"] == ["users.manage"]
    # The owner may do all of this for anyone.
    assert (await api.put(f"/users/{operator.id}/pin", json={"pin": "1111"})).status_code == 204


async def test_every_mutation_bumps_the_version_and_writes_the_audit_log(api: Api) -> None:
    me = await owner_id(api)
    user = await member(api, "family", pin="2468")

    async def version() -> int:
        async with api.c.db.session() as s:
            row = await s.get(User, user.id)
            assert row is not None
            return row.version

    seen = await version()
    for method, path, body in (
        ("PATCH", f"/users/{user.id}", {"name": "Renamed"}),
        ("PATCH", f"/users/{user.id}", {"role_id": "friend"}),
        ("PATCH", f"/users/{user.id}", {"grants": ["recordings.view"], "limits": {"max_streams": 2}}),
        ("POST", f"/users/{user.id}/suspend", None),
        ("POST", f"/users/{user.id}/reinstate", None),
        ("PATCH", "/roles/friend", {"limits": {"max_streams": 2}}),
    ):
        assert (await api.req(method, path, json=body)).status_code == 200, (method, path)
        assert await version() == seen + 1, (method, path)
        seen += 1
    rows = await audit_rows(api)
    assert all(r.actor_user_id == me for r in rows)
    by_action = {r.action: r for r in rows}
    assert {
        "user.create",
        "user.update",
        "user.role_change",
        "user.access_change",
        "user.suspend",
        "user.reinstate",
        "role.update",
    } <= set(by_action)
    assert by_action["user.role_change"].detail == {"before": "family", "after": "friend"}
    assert by_action["user.role_change"].target_user_id == user.id
    assert by_action["user.access_change"].detail == {
        "before": {"grants": [], "denials": [], "limits": {}},
        "after": {"grants": ["recordings.view"], "denials": [], "limits": {"max_streams": 2}},
    }
    assert by_action["user.suspend"].detail == {"before": "active", "after": "suspended"}
    role_change = by_action["role.update"].detail
    assert role_change["role_id"] == "friend" and role_change["before"]["limits"] == {"max_streams": 1}
    assert role_change["after"]["limits"] == {"max_streams": 2}
    # Nothing secret ever reaches the log.
    dumped = repr([(r.action, r.detail) for r in rows])
    assert "2468" not in dumped and "scrypt" not in dumped and user.tokens["access_token"] not in dumped

    listed = (await api.get("/audit")).json()
    assert listed[0]["action"] == "role.update" and listed[0]["actor_name"] == "Admin"
    mine = (await api.get("/audit", params={"user_id": user.id, "limit": 2})).json()
    assert len(mine) == 2 and all(e["target_user_id"] == user.id for e in mine)
    assert mine[0]["target_name"] == "Renamed"
    assert (await api.get("/audit", params={"limit": 0})).status_code == 422


# ---------------------------------------------------------------- roles
async def test_roles_crud(api: Api) -> None:
    roles = (await api.get("/roles")).json()
    assert [r["id"] for r in roles] == ["owner", "family", "kid", "friend"]
    owner = roles[0]
    assert owner["builtin"] and not owner["editable"] and set(owner["permissions"]) == PERMISSIONS
    assert owner["user_count"] == 1 and roles[2]["limits"]["rating_ceiling_tv"] == "TV-PG"
    assert roles[3]["on_shared_devices"] is False

    created = await api.post(
        "/roles",
        json={
            "name": "Grandparents",
            "permissions": ["watch.library", "watch.live", "rooms.join"],
            "limits": {"max_streams": 2},
            "on_shared_devices": True,
        },
    )
    assert created.status_code == 201, created.text
    role = created.json()
    assert role["id"] == "grandparents" and not role["builtin"] and role["editable"]
    assert role["limits"]["max_streams"] == 2 and role["limits"]["allow_unrated"] is True
    assert (await api.post("/roles", json={"name": "Grandparents"})).json()["id"] == "grandparents-2"
    assert (await api.post("/roles", json={"name": "Bad", "permissions": ["nope"]})).status_code == 400
    assert (await api.post("/roles", json={"name": ""})).status_code == 422

    patched = await api.patch(
        "/roles/grandparents",
        json={"name": "Elders", "permissions": ["watch.library"], "limits": {"allow_unrated": False}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Elders" and patched.json()["permissions"] == ["watch.library"]
    assert patched.json()["limits"] == {
        "max_streams": None,
        "rating_ceiling_movie": None,
        "rating_ceiling_tv": None,
        "allow_unrated": False,
    }
    # The Owner role is fixed, built-ins stay, a role in use stays.
    fixed = await api.patch("/roles/owner", json={"permissions": ["watch.library"]})
    assert fixed.status_code == 409 and fixed.json()["code"] == "role_fixed"
    for builtin in ("owner", "family", "kid", "friend"):
        r = await api.delete(f"/roles/{builtin}")
        assert r.status_code == 409 and r.json()["code"] == "role_builtin"
    elder = await member(api, "grandparents")
    in_use = await api.delete("/roles/grandparents")
    assert in_use.status_code == 409 and in_use.json()["code"] == "role_in_use"
    assert (await api.patch(f"/users/{elder.id}", json={"role_id": "friend"})).status_code == 200
    assert (await api.delete("/roles/grandparents")).status_code == 204
    assert (await api.delete("/roles/grandparents")).status_code == 404
    assert (await api.patch("/roles/nope", json={"name": "X"})).status_code == 404
    actions = await audit_actions(api)
    assert {"role.create", "role.update", "role.delete"} <= set(actions)


async def test_editing_a_role_reaches_every_member_on_their_next_request(api: Api) -> None:
    one = await member(api, "friend", name="One")
    two = await member(api, "friend", name="Two")
    kid = await member(api, "kid", name="Kid")
    r = await api.patch(
        "/roles/friend", json={"permissions": [*sorted(BUILTIN_ROLES["friend"].permissions), "users.invite"]}
    )
    assert r.status_code == 200 and r.json()["user_count"] == 2
    for user in (one, two):
        stale = await user.get("/settings")
        assert stale.status_code == 401 and stale.json()["code"] == "session_stale"
        await user.refresh()
        assert (await user.post("/invites", json={"role_id": "friend"})).status_code == 201
    assert (await kid.get("/settings")).status_code == 200  # another role's members are untouched


# ---------------------------------------------------------------- legacy profile routes
async def test_legacy_profile_routes_create_and_remove_users(api: Api) -> None:
    created = (await api.post("/profiles", json={"name": "Plain"})).json()
    small = (await api.post("/profiles", json={"name": "Small", "kids": True})).json()
    users = {u["profile_id"]: u for u in (await api.get("/users")).json()}
    assert users[created["id"]]["role_id"] == "family" and users[small["id"]]["role_id"] == "kid"
    assert (await api.delete(f"/profiles/{created['id']}")).status_code == 204
    assert created["id"] not in {u["profile_id"] for u in (await api.get("/users")).json()}
    # The profile list still marks who administers, from the user's permissions.
    promoted = await api.patch(f"/users/{users[small['id']]['id']}", json={"role_id": "owner"})
    assert promoted.status_code == 200
    profiles = {p["id"]: p for p in (await api.client.get("/api/v1/profiles")).json()}
    assert profiles[small["id"]]["is_admin"] and profiles[api.profile_id]["is_admin"]
    assert (await api.patch(f"/users/{users[small['id']]['id']}", json={"role_id": "kid"})).status_code == 200
    profiles = {p["id"]: p for p in (await api.client.get("/api/v1/profiles")).json()}
    assert not profiles[small["id"]]["is_admin"]


@pytest.mark.parametrize("role", ["family", "kid", "friend"])
async def test_non_owner_roles_cannot_reach_the_users_api(api: Api, role: str) -> None:
    user: Member = await member(api, role)
    for path in ("/users", "/roles", "/audit"):
        r = await user.get(path)
        assert r.status_code == 403 and r.json()["missing"] == ["users.manage"], path
