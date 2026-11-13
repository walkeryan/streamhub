"""Sign in with Plex: the browser PIN flow, who it maps onto, and every way it is refused."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.infra.models import AuditLog, Source, User
from tests.conftest import Api
from tests.people import audit_actions, owner_id, set_user
from tests.plex_identity import OWNER_TOKEN, PLEX_TV, USER_TOKEN, account, complete, mock_plex, plex_sign_in, start


def other_browser(api: Api) -> AsyncClient:
    """A second browser: the same server, none of the first one's cookies."""
    app = api.client._transport.app  # type: ignore[attr-defined]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://hub.test")


async def users(api: Api) -> dict[str, User]:
    async with api.c.db.session() as s:
        return {u.plex_account_id or u.id: u for u in (await s.execute(select(User))).scalars()}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def invite(api: Api, role_id: str = "friend", **extra: Any) -> str:
    r = await api.post("/invites", json={"role_id": role_id, **extra})
    assert r.status_code == 201, r.text
    return str(r.json()["url"]).removeprefix("/invite/")


@respx.mock
async def test_start_sends_the_browser_to_plex_with_a_strong_pin(api: Api) -> None:
    routes = mock_plex()
    r = await api.client.post("/api/v1/auth/plex/start", json={"return_to": "/pair?code=ABC234"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    asked = routes["pins"].calls.last.request
    assert asked.url.params["strong"] == "true"
    assert asked.headers["x-plex-client-identifier"] == api.c.adapter_ctx.client_id
    assert "x-plex-token" not in asked.headers
    state, query = await start(api.client, return_to="/pair?code=ABC234")
    assert query["clientID"] == [api.c.adapter_ctx.client_id] and query["code"][0].startswith("strong-code-")
    assert query["context[device][product]"] == [api.c.settings.plex_product]
    forward = urlsplit(query["forwardUrl"][0])
    assert (forward.scheme, forward.netloc, forward.path) == ("http", "hub.test", "/pair")
    assert forward.query == f"code=ABC234&plex={state}"
    # The PIN id never reaches the browser; the cookie that binds the flow is not readable by scripts.
    assert "4242" not in r.text and "pin" not in r.json()
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("sh-plex-state=") and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Max-Age=600" in cookie
    assert (await api.client.get("/api/v1/auth/config")).json()["plex_enabled"] is True


@respx.mock
async def test_https_gets_the_host_prefixed_secure_cookie(api: Api) -> None:
    mock_plex()
    r = await api.client.post("/api/v1/auth/plex/start", json={}, headers={"X-Forwarded-Proto": "https"})
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-sh-plex-state=") and "Secure" in cookie and "HttpOnly" in cookie


@respx.mock
@pytest.mark.parametrize(
    "attempt",
    [
        "https://evil.example.test/",
        "//evil.example.test",
        "/\\evil.example.test",
        "/pair?code=ABC234&next=//evil.example.test",
        "/settings/../../evil",
        "javascript:alert(1)",
        "/pair?code=abc234",
        "",
    ],
)
async def test_return_to_is_never_an_open_redirect(api: Api, attempt: str) -> None:
    mock_plex()
    state, query = await start(api.client, return_to=attempt)
    assert query["forwardUrl"] == [f"http://hub.test/?plex={state}"]


@respx.mock
async def test_unknown_account_waits_for_approval_then_signs_in(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    routes = mock_plex()
    state, _ = await start(api.client)
    done = await complete(api.client, state)
    assert done.status_code == 200, done.text
    body = done.json()
    assert set(body) == {"status", "pending_token", "user"} and body["status"] == "pending"
    assert body["user"] == {"display_name": "Viewer 501", "email": "viewer501@example.test"}
    # The PIN was read with its code, and the person's token was used for one call only.
    assert routes["pin"].calls.last.request.url.params["code"].startswith("strong-code-")
    assert routes["user"].call_count == 1
    guest = (await users(api))["501"]
    assert (guest.status, guest.kind, guest.role_id, guest.plex_username) == ("pending", "plex", None, "viewer501")
    assert guest.google_subject is None and guest.password_hash is None
    assert "user.signup" in await audit_actions(api)
    # Never stored, never logged.
    async with api.c.db.session() as s:
        stored = repr([vars(u) for u in (await s.execute(select(User))).scalars()])
        stored += repr([a.detail for a in (await s.execute(select(AuditLog))).scalars()])
    assert USER_TOKEN not in stored and USER_TOKEN not in caplog.text
    assert "strong-code-" not in caplog.text

    waiting = await api.client.get("/api/v1/auth/me", headers=bearer(body["pending_token"]))
    assert waiting.json()["status"] == "pending"
    assert (await api.post(f"/users/{guest.id}/approve", json={"role_id": "friend"})).status_code == 200
    me = (await api.client.get("/api/v1/auth/me", headers=bearer(body["pending_token"]))).json()
    assert me["status"] == "active" and me["user"]["sign_in_methods"] == ["plex"]
    assert me["user"]["plex_username"] == "viewer501" and me["user"]["kind"] == "plex"

    # Known now: the next sign-in is immediate, and follows the account id even when the rest changed.
    again = await plex_sign_in(api, account(username="renamed", email="moved@example.test"))
    assert again.status_code == 200 and again.json()["status"] == "active"
    assert again.json()["profile"]["name"] == "Viewer 501"
    renamed = (await users(api))["501"]
    assert (renamed.id, renamed.plex_username, renamed.email) == (guest.id, "renamed", "moved@example.test")
    assert len(await users(api)) == 2


@respx.mock
async def test_a_pin_is_bound_to_the_browser_that_started_it(api: Api) -> None:
    mock_plex()
    state, _ = await start(api.client)
    async with other_browser(api) as thief:
        r = await complete(thief, state)
        assert r.status_code == 401 and r.json()["code"] == "sign_in_expired"
        # Nor with a cookie of its own from a flow of its own.
        mine, _ = await start(thief)
        assert mine != state
    assert "501" not in await users(api)
    # The attempt spent the transaction: the PIN completes for nobody now.
    assert (await complete(api.client, state)).status_code == 401


@respx.mock
async def test_a_pin_is_single_use(api: Api) -> None:
    routes = mock_plex()
    state, _ = await start(api.client)
    assert (await complete(api.client, state)).status_code == 200
    replay = await complete(api.client, state)
    assert replay.status_code == 401 and replay.json()["code"] == "sign_in_expired"
    assert routes["pin"].call_count == 1
    for junk in ("", "x", "A" * 43, "../" * 10):
        assert (await complete(api.client, junk)).status_code == 401


@respx.mock
async def test_an_expired_pin_is_refused(api: Api) -> None:
    now = [1000.0]
    api.c.plex_auth.clock = lambda: now[0]
    routes = mock_plex()
    state, _ = await start(api.client)
    now[0] += 601
    r = await complete(api.client, state)
    assert r.status_code == 401 and r.json()["code"] == "sign_in_expired"
    assert routes["pin"].call_count == 0 and not api.c.plex_auth.transactions


@respx.mock
async def test_plex_errors_sign_nobody_in(api: Api) -> None:
    respx.post(f"{PLEX_TV}/pins").mock(return_value=httpx.Response(503))
    r = await api.client.post("/api/v1/auth/plex/start", json={})
    assert r.status_code == 502 and r.json()["code"] == "plex_unavailable" and "set-cookie" not in r.headers

    # Nobody signed in at Plex (the person closed the page): the PIN has no token.
    mock_plex(authorised=False)
    state, _ = await start(api.client)
    r = await complete(api.client, state)
    assert r.status_code == 401 and r.json()["code"] == "sign_in_expired"

    # plex.tv fails while the PIN is read, or while the account is.
    for broken in ("pin", "user"):
        routes = mock_plex()
        state, _ = await start(api.client)
        routes[broken].mock(return_value=httpx.Response(500, text=f"upstream said {USER_TOKEN}"))
        r = await complete(api.client, state)
        assert r.status_code == 502 and r.json()["code"] == "plex_unavailable", broken
        assert USER_TOKEN not in r.text
    # An answer without an account id is not an identity.
    r = await plex_sign_in(api, {"username": "nobody", "email": "nobody@example.test"})
    assert r.status_code == 502
    assert len(await users(api)) == 1


@respx.mock
async def test_invite_only_refuses_strangers_and_an_invite_is_used_once(api: Api) -> None:
    me = await owner_id(api)
    assert (await api.patch("/settings", json={"signup_mode": "invite_only"})).status_code == 200
    r = await plex_sign_in(api)
    assert r.status_code == 403 and r.json()["code"] == "invite_required"
    assert len(await users(api)) == 1

    token = await invite(api, "friend")
    r = await plex_sign_in(api, invite=token)
    assert r.status_code == 200 and r.json()["status"] == "active", r.text
    friend = (await users(api))["501"]
    assert (friend.status, friend.role_id, friend.approved_by_user_id) == ("active", "friend", me)
    assert "user.invite_accept" in await audit_actions(api)
    # The token rode the server-side transaction: it was never sent to Plex.
    mock_plex(account(502))
    _, query = await start(api.client, invite=token)
    assert token not in repr(query)
    # Spent: a second person through the same link is refused.
    r = await plex_sign_in(api, account(503), invite=token)
    assert r.status_code == 403 and r.json()["code"] == "invite_required"
    assert "503" not in await users(api)


@respx.mock
async def test_suspended_and_pending_answer_as_they_do_for_google(api: Api) -> None:
    first = (await plex_sign_in(api)).json()
    assert first["status"] == "pending"
    # Still pending on a second visit: a fresh waiting token, not a second user.
    assert (await plex_sign_in(api)).json()["status"] == "pending"
    assert len(await users(api)) == 2
    guest = (await users(api))["501"]
    assert (await api.post(f"/users/{guest.id}/approve", json={"role_id": "friend"})).status_code == 200
    assert (await api.post(f"/users/{guest.id}/suspend")).status_code == 200
    r = await plex_sign_in(api)
    assert r.status_code == 403 and r.json()["code"] == "user_suspended"


@respx.mock
async def test_a_matching_address_is_never_merged_into_another_user(api: Api) -> None:
    """Someone else's Google address on a Plex account proves nothing about who holds it."""
    me = await owner_id(api)
    await set_user(api, me, kind="google", google_subject="owner-subject", email="owner@example.test")
    r = await plex_sign_in(api, account(777, email="owner@example.test", confirmed=True))
    assert r.status_code == 200 and r.json()["status"] == "pending"
    everyone = await users(api)
    stranger = everyone["777"]
    assert stranger.id != me and stranger.role_id is None and stranger.google_subject is None
    owner = next(u for u in everyone.values() if u.id == me)
    assert owner.plex_account_id is None and owner.role_id == "owner"
    # Refused outright where strangers need an invitation.
    assert (await api.patch("/settings", json={"signup_mode": "invite_only"})).status_code == 200
    r = await plex_sign_in(api, account(778, email="owner@example.test"))
    assert r.status_code == 403 and "778" not in await users(api)


@respx.mock
async def test_an_unconfirmed_address_is_not_kept(api: Api) -> None:
    r = await plex_sign_in(api, account(601, email="claimed@example.test", confirmed=False))
    assert r.json()["user"] == {"display_name": "Viewer 601", "email": None}
    assert (await users(api))["601"].email is None


async def link_owner_server(api: Api) -> None:
    config = api.c.crypto.encrypt_json(
        {"server_url": "http://plex.example.test:32400", "token": "server-token", "account_token": OWNER_TOKEN}
    )
    async with api.c.db.session() as s:
        s.add(Source(id="plex", type="plex", name="Library", config_enc=config))
        await s.commit()


def mock_friends(*ids: int, status: int = 200) -> respx.Route:
    def friends(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-plex-token") != OWNER_TOKEN:
            return httpx.Response(401)
        listed = [{"id": i, "username": f"viewer{i}", "status": "accepted"} for i in ids]
        return httpx.Response(status, json=[*listed, {"id": 999, "status": "pending"}])

    return respx.get(f"{PLEX_TV}/friends").mock(side_effect=friends)


@respx.mock
async def test_friends_auto_approve_is_off_by_default(api: Api) -> None:
    await link_owner_server(api)
    friends = mock_friends(501)
    assert (await api.get("/settings")).json()["plex_friends_auto_approve"] is False
    assert (await plex_sign_in(api)).json()["status"] == "pending"
    assert friends.call_count == 0


@respx.mock
async def test_someone_the_owners_server_is_shared_with_becomes_a_friend(api: Api) -> None:
    await link_owner_server(api)
    friends = mock_friends(501, 502)
    r = await api.patch("/settings", json={"plex_friends_auto_approve": True})
    assert r.status_code == 200 and r.json()["plex_friends_auto_approve"] is True
    done = await plex_sign_in(api)
    assert done.status_code == 200 and done.json()["status"] == "active", done.text
    friend = (await users(api))["501"]
    assert (friend.status, friend.role_id) == ("active", "friend")
    assert "user.plex_friend_accept" in await audit_actions(api)
    # Someone else is a stranger still; an unaccepted invitation at Plex is not a share.
    assert (await plex_sign_in(api, account(503))).json()["status"] == "pending"
    assert (await plex_sign_in(api, account(999))).json()["status"] == "pending"
    # The list is cached briefly rather than fetched per sign-in.
    assert (await plex_sign_in(api, account(502))).json()["status"] == "active"
    assert friends.call_count == 1
    # It holds where strangers need an invitation too: the share is the owner's say-so.
    assert (await api.patch("/settings", json={"signup_mode": "invite_only"})).status_code == 200
    api.c.plex_auth._friends = None
    mock_friends(504)
    assert (await plex_sign_in(api, account(504))).json()["status"] == "active"
    assert (await plex_sign_in(api, account(505))).status_code == 403


@respx.mock
async def test_the_friends_check_fails_closed(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    await api.patch("/settings", json={"plex_friends_auto_approve": True})
    # No linked Plex server: nobody to ask.
    assert (await plex_sign_in(api, account(501))).json()["status"] == "pending"
    await link_owner_server(api)
    for n, broken in enumerate((httpx.Response(500), httpx.Response(200, json={"friends": []}), httpx.ConnectError)):
        route = respx.get(f"{PLEX_TV}/friends")
        route.mock(side_effect=broken) if n == 2 else route.mock(return_value=broken)
        assert (await plex_sign_in(api, account(510 + n))).json()["status"] == "pending", n
    assert OWNER_TOKEN not in caplog.text
    # A failure is not remembered as an empty list.
    mock_friends(520)
    assert (await plex_sign_in(api, account(520))).json()["status"] == "active"


@respx.mock
async def test_the_owners_own_plex_account_is_never_made_owner(api: Api) -> None:
    await link_owner_server(api)
    await api.patch("/settings", json={"plex_friends_auto_approve": True})
    # Even listed (it never is), the most this path confers is Friend.
    mock_friends(42)
    done = await plex_sign_in(api, account(42, username="serverowner"))
    assert done.json()["status"] == "active"
    theirs = (await users(api))["42"]
    assert theirs.role_id == "friend" and theirs.id != await owner_id(api)
    async with api.c.db.session() as s:
        owners = (await s.execute(select(User).where(User.role_id == "owner"))).scalars().all()
    assert [u.id for u in owners] == [await owner_id(api)]


@respx.mock
async def test_the_switches_belong_to_system_manage(api: Api) -> None:
    from tests.people import member

    kid = await member(api, "family", "Parent")
    for patch in ({"plex_friends_auto_approve": True}, {"plex_sign_in": False}):
        assert (await kid.req("PATCH", "/settings", json=patch)).status_code == 403
    assert (await api.patch("/settings", json={"plex_friends_auto_approve": "yes"})).status_code == 422
    assert (await api.patch("/settings", json={"plex_sign_in": False})).json()["plex_sign_in"] is False
    assert (await api.client.get("/api/v1/auth/config")).json()["plex_enabled"] is False
    r = await api.client.post("/api/v1/auth/plex/start", json={})
    assert r.status_code == 403 and r.json()["code"] == "plex_disabled"
    assert (await complete(api.client, "A" * 43)).status_code == 403


@respx.mock
async def test_completion_is_same_origin(api: Api) -> None:
    mock_plex()
    state, _ = await start(api.client)
    r = await api.client.post(
        "/api/v1/auth/plex/complete", json={"state": state}, headers={"Origin": "https://evil.example.test"}
    )
    assert r.status_code == 403
    assert api.c.plex_auth.transactions  # refused before the transaction was touched
    ok = await api.client.post(
        "/api/v1/auth/plex/complete", json={"state": state}, headers={"Origin": "http://hub.test"}
    )
    assert ok.status_code == 200
