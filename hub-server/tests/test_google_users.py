"""Google sign-in for everyone: bootstrap owner, approval, invites, and the refusals."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import respx
from sqlalchemy import select, update

from app.infra.models import AuditLog, Invite, Notice, Profile, User, utcnow
from tests.conftest import Api
from tests.people import Member, audit_actions, member, owner_id
from tests.test_google_auth import configure, mock_google


async def google(api: Api, subject: str, email: str, *, invite: str = "", name: str = "") -> httpx.Response:
    """Drive start -> callback for one synthetic Google identity; returns the callback redirect."""
    params = {"invite": invite} if invite else {}
    start = await api.client.get("/api/v1/auth/google/start", params=params)
    assert start.status_code == 303, start.text
    query = parse_qs(urlsplit(start.headers["location"]).query)
    assert "invite" not in start.headers["location"] and (not invite or invite not in start.headers["location"])
    claims: dict[str, Any] = {"sub": subject, "email": email}
    if name:
        claims["name"] = name
    mock_google(query["nonce"][0], **claims)
    return await api.client.get(
        "/api/v1/auth/google/callback", params={"state": query["state"][0], "code": "synthetic-code"}
    )


async def complete(api: Api) -> httpx.Response:
    return await api.client.post("/api/v1/auth/google/complete", headers={"Origin": api.c.google_auth.origin})


async def sign_in(api: Api, subject: str, email: str, **kw: Any) -> dict[str, Any]:
    callback = await google(api, subject, email, **kw)
    assert callback.headers["location"] == "/?google_complete=1", callback.headers["location"]
    done = await complete(api)
    assert done.status_code == 200, done.text
    body: dict[str, Any] = done.json()
    return body


async def users(api: Api) -> dict[str, User]:
    async with api.c.db.session() as s:
        return {u.google_subject or u.id: u for u in (await s.execute(select(User))).scalars()}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@respx.mock
async def test_bootstrap_address_claims_the_owner_once(api: Api) -> None:
    configure(api)
    me = await owner_id(api)
    body = await sign_in(api, "owner-subject", "owner@example.test")
    assert body["status"] == "active" and body["profile"]["id"] == api.profile_id and body["profile"]["is_admin"]
    owner = (await users(api))["owner-subject"]
    assert (owner.id, owner.kind, owner.email, owner.role_id) == (me, "google", "owner@example.test", "owner")
    assert "user.google_link" in await audit_actions(api)
    mine = (await api.client.get("/api/v1/auth/me", headers=bearer(body["access_token"]))).json()
    assert mine["role"]["id"] == "owner" and mine["user"]["kind"] == "google"
    # The same address under another Google identity is a stranger: the claim happens once.
    again = await sign_in(api, "impostor-subject", "owner@example.test")
    assert again["status"] == "pending"
    assert (await users(api))["impostor-subject"].role_id is None
    # And the owner keeps signing in as themselves.
    assert (await sign_in(api, "owner-subject", "owner@example.test"))["profile"]["id"] == api.profile_id


@respx.mock
async def test_owner_address_setting_wins_over_the_retired_allowlist(api: Api) -> None:
    configure(api)
    api.c.settings.google_allowed_emails = "old@example.test, other@example.test"
    api.c.settings.google_owner_email = " Chief@Example.test "
    assert api.c.google_auth.bootstrap_email == "chief@example.test"
    # Being on the old list means nothing any more.
    assert (await sign_in(api, "old-subject", "old@example.test"))["status"] == "pending"
    assert (await sign_in(api, "chief-subject", "chief@example.test"))["profile"]["id"] == api.profile_id
    api.c.settings.google_owner_email = ""
    assert api.c.google_auth.bootstrap_email == "old@example.test"
    api.c.settings.google_allowed_emails = ""
    assert api.c.google_auth.bootstrap_email == "" and api.c.google_auth.enabled


@respx.mock
async def test_unknown_user_waits_then_the_same_browser_becomes_active(api: Api) -> None:
    configure(api)
    api.c.app_settings._cache["notify_webhook"] = "https://hooks.example.test/notify/abc"
    hook = respx.post("https://hooks.example.test/notify/abc").mock(return_value=httpx.Response(204))
    body = await sign_in(api, "guest-subject", "Guest@Example.test", name="Guest Person")
    assert set(body) == {"status", "pending_token", "user"} and body["status"] == "pending"
    assert body["user"] == {"display_name": "Guest Person", "email": "guest@example.test"}
    pending = bearer(body["pending_token"])
    guest = (await users(api))["guest-subject"]
    assert (guest.status, guest.role_id, guest.kind, guest.display_name) == ("pending", None, "google", "Guest Person")

    # The owner is told three ways: audit log, a notice, and the webhook.
    assert "user.signup" in await audit_actions(api)
    notices = (await api.get("/notices")).json()["notices"]
    assert [(n["kind"], n["ref_id"]) for n in notices] == [("user_pending", guest.id)]
    assert "Guest Person" in notices[0]["title"]
    assert hook.call_count == 1
    sent = hook.calls.last.request
    assert b'"event":"user.pending"' in sent.content.replace(b" ", b"") and guest.id.encode() in sent.content

    # Waiting: /auth/me and nothing else.
    for path in ("/settings", "/home", "/profiles"):
        r = await api.client.get("/api/v1" + path, headers=pending)
        assert r.status_code == 403 and r.json()["code"] == "approval_pending", path
    waiting = (await api.client.get("/api/v1/auth/me", headers=pending)).json()
    assert waiting["status"] == "pending" and waiting["tokens"] is None and waiting["permissions"] == []
    assert [u["id"] for u in (await api.get("/users", params={"status": "pending"})).json()] == [guest.id]

    # Approved: the very next poll hands the waiting browser its tokens. Once.
    assert (await api.post(f"/users/{guest.id}/approve", json={"role_id": "friend"})).status_code == 200
    upgraded = await api.client.get("/api/v1/auth/me", headers=pending)
    assert upgraded.status_code == 200, upgraded.text
    me = upgraded.json()
    assert me["status"] == "active" and me["role"]["id"] == "friend" and "rooms.create" in me["permissions"]
    tokens = me["tokens"]
    assert tokens["profile"]["name"] == "Guest Person" and not tokens["profile"]["is_admin"]
    assert (await api.client.get("/api/v1/home", headers=bearer(tokens["access_token"]))).status_code == 200
    replay = await api.client.get("/api/v1/auth/me", headers=pending)
    assert replay.status_code == 401 and replay.json()["code"] == "session_revoked"
    refreshed = await api.client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    # Signing in again later needs no approval.
    assert (await sign_in(api, "guest-subject", "guest@example.test"))["status"] == "active"
    assert hook.call_count == 1


@respx.mock
async def test_denied_user_is_gone_and_their_pending_token_dies(api: Api) -> None:
    configure(api)
    body = await sign_in(api, "guest-subject", "guest@example.test")
    guest = (await users(api))["guest-subject"]
    assert (await api.post(f"/users/{guest.id}/deny")).status_code == 204
    r = await api.client.get("/api/v1/auth/me", headers=bearer(body["pending_token"]))
    assert r.status_code == 401
    assert "guest-subject" not in await users(api)


@respx.mock
async def test_invite_only_mode_refuses_strangers_without_creating_anyone(api: Api) -> None:
    configure(api)
    assert (await api.patch("/settings", json={"signup_mode": "invite_only"})).json()["signup_mode"] == "invite_only"
    assert (await api.client.get("/api/v1/auth/config")).json()["signup_mode"] == "invite_only"
    before = set(await users(api))
    callback = await google(api, "stranger-subject", "stranger@example.test")
    assert callback.headers["location"] == "/?google_error=invite_required"
    assert set(await users(api)) == before and not api.c.google_auth.handoffs
    assert (await complete(api)).status_code == 401
    # The owner still gets in, and a bad mode is refused.
    assert (await sign_in(api, "owner-subject", "owner@example.test"))["status"] == "active"
    assert (await api.patch("/settings", json={"signup_mode": "open"})).status_code == 422
    assert (await api.patch("/settings", json={"signup_mode": "approval"})).json()["signup_mode"] == "approval"


@respx.mock
async def test_suspended_google_user_cannot_sign_in(api: Api) -> None:
    configure(api)
    api.c.app_settings._cache["signup_mode"] = "invite_only"
    invite = (await api.post("/invites", json={"role_id": "friend"})).json()["url"].rsplit("/", 1)[1]
    assert (await sign_in(api, "guest-subject", "guest@example.test", invite=invite))["status"] == "active"
    guest = (await users(api))["guest-subject"]
    assert (await api.post(f"/users/{guest.id}/suspend")).status_code == 200
    callback = await google(api, "guest-subject", "guest@example.test")
    assert callback.headers["location"] == "/?google_error=user_suspended"
    assert not api.c.google_auth.handoffs
    assert (await api.post(f"/users/{guest.id}/reinstate")).status_code == 200
    assert (await sign_in(api, "guest-subject", "guest@example.test"))["status"] == "active"


@respx.mock
async def test_unverified_address_never_becomes_a_user(api: Api) -> None:
    configure(api)
    before = set(await users(api))
    start = await api.client.get("/api/v1/auth/google/start")
    query = parse_qs(urlsplit(start.headers["location"]).query)
    mock_google(query["nonce"][0], sub="guest-subject", email="guest@example.test", email_verified=False)
    callback = await api.client.get(
        "/api/v1/auth/google/callback", params={"state": query["state"][0], "code": "synthetic-code"}
    )
    assert callback.headers["location"] == "/?google_error=1"
    assert set(await users(api)) == before


# ---------------------------------------------------------------- invites
@respx.mock
async def test_invite_activates_at_once_with_its_role(api: Api) -> None:
    configure(api)
    me = await owner_id(api)
    created = await api.post("/invites", json={"role_id": "friend", "expires_in_hours": 24, "max_uses": 1})
    assert created.status_code == 201 and created.headers["cache-control"] == "private, no-store"
    invite = created.json()
    token = invite["url"].removeprefix("/invite/")
    assert len(token) == 43 and invite["state"] == "open" and invite["role_name"] == "Friend"
    assert timedelta(hours=23) < _until(invite["expires_at"]) <= timedelta(hours=24)

    # Only the hash is stored, and the token never appears in the audit log or a listing.
    async with api.c.db.session() as s:
        row = await s.get(Invite, invite["id"])
        assert row is not None and row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert row.created_by_user_id == me
        logged = repr([(a.action, a.detail) for a in (await s.execute(select(AuditLog))).scalars()])
    assert token not in logged and "invite.create" in logged
    listing = await api.get("/invites")
    assert token not in listing.text and "url" not in listing.json()[0]

    body = await sign_in(api, "friend-subject", "friend@example.test", invite=token, name="A Friend")
    assert body["status"] == "active" and "access_token" in body
    friend = (await users(api))["friend-subject"]
    assert (friend.status, friend.role_id, friend.approved_by_user_id) == ("active", "friend", me)
    mine = (await api.client.get("/api/v1/auth/me", headers=bearer(body["access_token"]))).json()
    assert mine["role"]["id"] == "friend" and mine["limits"]["max_streams"] == 1
    assert "user.invite_accept" in await audit_actions(api)
    used = (await api.get("/invites")).json()[0]
    assert (used["uses"], used["state"], used["created_by_name"]) == (1, "used", "Admin")

    # Spent: the next person through the same link is an ordinary sign-up.
    assert (await sign_in(api, "late-subject", "late@example.test", invite=token))["status"] == "pending"
    api.c.app_settings._cache["signup_mode"] = "invite_only"
    callback = await google(api, "later-subject", "later@example.test", invite=token)
    assert callback.headers["location"] == "/?google_error=invite_required"


@respx.mock
async def test_invite_lets_a_waiting_user_in(api: Api) -> None:
    configure(api)
    assert (await sign_in(api, "guest-subject", "guest@example.test"))["status"] == "pending"
    token = (await api.post("/invites", json={"role_id": "kid"})).json()["url"].removeprefix("/invite/")
    body = await sign_in(api, "guest-subject", "guest@example.test", invite=token)
    assert body["status"] == "active"
    guest = (await users(api))["guest-subject"]
    assert guest.role_id == "kid"
    async with api.c.db.session() as s:
        profile = (await s.execute(select(Profile).where(Profile.user_id == guest.id))).scalar_one()
        assert profile.kids
    # An invite never changes someone who is already active.
    other = (await api.post("/invites", json={"role_id": "family"})).json()["url"].removeprefix("/invite/")
    await sign_in(api, "guest-subject", "guest@example.test", invite=other)
    assert (await users(api))["guest-subject"].role_id == "kid"
    assert (await api.get("/invites")).json()[0]["uses"] == 0


@respx.mock
async def test_dead_invites_do_not_admit_anyone(api: Api) -> None:
    configure(api)
    api.c.app_settings._cache["signup_mode"] = "invite_only"

    async def refused(token: str, subject: str) -> bool:
        callback = await google(api, subject, f"{subject}@example.test", invite=token)
        return bool(callback.headers["location"] == "/?google_error=invite_required")

    def token_of(r: httpx.Response) -> str:
        assert r.status_code == 201, r.text
        return str(r.json()["url"]).removeprefix("/invite/")

    revoked = await api.post("/invites", json={"role_id": "friend"})
    assert (await api.delete(f"/invites/{revoked.json()['id']}")).status_code == 204
    assert await refused(token_of(revoked), "a")
    assert (await api.get("/invites")).json()[0]["state"] == "revoked"

    expired = await api.post("/invites", json={"role_id": "friend"})
    async with api.c.db.session() as s:
        await s.execute(
            update(Invite).where(Invite.id == expired.json()["id"]).values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await s.commit()
    assert await refused(token_of(expired), "b")
    assert await refused("not-a-token", "c")
    assert await refused("A" * 43, "d")

    # An invite is only as good as its creator: suspended, or no longer allowed to invite.
    inviter = await member(api, "family", name="Inviter")
    theirs = token_of(await inviter.post("/invites", json={"role_id": "friend"}))
    assert (await api.post(f"/users/{inviter.id}/suspend")).status_code == 200
    assert await refused(theirs, "e")
    assert (await api.post(f"/users/{inviter.id}/reinstate")).status_code == 200
    assert (await api.patch(f"/users/{inviter.id}", json={"denials": ["users.invite"]})).status_code == 200
    assert await refused(theirs, "f")
    assert (await api.patch(f"/users/{inviter.id}", json={"denials": []})).status_code == 200
    assert (await sign_in(api, "g", "g@example.test", invite=theirs))["status"] == "active"
    assert "invite.revoke" in await audit_actions(api)


async def test_invite_roles_are_bounded_by_the_inviter(api: Api) -> None:
    family: Member = await member(api, "family")
    wide = await api.post("/roles", json={"name": "Wide", "permissions": ["watch.live", "requests.approve"]})
    tight = await api.post(
        "/roles", json={"name": "Tight", "permissions": ["watch.live"], "limits": {"max_streams": 1}}
    )
    assert wide.status_code == tight.status_code == 201

    def too_wide(r: httpx.Response) -> bool:
        return bool(r.status_code == 403 and r.json()["code"] == "invite_role_too_wide")

    # Nobody invites straight into the Owner role, the owner included.
    assert too_wide(await api.post("/invites", json={"role_id": "owner"}))
    assert too_wide(await family.post("/invites", json={"role_id": "owner"}))
    # A permission the inviter lacks makes the role wider than they are.
    assert too_wide(await family.post("/invites", json={"role_id": "wide"}))
    for role in ("family", "kid", "friend", "tight"):
        assert (await family.post("/invites", json={"role_id": role})).status_code == 201, role
    # A looser limit counts too: a one-stream user cannot invite to an unlimited role.
    friend = await member(api, "friend", name="Friend", grants=["users.invite"])
    assert too_wide(await friend.post("/invites", json={"role_id": "family"}))
    assert (await friend.post("/invites", json={"role_id": "friend"})).status_code == 201
    assert (await api.post("/invites", json={"role_id": "wide"})).status_code == 201

    assert (await family.post("/invites", json={"role_id": "nope"})).status_code == 404
    for bad in ({"expires_in_hours": 0}, {"expires_in_hours": 721}, {"max_uses": 0}, {"max_uses": 51}):
        assert (await family.post("/invites", json={"role_id": "kid", **bad})).status_code == 422, bad
    # Each inviter sees and revokes their own; a user manager sees all.
    mine = (await family.get("/invites")).json()
    assert len(mine) == 4 and {i["created_by_user_id"] for i in mine} == {family.id}
    assert len((await api.get("/invites")).json()) == 6
    theirs = (await friend.get("/invites")).json()[0]["id"]
    assert (await family.req("DELETE", f"/invites/{theirs}")).status_code == 404
    assert (await friend.req("DELETE", f"/invites/{theirs}")).status_code == 204
    assert (await api.delete(f"/invites/{mine[0]['id']}")).status_code == 204


@respx.mock
async def test_multi_use_invite_admits_exactly_that_many(api: Api) -> None:
    configure(api)
    api.c.app_settings._cache["signup_mode"] = "invite_only"
    token = (await api.post("/invites", json={"role_id": "friend", "max_uses": 2})).json()["url"].rsplit("/", 1)[1]
    assert (await sign_in(api, "one", "one@example.test", invite=token))["status"] == "active"
    assert (await sign_in(api, "two", "two@example.test", invite=token))["status"] == "active"
    callback = await google(api, "three", "three@example.test", invite=token)
    assert callback.headers["location"] == "/?google_error=invite_required"
    assert (await api.get("/invites")).json()[0]["uses"] == 2


# ---------------------------------------------------------------- one person, one profile
@respx.mock
async def test_google_mode_lists_only_the_callers_own_profile(api: Api) -> None:
    await member(api, "kid", name="Small")
    configure(api)
    assert (await api.client.get("/api/v1/profiles")).status_code == 401
    mine = (await api.get("/profiles")).json()
    assert [p["id"] for p in mine] == [api.profile_id]
    body = await sign_in(api, "guest-subject", "guest@example.test")
    r = await api.client.get("/api/v1/profiles", headers=bearer(body["pending_token"]))
    assert r.status_code == 403 and r.json()["code"] == "approval_pending"


@respx.mock
async def test_pending_notice_reaches_every_user_manager(api: Api) -> None:
    configure(api)
    manager = await member(api, "family", name="Manager", grants=["users.manage"])
    await member(api, "family", name="Bystander")
    await sign_in(api, "guest-subject", "guest@example.test")
    async with api.c.db.session() as s:
        notified = {n.profile_id for n in (await s.execute(select(Notice))).scalars()}
    assert notified == {api.profile_id, manager.profile_id}


def _until(value: str) -> timedelta:
    from datetime import datetime

    return datetime.fromisoformat(value) - utcnow()
