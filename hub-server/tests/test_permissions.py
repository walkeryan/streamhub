"""The permission algebra, and what the server does with a token on every request."""

from __future__ import annotations

import jwt
import pytest

from app.services.auth import ALG, Principal
from app.services.errors import ServiceError
from app.services.permissions import (
    BUILTIN_ROLES,
    NO_LIMITS,
    PERMISSIONS,
    Limits,
    clean_limits,
    clean_permissions,
    effective_limits,
    effective_permissions,
    is_wider,
)
from tests.conftest import Api
from tests.people import member, owner_id, set_user

KID = BUILTIN_ROLES["kid"]
FAMILY = BUILTIN_ROLES["family"]


def test_catalogue_and_builtin_roles_match_the_design() -> None:
    assert len(PERMISSIONS) == 16
    assert BUILTIN_ROLES["owner"].permissions == PERMISSIONS and BUILTIN_ROLES["owner"].limits == {}
    assert "watch.adult" not in FAMILY.permissions and {"users.invite", "requests.auto_approve"} <= FAMILY.permissions
    assert not FAMILY.permissions & {"users.manage", "sources.manage", "system.manage", "requests.approve"}
    assert KID.permissions == {
        "watch.library",
        "watch.vod",
        "watch.live",
        "rooms.join",
        "recordings.view",
        "requests.create",
    }
    assert KID.limits == {
        "max_streams": 1,
        "rating_ceiling_movie": "PG",
        "rating_ceiling_tv": "TV-PG",
        "allow_unrated": False,
    }
    friend = BUILTIN_ROLES["friend"]
    assert friend.limits == {"max_streams": 1} and not friend.on_shared_devices
    assert {"rooms.create", "remote.access"} <= friend.permissions and "recordings.view" not in friend.permissions


def test_effective_permissions_are_role_plus_grants_minus_denials() -> None:
    assert effective_permissions(KID.permissions, [], []) == KID.permissions
    assert "rooms.create" in effective_permissions(KID.permissions, ["rooms.create"], [])
    assert "watch.live" not in effective_permissions(KID.permissions, [], ["watch.live"])
    # A denial beats a grant of the same permission, and unknown names never count.
    assert "watch.adult" not in effective_permissions(KID.permissions, ["watch.adult"], ["watch.adult"])
    assert "made.up" not in effective_permissions(["made.up"], ["also.made.up"], [])


def test_effective_limits_overlay_overrides_on_the_role() -> None:
    assert effective_limits({}, {}) == NO_LIMITS
    assert effective_limits(KID.limits, {}) == Limits(1, "PG", "TV-PG", False)
    raised = effective_limits(KID.limits, {"rating_ceiling_movie": "PG-13", "max_streams": None})
    assert raised == Limits(None, "PG-13", "TV-PG", False)
    assert effective_limits({"max_streams": 2}, {"allow_unrated": False}) == Limits(2, None, None, False)
    # Junk in storage never widens anything into a crash or a negative limit.
    assert effective_limits({"max_streams": 0, "rating_ceiling_movie": "XXX"}, {}) == NO_LIMITS


def test_is_wider_compares_permissions_and_every_limit() -> None:
    owner, kid, family = BUILTIN_ROLES["owner"].permissions, KID.permissions, FAMILY.permissions
    kid_limits = effective_limits(KID.limits, {})
    assert is_wider(owner, NO_LIMITS, kid, kid_limits) and not is_wider(kid, kid_limits, owner, NO_LIMITS)
    assert is_wider(family, NO_LIMITS, kid, kid_limits) and not is_wider(kid, kid_limits, family, NO_LIMITS)
    assert not is_wider(kid, kid_limits, kid, kid_limits)
    # Same permissions, looser limit: each limit counts on its own.
    assert is_wider(kid, Limits(2, "PG", "TV-PG", False), kid, kid_limits)
    assert is_wider(kid, Limits(1, "R", "TV-PG", False), kid, kid_limits)
    assert is_wider(kid, Limits(1, "PG", None, False), kid, kid_limits)
    assert is_wider(kid, Limits(1, "PG", "TV-PG", True), kid, kid_limits)
    assert not is_wider(kid, Limits(1, "G", "TV-G", False), kid, kid_limits)
    # Disjoint sets are each wider than the other.
    assert is_wider(frozenset({"rooms.create"}), NO_LIMITS, kid, NO_LIMITS)


