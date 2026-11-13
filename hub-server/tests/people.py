"""Helpers for tests about users, roles and sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select

from app.infra.models import AuditLog, User
from tests.conftest import Api


@dataclass
class Member:
    """A signed-in user other than the fixture's owner."""

    api: Api
    id: str
    profile_id: str
    tokens: dict[str, Any]

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens['access_token']}"}

    async def req(self, method: str, url: str, **kw: Any) -> httpx.Response:
        return await self.api.client.request(method, "/api/v1" + url, headers=self.headers, **kw)

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.req("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.req("POST", url, **kw)

    async def refresh(self) -> httpx.Response:
        r = await self.api.client.post("/api/v1/auth/refresh", json={"refresh_token": self.tokens["refresh_token"]})
        if r.status_code == 200:
            self.tokens = r.json()
        return r


async def sign_in(api: Api, user: dict[str, Any], pin: str | None = None) -> Member:
    if api.c.settings.google_auth_required:
        # Local sign-in is closed in this mode; mint the session the way a pairing would.
        profile = await api.c.auth.get_profile(user["profile_id"])
        return Member(api, user["id"], user["profile_id"], await api.c.auth.issue(profile))
    r = await api.client.post("/api/v1/auth/login", json={"profile_id": user["profile_id"], "pin": pin})
    assert r.status_code == 200, r.text
    return Member(api, user["id"], user["profile_id"], r.json())


async def member(
    api: Api, role_id: str = "family", name: str = "Member", pin: str | None = None, **patch: Any
) -> Member:
    """Create a managed user as the owner, optionally with overrides, and sign them in."""
    body: dict[str, Any] = {"name": name, "role_id": role_id}
    if pin:
        body["pin"] = pin
    r = await api.post("/users", json=body)
    assert r.status_code == 201, r.text
    if patch:
        r2 = await api.patch(f"/users/{r.json()['id']}", json=patch)
        assert r2.status_code == 200, r2.text
    return await sign_in(api, r.json(), pin)


async def owner_id(api: Api) -> str:
    r = await api.get("/auth/me")
    assert r.status_code == 200, r.text
    return str(r.json()["user"]["id"])


async def set_user(api: Api, user_id: str, **values: Any) -> None:
    """Write user columns directly, bypassing the service (and its version bump) on purpose."""
    async with api.c.db.session() as s:
        user = await s.get(User, user_id)
        assert user is not None
        for key, value in values.items():
            setattr(user, key, value)
        await s.commit()


async def audit_actions(api: Api) -> list[str]:
    async with api.c.db.session() as s:
        return [r.action for r in (await s.execute(select(AuditLog).order_by(AuditLog.id))).scalars()]
