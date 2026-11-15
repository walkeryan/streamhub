"""Each person signs in on shared devices with one secret of their choosing: a number PIN, a D-pad
arrow pattern, or a password. The kind only says how a client asks for it; every kind is hashed,
checked and rate limited the same way."""

from __future__ import annotations

from typing import Any

from tests.conftest import Api
from tests.people import sign_in
from tests.test_pin_change import age_sessions, refused
from tests.test_shared_devices import bearer, pair, switch, user
from tests.test_users_migration import NOW, config

PASSWORD = "correct horse 7"


async def kind_of(api: Api, person: dict[str, Any], tv: dict[str, Any] | None = None) -> Any:
    """``pin_kind`` as the user list, the user page, the profile list and (given a TV) the picker say it."""
    listed = {u["id"]: u for u in (await api.get("/users")).json()}[person["id"]]
    detail = (await api.get(f"/users/{person['id']}")).json()
    profile = {p["id"]: p for p in (await api.get("/profiles")).json()}[person["profile_id"]]
    assert listed["pin_kind"] == detail["pin_kind"] == profile["pin_kind"]
    assert detail["has_pin"] is (detail["pin_kind"] is not None)
    if tv is not None:
        r = await api.client.get("/api/v1/auth/switchable", headers=bearer(tv))
        assert {u["id"]: u for u in r.json()["users"]}[person["id"]]["pin_kind"] == detail["pin_kind"]
    return detail["pin_kind"]


async def test_each_kind_is_set_shown_and_cleared(api: Api) -> None:
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True)
    path = f"/users/{kid['id']}/pin"
    assert await kind_of(api, kid, tv) is None

    assert (await api.put(path, json={"pin": "1357", "kind": "pin"})).status_code == 204
    assert await kind_of(api, kid, tv) == "pin"
    assert (await api.put(path, json={"pin": "246824", "kind": "arrows"})).status_code == 204
    assert await kind_of(api, kid, tv) == "arrows"
    assert (await api.get(f"/users/{kid['id']}")).json()["pin_arrows"] == 6
    assert (await api.put(path, json={"pin": PASSWORD, "kind": "password"})).status_code == 204
    assert await kind_of(api, kid, tv) == "password"
    # A password has no arrows to count, whatever was there before.
    assert (await api.get(f"/users/{kid['id']}")).json()["pin_arrows"] is None
    assert (await api.put(path, json={"pin": None, "kind": "password"})).status_code == 204
    assert await kind_of(api, kid, tv) is None
    # Without a kind it is a number PIN, as it always was.
    assert (await api.put(path, json={"pin": "8642"})).status_code == 204
    assert await kind_of(api, kid, tv) == "pin"


async def test_each_kind_has_its_own_rules(api: Api) -> None:
    kid = await user(api, "Kid", "kid", pin="2468")
    path = f"/users/{kid['id']}/pin"
    refused_bodies = [
        {"pin": "12a4", "kind": "pin"},
        {"pin": "123456789", "kind": "pin"},
        {"pin": "２４６８", "kind": "pin"},  # full-width digits are not ASCII digits
        {"pin": "2461", "kind": "arrows"},
        {"pin": "246824682", "kind": "arrows"},
        {"pin": "short", "kind": "password"},
        {"pin": "x" * 65, "kind": "password"},
        {"pin": "tab\there", "kind": "password"},
    ]
    for body in refused_bodies:
        r = await api.put(path, json=body)
        assert r.status_code in (400, 422), (body, r.text)
        if r.status_code == 400:
            assert r.json()["code"] == "invalid_pin", (body, r.text)
    # Too short for any kind, or a kind that does not exist, is refused before it reaches the service.
    assert (await api.put(path, json={"pin": "123", "kind": "pin"})).status_code == 422
    assert (await api.put(path, json={"pin": "123456", "kind": "pattern"})).status_code == 422
    # A refused secret leaves the one that was there.
    assert await kind_of(api, kid) == "pin"
    # A password is taken exactly as typed: spaces, symbols and letters beyond ASCII.
    for password in ("  spaced  ", "pässwörd!", "x" * 64, "123456"):
        assert (await api.put(path, json={"pin": password, "kind": "password"})).status_code == 204, password
    tv = await pair(api, shared_device=True)
    assert (await switch(api, tv, kid["id"], "123456")).status_code == 200


async def test_kind_wins_over_the_older_arrows_flag(api: Api) -> None:
    kid = await user(api, "Kid", "kid")
    path = f"/users/{kid['id']}/pin"
    # Older clients say an arrow pattern with arrows: true alone, and it still works.
    assert (await api.put(path, json={"pin": "2288", "arrows": True})).status_code == 204
    assert await kind_of(api, kid) == "arrows"
    assert (await api.get(f"/users/{kid['id']}")).json()["pin_arrows"] == 4
    # Both sent: kind decides.
    assert (await api.put(path, json={"pin": "1357", "arrows": True, "kind": "pin"})).status_code == 204
    assert await kind_of(api, kid) == "pin"
    assert (await api.put(path, json={"pin": PASSWORD, "arrows": True, "kind": "password"})).status_code == 204
    assert await kind_of(api, kid) == "password"