def test_validation_rejects_unknown_permissions_and_limits() -> None:
    assert clean_permissions(["watch.vod", "watch.live", "watch.vod"]) == ["watch.live", "watch.vod"]
    with pytest.raises(ServiceError):
        clean_permissions(["watch.everything"])
    assert clean_limits({"max_streams": 3, "allow_unrated": False}) == {"max_streams": 3, "allow_unrated": False}
    assert clean_limits({"rating_ceiling_tv": None}) == {"rating_ceiling_tv": None}
    for bad in ({"max_streams": 0}, {"max_streams": True}, {"rating_ceiling_movie": "TV-PG"}, {"streams": 1}):
        with pytest.raises(ServiceError):
            clean_limits(bad)


def test_principal_is_admin_is_a_view_of_permissions() -> None:
    assert Principal("p", permissions={"system.manage"}).is_admin
    assert not Principal("p", permissions=PERMISSIONS - {"system.manage"}).is_admin
    with pytest.raises(AttributeError):
        Principal("p").is_admin = True  # type: ignore[misc]
    # The constructor shorthand for hand-built principals: Owner, or a plain viewer.
    assert Principal("p", True).permissions == PERMISSIONS
    assert Principal("p", False).permissions == KID.permissions


# ---------------------------------------------------------------- what every request checks
async def test_tokens_carry_user_and_version_and_me_reports_access(api: Api) -> None:
    kid = await member(api, "kid")
    claims = jwt.decode(kid.tokens["access_token"], options={"verify_signature": False})
    assert claims["uid"] == kid.id and claims["uv"] >= 0 and claims["sub"] == kid.profile_id and claims["sid"]
    body = (await kid.get("/auth/me")).json()
    assert body["status"] == "active" and body["user"]["id"] == kid.id and body["tokens"] is None
    assert body["role"] == {"id": "kid", "name": "Kid", "builtin": True}
    assert body["permissions"] == sorted(KID.permissions)
    assert body["limits"] == {
        "max_streams": 1,
        "rating_ceiling_movie": "PG",
        "rating_ceiling_tv": "TV-PG",
        "allow_unrated": False,
    }
    assert body["shared_device"] is False and body["adult_unlocked"] is False
    mine = (await api.get("/auth/me")).json()
    assert mine["role"]["id"] == "owner" and set(mine["permissions"]) == PERMISSIONS
    assert mine["limits"] == {
        "max_streams": None,
        "rating_ceiling_movie": None,
        "rating_ceiling_tv": None,
        "allow_unrated": True,
    }


