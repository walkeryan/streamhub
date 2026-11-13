"""POST /auth/login, the household's "pick a profile" sign-in: who a tap alone lets in, and
how guessing a PIN is held back."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from sqlalchemy import select

from app.infra.models import AuditLog
from tests.conftest import ADMIN_PASSWORD, Api
from tests.people import owner_id, set_user
from tests.test_password_signin import Clock, person, user_named
from tests.test_remote_access import AWAY, from_address


async def tap(api: Api, profile_id: str, **secret: Any) -> httpx.Response:
    return await api.client.post("/api/v1/auth/login", json={"profile_id": profile_id, **secret})


async def managed(api: Api, name: str, role_id: str = "kid", **extra: Any) -> dict[str, Any]:
    r = await api.post("/users", json={"name": name, "role_id": role_id, **extra})
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


# ---------------------------------------------------------------- who a tap lets in
async def test_a_tap_alone_enters_only_a_managed_user_without_a_secret(api: Api) -> None:
    kid = await managed(api, "Kid")
    assert (await tap(api, kid["profile_id"])).status_code == 200

    # Someone with a sign-in of their own is not entered by whoever is on the Wi-Fi.
    await person(api)
    gran = await user_named(api, "gran")
    gran_profile = (await api.c.users.get(gran.id))["profile_id"]
    google = await managed(api, "Google Person", "family")
    await set_user(api, google["id"], kind="google", google_subject="subject-1", email="g@example.test")
    plex = await managed(api, "Plex Person", "family")
    await set_user(api, plex["id"], kind="plex", plex_account_id="77")
    unknown = await tap(api, "no-such-profile")
    assert unknown.status_code == 401
    for profile_id in (gran_profile, google["profile_id"], plex["profile_id"]):
        r = await tap(api, profile_id)
        assert (r.status_code, r.text) == (unknown.status_code, unknown.text), profile_id
        assert (await tap(api, profile_id, pin="0000")).status_code == 401
    # With a PIN of their own they are entered by that PIN, as on any shared screen.
    assert (await api.put(f"/users/{gran.id}/pin", json={"pin": "4821"})).status_code == 204
    assert (await tap(api, gran_profile)).status_code == 401
    assert (await tap(api, gran_profile, pin="4821")).status_code == 200


async def test_a_tap_alone_never_enters_someone_who_manages_the_server(api: Api) -> None:
    for role_id, grants in (("owner", []), ("family", ["users.manage"]), ("family", ["sources.manage"])):
        boss = await managed(api, "Boss", role_id)
        if grants:
            assert (await api.patch(f"/users/{boss['id']}", json={"grants": grants})).status_code == 200
        assert (await tap(api, boss["profile_id"])).status_code == 401, (role_id, grants)
        assert (await api.put(f"/users/{boss['id']}/pin", json={"pin": "7391"})).status_code == 204
        assert (await tap(api, boss["profile_id"], pin="7391")).status_code == 200
    # The bootstrap owner: the password, or the PIN where one is set. Never nothing.
    assert (await tap(api, api.profile_id)).status_code == 401
    assert (await tap(api, api.profile_id, password="")).status_code == 401
    assert (await tap(api, api.profile_id, password=ADMIN_PASSWORD)).status_code == 200


async def test_the_owners_pin_stands_in_for_the_password_only_at_home(api: Api) -> None:
    me = await owner_id(api)
    assert (await api.put(f"/users/{me}/pin", json={"pin": "5309"})).status_code == 204
    api.headers = {}
    assert (await tap(api, api.profile_id, pin="5309")).status_code == 200
    async with from_address(api, AWAY) as client:
        for secret in ({"pin": "5309"}, {"password": ADMIN_PASSWORD}):
            r = await client.post("/api/v1/auth/login", json={"profile_id": api.profile_id, **secret})
            assert r.status_code == 403 and r.json()["code"] == "home_network_only"


# ---------------------------------------------------------------- guessing
async def test_wrong_pins_lock_the_profile_and_the_lock_is_recorded(api: Api) -> None:
    clock = Clock()
    api.c.auth.throttle.clock = clock
    kid = await managed(api, "Kid", pin="2468")
    allowed = api.c.auth.throttle.limits["p"][0]
    for n in range(allowed):
        assert (await tap(api, kid["profile_id"], pin=f"{n:04d}")).status_code == 401
    locked = await tap(api, kid["profile_id"], pin="2468")
    assert locked.status_code == 429 and locked.json()["code"] == "rate_limited"
    assert locked.headers["retry-after"] == "60"
    async with api.c.db.session() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.action == "auth.lockout"))).scalars().all()
    assert [(r.target_user_id, r.detail["scope"]) for r in rows] == [(kid["id"], "profile")]
    clock.now += 61
    assert (await tap(api, kid["profile_id"], pin="2468")).status_code == 200
    # A right answer clears the count: the next slip is the first again.
    for n in range(allowed - 1):
        assert (await tap(api, kid["profile_id"], pin=f"{n:04d}")).status_code == 401
    assert (await tap(api, kid["profile_id"], pin="2468")).status_code == 200


async def test_a_burst_sent_at_once_is_counted_before_any_of_it_is_checked(api: Api) -> None:
    kid = await managed(api, "Kid", pin="2468")
    allowed = api.c.auth.throttle.limits["p"][0]
    guesses = [tap(api, kid["profile_id"], pin=f"{n:04d}") for n in range(allowed + 15)]
    seen = [r.status_code for r in await asyncio.gather(*guesses)]
    assert seen.count(401) == allowed and seen.count(429) == 15, seen
    assert (await tap(api, kid["profile_id"], pin="2468")).status_code == 429


async def test_one_address_guessing_across_profiles_is_stopped(api: Api) -> None:
    per_address = api.c.auth.throttle.limits["a"][0]
    profiles = [(await managed(api, f"Kid {n}", pin="2468"))["profile_id"] for n in range(4)]
    seen = [(await tap(api, profiles[n % 4], pin="0000")).status_code for n in range(per_address + 1)]
    assert seen[:per_address] == [401] * per_address and seen[-1] == 429
    # Unknown profiles count the same, and say the same.
    assert (await tap(api, "no-such-profile", pin="0000")).status_code == 429
