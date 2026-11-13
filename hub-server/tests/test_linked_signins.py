"""Linking and unlinking Google, Plex and a password on one's own account, and an owner unlinking."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import respx
from sqlalchemy import select

from app.infra.models import AuditLog, AuthSession, User
from tests.conftest import Api
from tests.people import Member, member, owner_id
from tests.plex_identity import account, complete, mock_plex, plex_sign_in
from tests.test_google_auth import configure, mock_google
from tests.test_password_signin import GOOD, person, sign_in, user_named


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def methods(api: Api, headers: dict[str, str]) -> list[str]:
    me = await api.client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    found: list[str] = me.json()["user"]["sign_in_methods"]
    return found


async def link_plex(api: Api, headers: dict[str, str], who: dict[str, Any]) -> httpx.Response:
    mock_plex(who)
    r = await api.client.post("/api/v1/auth/plex/start", json={"return_to": "/settings", "link": True}, headers=headers)
    assert r.status_code == 200, r.text
    forward = urlsplit(parse_qs(r.json()["auth_url"].partition("#?")[2])["forwardUrl"][0])
    assert forward.path == "/settings"
    return await api.client.post(
        "/api/v1/auth/plex/complete", json={"state": parse_qs(forward.query)["plex"][0]}, headers=headers
    )


async def link_google(api: Api, headers: dict[str, str], subject: str, email: str, **claims: Any) -> httpx.Response:
    r = await api.client.post("/api/v1/auth/google/link", headers=headers)
    assert r.status_code == 200, r.text
    query = parse_qs(urlsplit(r.json()["auth_url"]).query)
    mock_google(query["nonce"][0], sub=subject, email=email, **claims)
    return await api.client.get(
        "/api/v1/auth/google/callback", params={"state": query["state"][0], "code": "synthetic-code"}
    )


async def audit(api: Api) -> list[tuple[str, str | None, dict[str, Any]]]:
    async with api.c.db.session() as s:
        rows = (await s.execute(select(AuditLog).order_by(AuditLog.id))).scalars()
        return [(r.action, r.target_user_id, r.detail) for r in rows]


@respx.mock
async def test_link_plex_then_sign_in_with_it(api: Api) -> None:
    gran = await person(api)
    mine = bearer(gran["access_token"])
    assert await methods(api, mine) == ["password"]
    done = await link_plex(api, mine, account(601))
    assert done.status_code == 200 and done.json() == {"status": "linked"}, done.text
    assert await methods(api, mine) == ["plex", "password"]
    row = await user_named(api, "gran")
    assert (row.plex_account_id, row.plex_username, row.kind) == ("601", "viewer601", "password")
    assert ("user.signin_link", row.id, {"method": "plex"}) in await audit(api)
    # The same Plex account now signs in as Gran, with no second user.
    again = await plex_sign_in(api, account(601))
    assert again.json()["status"] == "active" and again.json()["profile"]["id"] == gran["profile"]["id"]
    assert len((await api.get("/users")).json()) == 2


@respx.mock
async def test_linking_needs_the_session_that_started_it(api: Api) -> None:
    gran = await person(api)
    other = await member(api, "family", "Parent")
    mock_plex(account(601))
    assert (await api.client.post("/api/v1/auth/plex/start", json={"link": True})).status_code == 401
    r = await api.client.post("/api/v1/auth/plex/start", json={"link": True}, headers=bearer(gran["access_token"]))
    state = parse_qs(urlsplit(parse_qs(r.json()["auth_url"].partition("#?")[2])["forwardUrl"][0]).query)["plex"][0]
    # Signed out, or signed in as someone else: nothing is attached and nobody is signed in.
    stranger = await complete(api.client, state)
    assert stranger.status_code == 401
    r = await api.client.post("/api/v1/auth/plex/start", json={"link": True}, headers=bearer(gran["access_token"]))
    state = parse_qs(urlsplit(parse_qs(r.json()["auth_url"].partition("#?")[2])["forwardUrl"][0]).query)["plex"][0]
    wrong = await api.client.post("/api/v1/auth/plex/complete", json={"state": state}, headers=other.headers)
    assert wrong.status_code == 403
    assert (await user_named(api, "gran")).plex_account_id is None
    async with api.c.db.session() as s:
        assert not [u for u in (await s.execute(select(User))).scalars() if u.plex_account_id]


@respx.mock
async def test_an_identity_belongs_to_one_user(api: Api) -> None:
    gran = await person(api)
    pal = await person(api, "pal", "Pal")
    assert (await link_plex(api, bearer(gran["access_token"]), account(601))).status_code == 200
    taken = await link_plex(api, bearer(pal["access_token"]), account(601))
    assert taken.status_code == 409 and taken.json()["code"] == "identity_in_use"
    assert (await user_named(api, "pal")).plex_account_id is None
    # One Plex account per user: a second needs the first unlinked.
    second = await link_plex(api, bearer(gran["access_token"]), account(602))
    assert second.status_code == 409 and second.json()["code"] == "already_linked"
    # Linking the very same one again changes nothing.
    assert (await link_plex(api, bearer(gran["access_token"]), account(601))).status_code == 200


@respx.mock
async def test_link_google_through_the_callback(api: Api) -> None:
    configure(api)
    api.c.settings.google_auth_required = False
    gran = await person(api)
    pal = await person(api, "pal", "Pal")
    mine = bearer(gran["access_token"])
    assert (await api.client.post("/api/v1/auth/google/link")).status_code == 401
    start = await api.client.post("/api/v1/auth/google/link", headers=mine)
    cookie = start.headers["set-cookie"]
    assert cookie.startswith("__Host-sh-google-state=") and "HttpOnly" in cookie and "Secure" in cookie

    back = await link_google(api, mine, "gran-subject", "gran@example.test")
    assert back.status_code == 303 and back.headers["location"] == "/settings?linked=google"
    assert "__Host-sh-google-handoff" not in back.headers.get("set-cookie", "")
    row = await user_named(api, "gran")
    assert (row.google_subject, row.email, row.kind) == ("gran-subject", "gran@example.test", "password")
    assert await methods(api, mine) == ["google", "password"]
    assert ("user.signin_link", row.id, {"method": "google"}) in await audit(api)
    # Nobody was signed in by it: there is nothing to complete.
    assert (
        await api.client.post("/api/v1/auth/google/complete", headers={"Origin": api.c.google_auth.origin})
    ).status_code == 401

    # Somebody else's Google identity is refused, as is an unverified one.
    taken = await link_google(api, bearer(pal["access_token"]), "gran-subject", "gran@example.test")
    assert taken.headers["location"] == "/settings?link_error=identity_in_use"
    unverified = await link_google(
        api, bearer(pal["access_token"]), "pal-subject", "p@example.test", email_verified=False
    )
    assert unverified.headers["location"] == "/settings?link_error=failed"
    assert (await user_named(api, "pal")).google_subject is None
    # A link state is not a sign-in state and the other way round.
    r = await api.client.post("/api/v1/auth/google/link", headers=mine)
    state = parse_qs(urlsplit(r.json()["auth_url"]).query)["state"][0]
    assert state not in api.c.google_auth.transactions and state in api.c.google_auth.link_transactions


@respx.mock
async def test_the_last_way_in_cannot_be_removed(api: Api) -> None:
    gran = await person(api)
    mine = bearer(gran["access_token"])
    r = await api.client.delete("/api/v1/auth/methods/password", headers=mine)
    assert r.status_code == 409 and r.json()["code"] == "last_method"
    assert (await api.client.delete("/api/v1/auth/methods/plex", headers=mine)).status_code == 404
    assert (await api.client.delete("/api/v1/auth/methods/pin", headers=mine)).status_code == 422
    assert (await link_plex(api, mine, account(601))).status_code == 200
    # Two now: either may go, and then the other is the last.
    r = await api.client.delete("/api/v1/auth/methods/password", headers=mine)
    assert r.status_code == 204, r.text
    row = await api.c.users.get((await api.client.get("/api/v1/auth/me", headers=mine)).json()["user"]["id"])
    assert row["sign_in_methods"] == ["plex"] and row["username"] is None
    assert (await sign_in(api, "gran")).status_code == 401
    assert (await api.client.delete("/api/v1/auth/methods/plex", headers=mine)).json()["code"] == "last_method"
    assert ("user.signin_unlink", row["id"], {"method": "password"}) in await audit(api)
    # The device that did it stays signed in.
    assert (await api.client.get("/api/v1/auth/me", headers=mine)).status_code == 200
    # A password can be linked again, under a name of their choosing.
    r = await api.client.put("/api/v1/auth/password", json={"username": "gran2", "new_password": GOOD}, headers=mine)
    assert r.status_code == 204 and await methods(api, mine) == ["plex", "password"]
    assert "user.password_set" in [a for a, _, _ in await audit(api)]


@respx.mock
async def test_an_owner_unlinks_one_and_the_person_is_signed_out(api: Api) -> None:
    gran = await person(api)
    mine = bearer(gran["access_token"])
    assert (await link_plex(api, mine, account(601))).status_code == 200
    row = await user_named(api, "gran")
    parent: Member = await member(api, "family", "Parent")
    assert (await parent.req("DELETE", f"/users/{row.id}/methods/plex")).status_code == 403
    listed = next(u for u in (await api.get("/users")).json() if u["id"] == row.id)
    assert listed["sign_in_methods"] == ["plex", "password"] and listed["plex_username"] == "viewer601"

    r = await api.delete(f"/users/{row.id}/methods/plex")
    assert r.status_code == 200 and r.json()["sign_in_methods"] == ["password"], r.text
    assert ("user.signin_unlink", row.id, {"method": "plex"}) in await audit(api)
    assert (await api.client.get("/api/v1/auth/me", headers=mine)).status_code == 401
    async with api.c.db.session() as s:
        rows = (await s.execute(select(AuthSession).where(AuthSession.user_id == row.id))).scalars().all()
        assert rows and all(x.revoked_at is not None for x in rows)
    # That Plex account is a stranger again; the password still works.
    assert (await plex_sign_in(api, account(601))).json()["status"] == "pending"
    assert (await sign_in(api, "gran")).status_code == 200
    r = await api.delete(f"/users/{row.id}/methods/password")
    assert r.status_code == 409 and r.json()["code"] == "last_method"
    assert (await api.delete(f"/users/{row.id}/methods/google")).status_code == 404
    assert (await api.delete("/users/nobody/methods/google")).status_code == 404


@respx.mock
async def test_only_an_owner_touches_an_owners_sign_in(api: Api) -> None:
    me = await owner_id(api)
    assert (await link_plex(api, api.headers, account(42))).status_code == 200
    r = await api.put("/auth/password", json={"username": "boss", "new_password": GOOD})
    assert r.status_code == 204, r.text
    manager = await member(api, "family", "Manager", grants=["users.manage"])
    r = await manager.req("DELETE", f"/users/{me}/methods/plex")
    assert r.status_code == 403 and r.json()["code"] == "owner_required"
    assert (await manager.post(f"/users/{me}/password-reset")).status_code == 403
    # Linking Plex to the owner is the way the owner signs in with Plex: still the one owner.
    again = await plex_sign_in(api, account(42))
    assert again.json()["status"] == "active" and again.json()["profile"]["is_admin"]


@respx.mock
async def test_not_from_a_shared_device(api: Api) -> None:
    gran = await person(api)
    row = await user_named(api, "gran")
    async with api.c.db.session() as s:
        await s.execute(AuthSession.__table__.update().where(AuthSession.user_id == row.id).values(shared_device=True))
        await s.commit()
    mine = bearer(gran["access_token"])
    mock_plex(account(601))
    for r in (
        await api.client.post("/api/v1/auth/plex/start", json={"link": True}, headers=mine),
        await api.client.post("/api/v1/auth/google/link", headers=mine),
        await api.client.delete("/api/v1/auth/methods/password", headers=mine),
        await api.client.put(
            "/api/v1/auth/password", json={"current_password": GOOD, "new_password": GOOD + "!"}, headers=mine
        ),
    ):
        assert r.status_code == 403 and r.json()["code"] == "shared_device", r.text
