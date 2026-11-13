"""Username and password: setup links, sign-in, the throttle, change and reset."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.infra.models import AuditLog, AuthSession, Invite, User
from app.services.errors import ServiceError
from app.services.passwords import (
    COMMON_PASSWORDS,
    LoginThrottle,
    clean_username,
    validate_password,
)
from app.services.sessions import RateLimited
from tests.conftest import Api
from tests.people import audit_actions, member, owner_id

GOOD = "correct horse battery"


class Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(api: Api) -> Clock:
    c = Clock()
    api.c.passwords.throttle.clock = c
    return c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def add_person(api: Api, name: str = "Gran", role_id: str = "friend") -> str:
    r = await api.post("/invites", json={"role_id": role_id, "name": name})
    assert r.status_code == 201, r.text
    assert r.json()["display_name"] == name
    return str(r.json()["url"]).removeprefix("/invite/")


async def accept(api: Api, token: str, username: str, password: str = GOOD) -> httpx.Response:
    return await api.client.post(
        "/api/v1/auth/invite/accept", json={"token": token, "username": username, "password": password}
    )


async def sign_in(api: Api, username: str, password: str = GOOD, **kw: Any) -> httpx.Response:
    return await api.client.post("/api/v1/auth/password", json={"username": username, "password": password}, **kw)


async def person(api: Api, username: str = "gran", name: str = "Gran", role_id: str = "friend") -> dict[str, Any]:
    r = await accept(api, await add_person(api, name, role_id), username)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def user_named(api: Api, username: str) -> User:
    async with api.c.db.session() as s:
        return (await s.execute(select(User).where(User.username == username))).scalar_one()


# ---------------------------------------------------------------- the rules
def test_username_rules() -> None:
    assert clean_username("  Gran.Smith-1_ ") == "gran.smith-1_"
    for bad in ("ab", "a" * 33, "gran smith", "gran@home", "grän", "", "gran/1", "a\nb"):
        with pytest.raises(ServiceError) as refused:
            clean_username(bad)
        assert refused.value.code == "invalid_username"


def test_password_rules() -> None:
    validate_password(GOOD, "gran")
    assert 150 <= len(COMMON_PASSWORDS) <= 400 and all(p == p.lower() for p in COMMON_PASSWORDS)
    for bad in ("short", "123456789", "x" * 257, "Password123", "QWERTYUIOP", "1234567890", "my-GRAN-secret"):
        with pytest.raises(ServiceError) as refused:
            validate_password(bad, "gran")
        assert refused.value.code == "weak_password", bad


# ---------------------------------------------------------------- setup links
async def test_a_setup_link_makes_a_person_who_can_sign_in(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    me = await owner_id(api)
    token = await add_person(api, "Gran", "friend")
    look = await api.client.post("/api/v1/auth/invite/lookup", json={"token": token})
    assert look.status_code == 200 and look.headers["cache-control"] == "no-store"
    assert look.json() == {"valid": True, "display_name": "Gran", "username": None, "setup": False}
    assert (await api.client.get("/api/v1/auth/config")).json()["password_enabled"] is True

    done = await accept(api, token, " Gran ")
    assert done.status_code == 200 and done.headers["cache-control"] == "no-store", done.text
    body = done.json()
    assert body["profile"]["name"] == "Gran" and not body["profile"]["is_admin"]
    gran = await user_named(api, "gran")
    assert (gran.kind, gran.status, gran.role_id, gran.approved_by_user_id) == ("password", "active", "friend", me)
    assert gran.password_hash and gran.password_hash.startswith("scrypt$") and GOOD not in gran.password_hash
    mine = (await api.client.get("/api/v1/auth/me", headers=bearer(body["access_token"]))).json()
    assert mine["user"]["sign_in_methods"] == ["password"] and mine["user"]["username"] == "gran"
    assert mine["role"]["id"] == "friend"
    assert "user.invite_accept" in await audit_actions(api)

    # Used once: nobody else comes in through it, and a look says only that it is no good.
    again = await accept(api, token, "someone")
    assert again.status_code == 403 and again.json()["code"] == "invite_invalid"
    look = await api.client.post("/api/v1/auth/invite/lookup", json={"token": token})
    assert look.json() == {"valid": False, "display_name": None, "username": None, "setup": False}

    r = await sign_in(api, "GRAN")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json()["profile"]["id"] == body["profile"]["id"]
    # Nothing secret reached the log or the audit trail.
    async with api.c.db.session() as s:
        trail = repr([a.detail for a in (await s.execute(select(AuditLog))).scalars()])
    for secret in (GOOD, token):
        assert secret not in caplog.text and secret not in trail


async def test_a_refused_choice_does_not_spend_the_link(api: Api) -> None:
    await person(api, "taken")
    token = await add_person(api, "Next")
    for username, password, status, code in (
        ("ab", GOOD, 400, "invalid_username"),
        ("next", "short", 400, "weak_password"),
        ("next", "next-is-my-name", 400, "weak_password"),
        ("next", "password1234", 400, "weak_password"),
        ("taken", GOOD, 409, "username_taken"),
    ):
        r = await accept(api, token, username, password)
        assert (r.status_code, r.json()["code"]) == (status, code), (username, password)
    async with api.c.db.session() as s:
        assert [i.uses for i in (await s.execute(select(Invite).order_by(Invite.created_at))).scalars()] == [1, 0]
    assert (await accept(api, token, "next")).status_code == 200


async def test_dead_links_all_answer_alike(api: Api) -> None:
    revoked = await api.post("/invites", json={"role_id": "friend", "name": "Gone"})
    assert (await api.delete(f"/invites/{revoked.json()['id']}")).status_code == 204
    dead = [revoked.json()["url"].removeprefix("/invite/"), "A" * 43, "nonsense", ""]
    answers = set()
    for token in dead:
        look = await api.client.post("/api/v1/auth/invite/lookup", json={"token": token})
        r = await accept(api, token, "someone")
        answers.add((look.status_code, look.text, r.status_code, r.text))
    assert len(answers) == 1 and next(iter(answers))[2] == 403


async def test_an_invite_role_is_still_bounded_by_its_creator(api: Api) -> None:
    parent = await member(api, "family", "Parent")
    assert (await parent.post("/invites", json={"role_id": "owner", "name": "Me"})).status_code == 403
    token = (await parent.post("/invites", json={"role_id": "friend", "name": "Pal"})).json()["url"][8:]
    assert (await api.post(f"/users/{parent.id}/suspend")).status_code == 200
    assert (await accept(api, token, "pal")).status_code == 403


async def test_the_method_can_be_switched_off(api: Api) -> None:
    await person(api)
    token = await add_person(api, "Late")
    assert (await api.patch("/settings", json={"password_sign_in": False})).json()["password_sign_in"] is False
    assert (await api.client.get("/api/v1/auth/config")).json()["password_enabled"] is False
    for r in (await sign_in(api, "gran"), await accept(api, token, "late")):
        assert r.status_code == 403 and r.json()["code"] == "password_disabled"


# ---------------------------------------------------------------- sign-in
async def test_wrong_name_and_wrong_password_are_one_answer(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    await person(api)
    import app.services.passwords as passwords

    verified: list[str] = []
    real = passwords.verify_secret

    def spy(value: str, stored: str | None) -> bool:
        verified.append(stored or "")
        return real(value, stored)

    monkeypatch.setattr(passwords, "verify_secret", spy)
    wrong = await sign_in(api, "gran", "not the password")
    unknown = await sign_in(api, "nobody", "not the password")
    blank = await sign_in(api, "", "")
    odd = await sign_in(api, "x" * 64, "y" * 256)
    answers = {(r.status_code, r.text, r.headers.get("www-authenticate")) for r in (wrong, unknown, blank, odd)}
    assert len(answers) == 1 and wrong.status_code == 401 and wrong.json()["code"] == "invalid_credentials"
    # The same work each time: one real scrypt verification, against a stand-in when there is no user.
    assert len(verified) == 4 and all(v.startswith("scrypt$") for v in verified)
    assert len(set(verified)) == 2

    # As far as practical the same time, too: an unknown name is not answered early.
    async def timed(username: str) -> float:
        started = time.perf_counter()
        await sign_in(api, username, "not the password")
        return time.perf_counter() - started

    known = min([await timed("gran") for _ in range(2)])
    stranger = min([await timed("ghost") for _ in range(2)])
    assert stranger > known * 0.5 and known > stranger * 0.5


async def test_a_user_without_a_password_cannot_be_guessed_into(api: Api) -> None:
    """A managed user, or one whose password was reset, has no hash: nothing verifies."""
    kid = await member(api, "kid", "Kid")
    async with api.c.db.session() as s:
        row = await s.get(User, kid.id)
        assert row is not None
        row.username = "kid"
        await s.commit()
    for guess in ("", "anything at all", "None"):
        assert (await sign_in(api, "kid", guess)).status_code in (401, 422)


async def test_suspended_people_are_told_only_after_the_right_password(api: Api) -> None:
    await person(api)
    gran = await user_named(api, "gran")
    assert (await api.post(f"/users/{gran.id}/suspend")).status_code == 200
    assert (await sign_in(api, "gran", "a wrong password")).json()["code"] == "invalid_credentials"
    r = await sign_in(api, "gran")
    assert r.status_code == 401 and r.json()["code"] == "user_suspended"


# ---------------------------------------------------------------- the throttle
def test_throttle_locks_a_key_for_longer_each_time() -> None:
    clock = Clock()
    t = LoginThrottle(clock=clock)

    def burst(key: str) -> int | None:
        locked = None
        for _ in range(t.limits[key[0]][0]):
            ticket = t.begin(key)
            locked = t.fail(ticket).get(key) or locked
        return locked

    assert burst("u:gran") == 60
    with pytest.raises(RateLimited) as refused:
        t.begin("u:gran")
    assert refused.value.retry_after == 60
    clock.now += 59
    with pytest.raises(RateLimited) as refused:
        t.begin("u:gran")
    assert refused.value.retry_after == 1
    clock.now += 1
    assert burst("u:gran") == 120
    clock.now += 120
    assert burst("u:gran") == 240
    # Another name is untouched; the lock never grows past an hour.
    t.succeed(t.begin("u:other"))
    for _ in range(12):
        clock.now += 3600
        last = burst("u:gran")
    assert last == 3600
    # A day of quiet forgets the history.
    clock.now += 24 * 3600 + 1
    assert burst("u:gran") == 60


def test_throttle_counts_attempts_before_they_are_judged() -> None:
    """A burst sent at once cannot all be in flight: the sixth is refused before any has failed."""
    t = LoginThrottle(clock=Clock())
    tickets = [t.begin("u:gran", "a:203.0.113.9") for _ in range(5)]
    with pytest.raises(RateLimited):
        t.begin("u:gran", "a:203.0.113.9")
    # A success gives its attempt back and clears the name's record.
    t.succeed(tickets[0])
    t.succeed(t.begin("u:gran", "a:203.0.113.9"))


def test_throttle_has_a_ceiling_for_everyone() -> None:
    clock = Clock()
    t = LoginThrottle(clock=clock)
    for n in range(t.ceiling[0]):
        t.fail(t.begin(f"u:name{n}", f"a:198.51.100.{n % 250}"))
    with pytest.raises(RateLimited) as refused:
        t.begin("u:innocent", "a:192.0.2.1")
    assert 1 <= refused.value.retry_after <= t.ceiling[1]
    clock.now += t.ceiling[1] + 1
    t.succeed(t.begin("u:innocent", "a:192.0.2.1"))


async def test_lockout_by_name_and_by_address_over_http(api: Api, clock: Clock) -> None:
    await person(api)
    gran = await user_named(api, "gran")
    per_name = api.c.passwords.throttle.limits["u"][0]
    for _ in range(per_name):
        assert (await sign_in(api, "gran", "a wrong password")).status_code == 401
    locked = await sign_in(api, "gran")  # the right password does not help now
    assert locked.status_code == 429 and locked.json()["code"] == "rate_limited"
    assert locked.headers["retry-after"] == "60"
    # An unknown name locks exactly the same way.
    for _ in range(per_name):
        assert (await sign_in(api, "ghost", "a wrong password")).status_code == 401
    ghost = await sign_in(api, "ghost", "a wrong password")
    assert (ghost.status_code, ghost.text) == (locked.status_code, locked.text)
    async with api.c.db.session() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.action == "auth.lockout"))).scalars().all()
    assert [(r.target_user_id, r.detail["scope"]) for r in rows] == [(gran.id, "username"), (None, "username")]
    assert "ghost" not in repr([r.detail for r in rows])

    clock.now += 61
    assert (await sign_in(api, "gran")).status_code == 200

    # One address trying many names is stopped whatever the names are.
    per_address = api.c.passwords.throttle.limits["a"][0]
    seen = []
    for n in range(per_address + 1):
        seen.append((await sign_in(api, f"name{n}", "a wrong password")).status_code)
    assert seen[-1] == 429 and set(seen[:-1]) <= {401, 429} and seen.count(401) <= per_address
    assert (await sign_in(api, "gran")).status_code == 429
    async with api.c.db.session() as s:
        scopes = [
            r.detail["scope"] for r in (await s.execute(select(AuditLog))).scalars() if r.action == "auth.lockout"
        ]
    assert "address" in scopes


# ---------------------------------------------------------------- change and reset
async def test_change_password_needs_the_current_one_and_signs_other_devices_out(api: Api) -> None:
    first = await person(api)
    second = (await sign_in(api, "gran")).json()
    mine = bearer(first["access_token"])
    for body, status, code in (
        ({"new_password": "another good phrase"}, 403, "wrong_password"),
        ({"current_password": "not it", "new_password": "another good phrase"}, 403, "wrong_password"),
        ({"current_password": GOOD, "new_password": "short"}, 400, "weak_password"),
        ({"current_password": GOOD, "new_password": "i am gran okay"}, 400, "weak_password"),
    ):
        r = await api.client.put("/api/v1/auth/password", json=body, headers=mine)
        assert (r.status_code, r.json()["code"]) == (status, code), body
    r = await api.client.put(
        "/api/v1/auth/password", json={"current_password": GOOD, "new_password": "another good phrase"}, headers=mine
    )
    assert r.status_code == 204, r.text
    assert "user.password_change" in await audit_actions(api)
    # This device stays signed in; the other one is out.
    assert (await api.client.get("/api/v1/auth/me", headers=mine)).status_code == 200
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(second["access_token"]))).status_code == 401
    assert (
        await api.client.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    ).status_code == 401
    assert (await sign_in(api, "gran")).status_code == 401
    assert (await sign_in(api, "gran", "another good phrase")).status_code == 200
    assert (await api.client.put("/api/v1/auth/password", json={"new_password": "x" * 12})).status_code == 401


async def test_owner_reset_issues_a_setup_link_and_cuts_everything_else(api: Api) -> None:
    first = await person(api)
    gran = await user_named(api, "gran")
    parent = await member(api, "family", "Parent")
    assert (await parent.post(f"/users/{gran.id}/password-reset")).status_code == 403
    assert (await api.client.post(f"/api/v1/users/{gran.id}/password-reset")).status_code == 401

    r = await api.post(f"/users/{gran.id}/password-reset")
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store", r.text
    token = r.json()["url"].removeprefix("/invite/")
    assert len(token) == 43 and r.json()["expires_at"]
    assert "user.password_reset" in await audit_actions(api)
    # The old password and every session are gone at once.
    assert (await sign_in(api, "gran")).status_code == 401
    assert (await api.client.get("/api/v1/auth/me", headers=bearer(first["access_token"]))).status_code == 401
    assert (await user_named(api, "gran")).password_hash is None
    async with api.c.db.session() as s:
        live = (await s.execute(select(AuthSession).where(AuthSession.user_id == gran.id))).scalars().all()
        assert live and all(row.revoked_at is not None for row in live)

    look = (await api.client.post("/api/v1/auth/invite/lookup", json={"token": token})).json()
    assert look == {"valid": True, "display_name": "Gran", "username": "gran", "setup": True}
    # It sets that one person's password: it is not an invitation, and it cannot rename them.
    done = await accept(api, token, "someone-else", "a brand new phrase")
    assert done.status_code == 200 and done.json()["profile"]["id"] == first["profile"]["id"]
    assert (await user_named(api, "gran")).id == gran.id
    assert (await sign_in(api, "gran", "a brand new phrase")).status_code == 200
    assert (await sign_in(api, "someone-else", "a brand new phrase")).status_code == 401
    assert (await accept(api, token, "gran", "yet another phrase")).status_code == 403
    # A second reset withdraws the first link.
    one = (await api.post(f"/users/{gran.id}/password-reset")).json()["url"].removeprefix("/invite/")
    two = (await api.post(f"/users/{gran.id}/password-reset")).json()["url"].removeprefix("/invite/")
    assert (await accept(api, one, "gran", "yet another phrase")).status_code == 403
    assert (await accept(api, two, "gran", "yet another phrase")).status_code == 200


async def test_reset_gives_someone_without_a_password_a_way_to_choose_one(api: Api) -> None:
    kid = await member(api, "family", "Teen")
    r = await api.post(f"/users/{kid.id}/password-reset")
    token = r.json()["url"].removeprefix("/invite/")
    look = (await api.client.post("/api/v1/auth/invite/lookup", json={"token": token})).json()
    assert look == {"valid": True, "display_name": "Teen", "username": None, "setup": True}
    assert (await accept(api, token, "teen")).status_code == 200
    teen = await user_named(api, "teen")
    assert teen.id == kid.id and teen.kind == "managed" and teen.role_id == "family"


async def test_setup_links_are_never_invitations(api: Api) -> None:
    """A setup link names one user; Google and Plex sign-ins get nothing from it."""
    from app.services.invites import InvitesService

    await person(api)
    gran = await user_named(api, "gran")
    token = (await api.post(f"/users/{gran.id}/password-reset")).json()["url"].removeprefix("/invite/")
    async with api.c.db.session() as s:
        assert await InvitesService.redeem_in(s, token) is None


async def test_no_self_registration(api: Api) -> None:
    before = len((await api.get("/users")).json())
    for path, body in (
        ("/auth/password", {"username": "newcomer", "password": GOOD}),
        ("/auth/invite/accept", {"token": "", "username": "newcomer", "password": GOOD}),
        ("/auth/invite/accept", {"username": "newcomer", "password": GOOD}),
    ):
        assert (await api.client.post("/api/v1" + path, json=body)).status_code in (401, 403)
    assert len((await api.get("/users")).json()) == before
