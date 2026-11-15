"""A PIN chosen as a D-pad arrow pattern: stored as the keypad digits of its arrows (up 2, left 4,
right 6, down 8), with its length kept beside it so a TV knows how many presses to wait for."""

from __future__ import annotations

from typing import Any

from tests.conftest import Api
from tests.people import sign_in
from tests.test_shared_devices import bearer, pair, switch, user
from tests.test_users_migration import NOW, config


async def picker(api: Api, tv: dict[str, Any]) -> dict[str, dict[str, Any]]:
    r = await api.client.get("/api/v1/auth/switchable", headers=bearer(tv))
    assert r.status_code == 200, r.text
    return {u["id"]: u for u in r.json()["users"]}


async def arrows_of(api: Api, user_id: str) -> Any:
    listed = {u["id"]: u for u in (await api.get("/users")).json()}
    detail = (await api.get(f"/users/{user_id}")).json()
    assert listed[user_id]["pin_arrows"] == detail["pin_arrows"]
    return detail["pin_arrows"]


async def test_an_arrow_pattern_records_its_length_and_a_typed_pin_clears_it(api: Api) -> None:
    kid = await user(api, "Kid", "kid")
    path = f"/users/{kid['id']}/pin"
    assert await arrows_of(api, kid["id"]) is None

    # Up, Up, Down, Down, Left, Right.
    assert (await api.put(path, json={"pin": "228846", "arrows": True})).status_code == 204
    assert await arrows_of(api, kid["id"]) == 6
    tv = await pair(api, shared_device=True)
    listed = (await picker(api, tv))[kid["id"]]
    assert listed["has_pin"] is True and listed["pin_arrows"] == 6

    # The same digits typed as a PIN are an ordinary PIN: the TV goes back to the keypad.
    assert (await api.put(path, json={"pin": "228846"})).status_code == 204
    assert await arrows_of(api, kid["id"]) is None
    assert (await picker(api, tv))[kid["id"]]["pin_arrows"] is None

    # Taking the PIN off takes the pattern with it, whatever the request says.
    assert (await api.put(path, json={"pin": "2468", "arrows": True})).status_code == 204
    assert await arrows_of(api, kid["id"]) == 4
    assert (await api.put(path, json={"pin": None, "arrows": True})).status_code == 204
    assert await arrows_of(api, kid["id"]) is None
    assert (await picker(api, tv))[kid["id"]]["has_pin"] is False


async def test_an_arrow_pattern_holds_only_arrow_digits(api: Api) -> None:
    kid = await user(api, "Kid", "kid", pin="2468")
    path = f"/users/{kid['id']}/pin"
    for pin in ("2461", "24680", "5555"):
        r = await api.put(path, json={"pin": pin, "arrows": True})
        assert r.status_code == 400 and r.json()["code"] == "invalid_pin", (pin, r.text)
    # The length rules are a PIN's: four to eight.
    for pin in ("246", "246824682"):
        assert (await api.put(path, json={"pin": pin, "arrows": True})).status_code == 422, pin
    # A refused pattern leaves the PIN that was there.
    assert await arrows_of(api, kid["id"]) is None
    tv = await pair(api, shared_device=True)
    assert (await switch(api, tv, kid["id"], "2468")).status_code == 200
    # Creating a person asks the same.
    r = await api.post("/users", json={"name": "Small", "role_id": "kid", "pin": "1234", "arrows": True})
    assert r.status_code == 400 and r.json()["code"] == "invalid_pin"


async def test_switching_takes_the_pattern_as_its_digits(api: Api) -> None:
    created = await api.post("/users", json={"name": "Parent", "role_id": "family", "pin": "88664422", "arrows": True})
    assert created.status_code == 201, created.text
    parent = created.json()
    assert parent["pin_arrows"] == 8
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True, profile_id=kid["profile_id"])
    assert (await picker(api, tv))[parent["id"]]["pin_arrows"] == 8

    wrong = await switch(api, tv, parent["id"], "88664424")
    assert wrong.status_code == 401 and wrong.json()["code"] == "pin_invalid"
    r = await switch(api, tv, parent["id"], "88664422")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["id"] == parent["id"] and r.json()["user"]["pin_arrows"] == 8
    # /auth/me says the same of the person now on the TV.
    me = (await api.client.get("/api/v1/auth/me", headers=bearer(r.json()))).json()
    assert me["user"]["pin_arrows"] == 8


async def test_the_older_profile_route_records_a_pattern_too(api: Api) -> None:
    kid = await user(api, "Kid", "kid")
    path = f"/profiles/{kid['profile_id']}/pin"
    assert (await api.put(path, json={"pin": "44662288", "arrows": True})).status_code == 204
    profiles = {p["id"]: p for p in (await api.get("/profiles")).json()}
    assert profiles[kid["profile_id"]]["pin_arrows"] == 8
    r = await api.put(path, json={"pin": "1234", "arrows": True})
    assert r.status_code == 400 and r.json()["code"] == "invalid_pin"
    assert (await api.put(path, json={"pin": "1234"})).status_code == 204
    profiles = {p["id"]: p for p in (await api.get("/profiles")).json()}
    assert profiles[kid["profile_id"]]["pin_arrows"] is None
    # The household's sign-in takes the digits as before.
    assert (await sign_in(api, kid, "1234")).id == kid["id"]


def test_revision_0015_keeps_every_pin_a_typed_one(tmp_path: Any) -> None:
    import sqlite3

    from alembic import command

    path = tmp_path / "upgrade.db"
    cfg = config(path)
    command.upgrade(cfg, "0014")
    db = sqlite3.connect(path)
    db.execute(
        "INSERT INTO profile (id, name, is_admin, theme_id, languages, kids, content_policy, pin_hash, "
        "token_version, prefs, created_at) VALUES ('p', 'P', 0, 'midnight', '[]', 0, '{}', 'kept', 0, '{}', ?)",
        (NOW,),
    )
    db.commit()
    db.close()
    command.upgrade(cfg, "head")
    db = sqlite3.connect(path)
    assert db.execute("SELECT pin_hash, pin_arrows FROM profile").fetchone() == ("kept", None)
    db.close()
    command.downgrade(cfg, "0014")
    db = sqlite3.connect(path)
    assert db.execute("SELECT pin_hash FROM profile").fetchone() == ("kept",)
    assert "pin_arrows" not in {row[1] for row in db.execute("PRAGMA table_info(profile)")}
    db.close()
