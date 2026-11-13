"""A PIN is what stands between a signed-in device and its user's protected things. Whoever holds
the device must not be able to replace it without knowing it."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import update

from app.infra.models import AuthSession, utcnow
from app.services.users import FRESH_SIGN_IN_S
from tests.conftest import Api
from tests.people import Member, member, sign_in
from tests.test_password_signin import GOOD, person, user_named


async def age_sessions(api: Api, seconds: float = FRESH_SIGN_IN_S + 60) -> None:
    """Make every session look as if its sign-in was a while ago."""
    async with api.c.db.session() as s:
        await s.execute(update(AuthSession).values(created_at=utcnow() - timedelta(seconds=seconds)))
        await s.commit()


def refused(r: Any, code: str) -> bool:
    return bool(r.status_code == 403 and r.json()["code"] == code)


async def test_changing_ones_own_pin_needs_the_current_one(api: Api) -> None:
    kid: Member = await member(api, "family", "Teen")
    path = f"/users/{kid.id}/pin"
    # A first PIN needs nothing: there is nothing to know yet.
    await age_sessions(api)
    assert (await kid.req("PUT", path, json={"pin": "2468"})).status_code == 204
    # From then on the device alone is not enough, to change it or to take it off.
    for body in ({"pin": "1111"}, {"pin": None}):
        assert refused(await kid.req("PUT", path, json=body), "current_pin_required"), body
        assert refused(await kid.req("PUT", path, json={**body, "current_pin": "0000"}), "pin_invalid"), body
    assert (await kid.req("PUT", path, json={"pin": "1357", "current_pin": "2468"})).status_code == 204
    assert refused(await kid.req("PUT", path, json={"pin": "9999", "current_pin": "2468"}), "pin_invalid")
    assert (await kid.req("PUT", path, json={"pin": None, "current_pin": "1357"})).status_code == 204


async def test_the_older_profile_route_asks_the_same(api: Api) -> None:
    """PUT /profiles/{id}/pin signs the profile's devices out when it changes the PIN, as it always did."""
    created = await api.post("/users", json={"name": "Teen", "role_id": "family", "pin": "2468"})
    user = created.json()
    path = f"/profiles/{user['profile_id']}/pin"
    kid = await sign_in(api, user, "2468")
    await age_sessions(api)
    assert refused(await kid.req("PUT", path, json={"pin": "1111"}), "current_pin_required")
    assert refused(await kid.req("PUT", path, json={"pin": None}), "current_pin_required")
    assert refused(await kid.req("PUT", path, json={"pin": "1111", "current_pin": "0000"}), "pin_invalid")
    assert (await kid.req("PUT", path, json={"pin": "1357", "current_pin": "2468"})).status_code == 204
    assert (await kid.get("/auth/me")).status_code == 401
    assert (await sign_in(api, user, "1357")).id == user["id"]


async def test_wrong_guesses_at_the_current_pin_run_out(api: Api) -> None:
    kid = await member(api, "family", "Teen")
    path = f"/users/{kid.id}/pin"
    await age_sessions(api)
    assert (await kid.req("PUT", path, json={"pin": "2468"})).status_code == 204
    seen = [(await kid.req("PUT", path, json={"pin": "1111", "current_pin": f"{n:04d}"})).status_code for n in range(7)]
    assert seen[:5] == [403] * 5 and seen[5:] == [429, 429]
    assert (await kid.req("PUT", path, json={"pin": "1111", "current_pin": "2468"})).status_code == 429


async def test_a_fresh_sign_in_or_the_account_password_stands_in_for_a_forgotten_pin(api: Api) -> None:
    gran = await person(api)
    row = await user_named(api, "gran")
    mine = {"Authorization": f"Bearer {gran['access_token']}"}
    path = f"/api/v1/users/{row.id}/pin"
    assert (await api.client.put(path, json={"pin": "2468"}, headers=mine)).status_code == 204
    # Just signed in: that proves who this is as well as the PIN would.
    assert (await api.client.put(path, json={"pin": "1357"}, headers=mine)).status_code == 204
    await age_sessions(api)
    assert refused(await api.client.put(path, json={"pin": "1111"}, headers=mine), "current_pin_required")
    wrong = await api.client.put(path, json={"pin": "1111", "current_password": "not it"}, headers=mine)
    assert refused(wrong, "wrong_password")
    assert (await api.client.put(path, json={"pin": "1111", "current_password": GOOD}, headers=mine)).status_code == 204


async def test_a_manager_still_sets_a_childs_pin_but_not_their_own_blind(api: Api) -> None:
    kid = await member(api, "kid", "Kid", pin="2468")
    await age_sessions(api)
    assert (await api.put(f"/users/{kid.id}/pin", json={"pin": "4321"})).status_code == 204
    assert (await api.put(f"/users/{kid.id}/pin", json={"pin": None})).status_code == 204
    me = (await api.get("/auth/me")).json()["user"]["id"]
    assert (await api.put(f"/users/{me}/pin", json={"pin": "5309"})).status_code == 204
    assert refused(await api.put(f"/users/{me}/pin", json={"pin": "0000"}), "current_pin_required")
    assert (await api.put(f"/users/{me}/pin", json={"pin": "0000", "current_pin": "5309"})).status_code == 204