async def test_forged_admin_claim_grants_nothing(api: Api) -> None:
    """``adm`` used to be the authority. A validly signed token that lies in it changes nothing."""
    kid = await member(api, "kid")
    claims = jwt.decode(kid.tokens["access_token"], options={"verify_signature": False})
    forged = jwt.encode({**claims, "adm": True}, api.c.auth._key, algorithm=ALG)
    r = await api.client.patch("/api/v1/settings", json={"region": "GB"}, headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 403
    assert r.json() == {
        "code": "forbidden",
        "message": "You do not have permission to do this",
        "missing": ["system.manage"],
    }


async def test_stale_user_version_is_401_until_refreshed(api: Api) -> None:
    kid = await member(api, "kid")
    assert (await kid.get("/settings")).status_code == 200
    # Any change to the user bumps the version the token carries.
    assert (await api.patch(f"/users/{kid.id}", json={"grants": ["rooms.create"]})).status_code == 200
    r = await kid.get("/settings")
    assert r.status_code == 401 and r.json()["code"] == "session_stale"
    assert (await kid.refresh()).status_code == 200
    assert "rooms.create" in (await kid.get("/auth/me")).json()["permissions"]


async def test_role_change_applies_on_the_next_request(api: Api) -> None:
    user = await member(api, "family")
    assert (await api.patch(f"/users/{user.id}", json={"role_id": "owner"})).status_code == 200
    await user.refresh()
    assert (await user.req("PATCH", "/settings", json={"region": "GB"})).status_code == 200
    assert (await api.patch(f"/users/{user.id}", json={"role_id": "friend"})).status_code == 200
    await user.refresh()
    assert (await user.req("PATCH", "/settings", json={"region": "US"})).status_code == 403


async def test_suspended_user_is_401_everywhere_including_refresh(api: Api) -> None:
    user = await member(api, "family")
    assert (await api.post(f"/users/{user.id}/suspend")).status_code == 200
    for response in (await user.get("/settings"), await user.get("/auth/me"), await user.refresh()):
        assert response.status_code == 401 and response.json()["code"] == "user_suspended", response.text
    login = await api.client.post("/api/v1/auth/login", json={"profile_id": user.profile_id})
    assert login.status_code == 401 and login.json()["code"] == "user_suspended"
    # Reinstated: the same device carries on after a refresh, no new sign-in.
    assert (await api.post(f"/users/{user.id}/reinstate")).status_code == 200
    assert (await user.refresh()).status_code == 200
    assert (await user.get("/settings")).status_code == 200


async def test_suspension_without_a_version_bump_still_locks_out(api: Api) -> None:
    """Status is read on every request; it does not depend on the token going stale."""
    user = await member(api, "family")
    await set_user(api, user.id, status="suspended")
    r = await user.get("/settings")
    assert r.status_code == 401 and r.json()["code"] == "user_suspended"


async def test_pending_user_reaches_only_auth_me(api: Api) -> None:
    user = await member(api, "family")
    await set_user(api, user.id, status="pending", role_id=None)
    profile = await api.c.auth.get_profile(user.profile_id)
    pending = {"Authorization": f"Bearer {await api.c.auth.issue_pending(profile)}"}
    for method, path in (
        ("GET", "/settings"),
        ("GET", "/home"),
        ("GET", "/profiles/" + user.profile_id + "/prefs"),
        ("GET", "/users"),
        ("POST", "/playback/resolve"),
        ("GET", "/auth/sessions"),
    ):
        r = await api.client.request(method, "/api/v1" + path, headers=pending, json={} if method == "POST" else None)
        assert r.status_code == 403 and r.json()["code"] == "approval_pending", (path, r.text)
    # The pending token is no refresh token, and the old access token is refused too.
    assert (
        await api.client.post("/api/v1/auth/refresh", json={"refresh_token": pending["Authorization"][7:]})
    ).status_code == 401
    r = await user.get("/settings")
    assert r.status_code == 403 and r.json()["code"] == "approval_pending"
    me = await api.client.get("/api/v1/auth/me", headers=pending)
    assert me.status_code == 200 and me.headers["cache-control"] == "private, no-store"
    body = me.json()
    assert body["status"] == "pending" and body["permissions"] == [] and body["role"] is None
    assert body["tokens"] is None and body["user"]["id"] == user.id


async def test_missing_or_foreign_session_row_is_401(api: Api) -> None:
    from sqlalchemy import delete

    from app.infra.models import AuthSession

    user = await member(api, "family")
    other = await member(api, "family", name="Other")
    sid = jwt.decode(user.tokens["access_token"], options={"verify_signature": False})["sid"]
    # A token naming another user's session is not that session.
    claims = jwt.decode(other.tokens["access_token"], options={"verify_signature": False})
    crossed = jwt.encode({**claims, "sid": sid}, api.c.auth._key, algorithm=ALG)
    r = await api.client.get("/api/v1/settings", headers={"Authorization": f"Bearer {crossed}"})
    assert r.status_code == 401 and r.json()["code"] == "session_revoked"
    async with api.c.db.session() as s:
        await s.execute(delete(AuthSession).where(AuthSession.sid == sid))
        await s.commit()
    r = await user.get("/settings")
    assert r.status_code == 401 and r.json()["code"] == "session_revoked"
    assert (await user.refresh()).status_code == 401


async def test_token_from_before_the_upgrade_is_adopted_on_refresh(api: Api) -> None:
    """A refresh token minted before sessions had rows (no ``uid``) signs nobody out."""
    profile = await api.c.auth.get_profile(api.profile_id)
    legacy_access = api.c.auth._encode(profile, "access", 900, "legacy-session")
    legacy_refresh = api.c.auth._encode(profile, "refresh", 900, "legacy-session")
    r = await api.client.get("/api/v1/settings", headers={"Authorization": f"Bearer {legacy_access}"})
    assert r.status_code == 401
    r = await api.client.post("/api/v1/auth/refresh", json={"refresh_token": legacy_refresh})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await api.client.get("/api/v1/settings", headers=headers)).status_code == 200
    sessions = (await api.client.get("/api/v1/auth/sessions", headers=headers)).json()
    assert "legacy-session" in {s["sid"] for s in sessions}
    # Once adopted it is an ordinary session: revoking it ends the old refresh token as well.
    assert (await api.delete("/auth/sessions/legacy-session")).status_code == 204
    assert (await api.client.post("/api/v1/auth/refresh", json={"refresh_token": legacy_refresh})).status_code == 401


async def test_owner_is_the_bootstrap_profile_and_last_seen_is_recorded(api: Api) -> None:
    me = (await api.get("/auth/me")).json()
    assert me["user"]["profile_id"] == api.profile_id and me["user"]["kind"] == "managed"
    assert me["user"]["last_seen_at"] is not None
    assert await owner_id(api) == me["user"]["id"]