async def test_adding_a_person_with_each_kind(api: Api) -> None:
    created = await api.post(
        "/users", json={"name": "Parent", "role_id": "family", "pin": PASSWORD, "kind": "password"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["pin_kind"] == "password" and created.json()["pin_arrows"] is None
    created = await api.post("/users", json={"name": "Teen", "role_id": "family", "pin": "26842684", "kind": "arrows"})
    assert created.json()["pin_kind"] == "arrows" and created.json()["pin_arrows"] == 8
    created = await api.post("/users", json={"name": "Small", "role_id": "kid", "pin": "4321"})
    assert created.json()["pin_kind"] == "pin"
    r = await api.post("/users", json={"name": "Other", "role_id": "kid", "pin": "abcd", "kind": "pin"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_pin"
    assert (await api.post("/users", json={"name": "None", "role_id": "kid"})).json()["pin_kind"] is None


async def test_switching_to_a_password_person_and_its_rate_limit(api: Api) -> None:
    parent = (
        await api.post("/users", json={"name": "Parent", "role_id": "family", "pin": PASSWORD, "kind": "password"})
    ).json()
    kid = await user(api, "Kid", "kid")
    tv = await pair(api, shared_device=True, profile_id=kid["profile_id"])

    r = await switch(api, tv, parent["id"])
    assert r.status_code == 401 and r.json()["code"] == "pin_required"
    # Anything up to 64 characters is offered to the hash check, which alone decides.
    for wrong in ("correct horse", "x" * 64, "1234"):
        r = await switch(api, tv, parent["id"], wrong)
        assert r.status_code == 401 and r.json()["code"] == "pin_invalid", (wrong, r.text)
    assert (await switch(api, tv, parent["id"], "y" * 65)).status_code == 422
    r = await switch(api, tv, parent["id"], PASSWORD)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["pin_kind"] == "password"
    me = (await api.client.get("/api/v1/auth/me", headers=bearer(r.json()))).json()
    assert me["user"]["pin_kind"] == "password"

    # Five wrong guesses in fifteen minutes close the target, as with a number PIN.
    tv2 = await pair(api, shared_device=True, profile_id=kid["profile_id"])
    for _ in range(5):
        assert (await switch(api, tv2, parent["id"], "not it at all")).status_code == 401
    assert (await switch(api, tv2, parent["id"], PASSWORD)).status_code == 429


async def test_a_password_opens_the_adult_category_and_proves_the_holder(api: Api) -> None:
    me = (await api.get("/auth/me")).json()["user"]
    path = f"/users/{me['id']}/pin"
    assert (await api.put(path, json={"pin": PASSWORD, "kind": "password"})).status_code == 204
    assert (await api.post("/auth/adult-unlock", json={"pin": "wrong password"})).status_code == 403
    assert (await api.post("/auth/adult-unlock", json={"pin": PASSWORD})).status_code == 200
    # Changing it later asks for the current one, which is the password.
    await age_sessions(api)
    assert refused(await api.put(path, json={"pin": "2468", "kind": "pin"}), "current_pin_required")
    assert refused(await api.put(path, json={"pin": "2468", "current_pin": "nope nope"}), "pin_invalid")
    r = await api.put(path, json={"pin": "2468", "kind": "pin", "current_pin": PASSWORD})
    assert r.status_code == 204, r.text
    # The older profile route asks the same and takes a kind too.
    profile_path = f"/profiles/{me['profile_id']}/pin"
    r = await api.put(profile_path, json={"pin": PASSWORD, "kind": "password", "current_pin": "2468"})
    assert r.status_code == 204, r.text
    assert (await api.get("/auth/me")).json()["user"]["pin_kind"] == "password"


async def test_the_household_sign_in_takes_a_password_person(api: Api) -> None:
    kid = await user(api, "Kid", "kid")
    path = f"/profiles/{kid['profile_id']}/pin"
    assert (await api.put(path, json={"pin": PASSWORD, "kind": "password"})).status_code == 204
    assert (await sign_in(api, kid, PASSWORD)).id == kid["id"]


def test_revision_0016_backfills_the_kind_of_every_pin(tmp_path: Any) -> None:
    import sqlite3

    from alembic import command

    path = tmp_path / "upgrade.db"
    cfg = config(path)
    command.upgrade(cfg, "0015")
    db = sqlite3.connect(path)
    for pid, pin_hash, arrows in (
        ("typed", "h1", None),
        ("pattern", "h2", 6),
        ("none", None, None),
        ("stale", None, 4),
    ):
        db.execute(
            "INSERT INTO profile (id, name, is_admin, theme_id, languages, kids, content_policy, pin_hash, pin_arrows, "
            "token_version, prefs, created_at) VALUES (?, 'P', 0, 'midnight', '[]', 0, '{}', ?, ?, 0, '{}', ?)",
            (pid, pin_hash, arrows, NOW),
        )
    db.commit()
    db.close()
    command.upgrade(cfg, "head")
    db = sqlite3.connect(path)
    rows = dict(db.execute("SELECT id, pin_kind FROM profile").fetchall())
    assert rows == {"typed": "pin", "pattern": "arrows", "none": None, "stale": None}
    db.close()
    command.downgrade(cfg, "0015")
    db = sqlite3.connect(path)
    assert "pin_kind" not in {row[1] for row in db.execute("PRAGMA table_info(profile)")}
    assert db.execute("SELECT pin_hash, pin_arrows FROM profile WHERE id = 'pattern'").fetchone() == ("h2", 6)
    db.close()
