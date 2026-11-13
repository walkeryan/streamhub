"""Small endpoints of the people work: the Plex-account link, the TV app release, and a check
that every permission in the catalogue is actually asked for somewhere."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.infra.models import AuditLog, User
from app.services.permissions import PERMISSIONS
from tests.conftest import Api
from tests.people import member, owner_id

APP = Path(__file__).resolve().parents[1] / "app"
SHA = "ab" * 32


# ---------------------------------------------------------------- plex_account_linked
async def linked(api: Api) -> set[str]:
    async with api.c.db.session() as s:
        return set((await s.execute(select(User.id).where(User.plex_account_linked.is_(True)))).scalars())


async def plex_audit(api: Api) -> list[tuple[str, str | None, str | None]]:
    async with api.c.db.session() as s:
        rows = (
            await s.execute(select(AuditLog).where(AuditLog.action.like("user.plex_%")).order_by(AuditLog.id))
        ).scalars()
        return [(r.action, r.actor_user_id, r.target_user_id) for r in rows]


async def test_plex_account_link_belongs_to_one_user(api: Api) -> None:
    me = await owner_id(api)
    parent = await member(api, "family", "Parent")
    assert (await api.get(f"/users/{me}")).json()["plex_account_linked"] is False
    r = await api.patch(f"/users/{me}", json={"plex_account_linked": True})  # one's own account is fine
    assert r.status_code == 200 and r.json()["plex_account_linked"] is True
    assert await linked(api) == {me}
    await api.login_admin()  # like any change to a user, it makes that user's tokens stale
    # Linking another user takes it from the first, and both changes are on the record.
    r = await api.patch(f"/users/{parent.id}", json={"plex_account_linked": True})
    assert r.status_code == 200 and r.json()["plex_account_linked"] is True
    assert await linked(api) == {parent.id}
    assert (await api.get(f"/users/{me}")).json()["plex_account_linked"] is False
    assert await plex_audit(api) == [
        ("user.plex_link", me, me),
        ("user.plex_unlink", me, me),
        ("user.plex_link", me, parent.id),
    ]
    # Setting what is already so, or sending null, changes and records nothing.
    assert (await api.patch(f"/users/{parent.id}", json={"plex_account_linked": True})).status_code == 200
    assert (await api.patch(f"/users/{me}", json={"plex_account_linked": False})).status_code == 200
    await api.login_admin()
    assert (await api.patch(f"/users/{parent.id}", json={"plex_account_linked": None})).status_code == 200
    assert await linked(api) == {parent.id} and len(await plex_audit(api)) == 3
    r = await api.patch(f"/users/{parent.id}", json={"plex_account_linked": False})
    assert r.status_code == 200 and r.json()["plex_account_linked"] is False
    assert await linked(api) == set() and (await plex_audit(api))[-1] == ("user.plex_unlink", me, parent.id)
    assert (await api.patch(f"/users/{parent.id}", json={"plex_account_linked": "yes please"})).status_code == 422


async def test_plex_account_link_needs_users_manage(api: Api) -> None:
    parent = await member(api, "family", "Parent")
    r = await parent.req("PATCH", f"/users/{parent.id}", json={"plex_account_linked": True})
    assert r.status_code == 403 and r.json()["missing"] == ["users.manage"]
    # A manager who is not an owner cannot move it onto, or off, an owner.
    manager = await member(api, "family", "Manager", grants=["users.manage"])
    await manager.refresh()
    me = await owner_id(api)
    r = await manager.req("PATCH", f"/users/{me}", json={"plex_account_linked": True})
    assert r.status_code == 403 and r.json()["code"] == "owner_required"
    assert (await api.patch(f"/users/{me}", json={"plex_account_linked": True})).status_code == 200
    await api.login_admin()
    r = await manager.req("PATCH", f"/users/{parent.id}", json={"plex_account_linked": True})
    assert r.status_code == 403 and r.json()["code"] == "owner_required"
    assert await linked(api) == {me}
    assert (await api.patch(f"/users/{me}", json={"plex_account_linked": False})).status_code == 200
    assert (await manager.req("PATCH", f"/users/{parent.id}", json={"plex_account_linked": True})).status_code == 200
    assert await linked(api) == {parent.id}


# ---------------------------------------------------------------- GET /app/tv
def release(api: Api, manifest: Any, *, apk: str | None = "streamhub-tv-1.4.0.apk") -> Path:
    downloads = api.c.settings.web_dist / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    if apk:
        (downloads / apk).write_bytes(b"not really an apk")
    path = downloads / "tv-release.json"
    path.write_text(manifest if isinstance(manifest, str) else json.dumps(manifest))
    return path


GOOD = {"version_code": 14, "version_name": "1.4.0", "apk": "streamhub-tv-1.4.0.apk", "sha256": SHA.upper()}


async def test_tv_release_is_public_and_read_from_the_manifest(api: Api) -> None:
    assert (await api.client.get("/api/v1/app/tv")).status_code == 404  # no manifest, no update service
    release(api, {**GOOD, "notes": "ignored"})
    r = await api.client.get("/api/v1/app/tv")  # no bearer
    assert r.status_code == 200, r.text
    assert r.json() == {
        "version_code": 14,
        "version_name": "1.4.0",
        "apk_url": "/downloads/streamhub-tv-1.4.0.apk",
        "sha256": SHA,
    }
    assert r.headers["cache-control"] == "no-cache"
    # The address it names is the one the web client's files are served from.
    (api.c.settings.web_dist / "index.html").write_text("<html>app</html>")
    assert (await api.client.get(r.json()["apk_url"])).content == b"not really an apk"
    # A new manifest is picked up without a restart.
    release(api, {**GOOD, "version_code": 15, "version_name": "1.5.0"})
    assert (await api.client.get("/api/v1/app/tv")).json()["version_code"] == 15


async def test_an_invalid_manifest_is_no_update_service(api: Api) -> None:
    bad: list[Any] = [
        "{not json",
        "[]",
        "x" * 20_000,
        {k: v for k, v in GOOD.items() if k != "sha256"},
        {**GOOD, "sha256": "abc123"},
        {**GOOD, "sha256": "zz" * 32},
        {**GOOD, "version_code": 0},
        {**GOOD, "version_code": "14"},
        {**GOOD, "version_code": True},
        {**GOOD, "version_name": ""},
        {**GOOD, "version_name": 14},
        {**GOOD, "apk": "missing.apk"},
        {**GOOD, "apk": "../secret.key"},
        {**GOOD, "apk": "sub/streamhub-tv-1.4.0.apk"},
        {**GOOD, "apk": "tv-release.json"},
        {**GOOD, "apk": ""},
        {**GOOD, "apk": None},
    ]
    for manifest in bad:
        release(api, manifest)
        r = await api.client.get("/api/v1/app/tv")
        assert r.status_code == 404 and r.json()["code"] == "not_found", manifest
    # Nothing outside the downloads directory is reachable through the manifest.
    (api.c.settings.web_dist / "outside.apk").write_bytes(b"x")
    release(api, {**GOOD, "apk": "../outside.apk"}, apk=None)
    assert (await api.client.get("/api/v1/app/tv")).status_code == 404


# ---------------------------------------------------------------- every permission gates something
def _strings(node: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def referenced_permissions() -> dict[str, set[str]]:
    """Permission names used where access is decided: ``require(...)`` in the API layer, and
    the modules that are the single place a feature asks (policies, visibility, sessions)."""
    found: dict[str, set[str]] = {}
    for path in sorted((APP / "api").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "require":
                for name in _strings(node) & PERMISSIONS:
                    found.setdefault(name, set()).add(f"require() in {path.name}")
    deciders = [*sorted((APP / "services").glob("*_policy.py")), APP / "services" / "visibility.py"]
    deciders += [APP / "services" / "sessions.py", APP / "services" / "invites.py", APP / "api" / "deps.py"]
    for path in deciders:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            # Only a membership test against someone's permissions counts, not a mention.
            if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                for name in _strings(node.left) & PERMISSIONS:
                    found.setdefault(name, set()).add(path.name)
            if isinstance(node, (ast.Dict, ast.DictComp)) and path.name == "visibility.py":
                for name in _strings(node) & PERMISSIONS:
                    found.setdefault(name, set()).add(path.name)
    return found


def test_every_permission_gates_something() -> None:
    found = referenced_permissions()
    unused = PERMISSIONS - set(found)
    assert not unused, f"permissions nothing checks: {sorted(unused)}"
    # The ones this phase wired up, and where.
    assert "room_policy.py" in found["rooms.create"] and "room_policy.py" in found["rooms.join"]
    assert "recording_policy.py" in found["recordings.view"] and "recording_policy.py" in found["recordings.schedule"]
    assert "deps.py" in found["remote.access"]
    assert "request_policy.py" in found["requests.approve"]
