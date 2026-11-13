"""Refresh tokens rotate. The one before the current is honoured for a moment (two tabs
refreshing at once); anything older being replayed means a copy is loose, and ends the session."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

import httpx
import jwt
from sqlalchemy import select

from app.infra.models import AuditLog, AuthSession, utcnow
from app.services.auth import ALG, REFRESH_GRACE_S
from tests.conftest import ADMIN_PASSWORD, Api
from tests.people import member


def claims(token: str) -> dict[str, Any]:
    decoded: dict[str, Any] = jwt.decode(token, options={"verify_signature": False})
    return decoded


async def refresh(api: Api, token: str) -> httpx.Response:
    return await api.client.post("/api/v1/auth/refresh", json={"refresh_token": token})


async def login(api: Api) -> dict[str, Any]:
    r = await api.client.post("/api/v1/auth/login", json={"profile_id": api.profile_id, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def age_rotation(api: Api, sid: str, seconds: float) -> None:
    async with api.c.db.session() as s:
        row = await s.get(AuthSession, sid)
        assert row is not None and row.refresh_rotated_at is not None
        row.refresh_rotated_at = utcnow() - timedelta(seconds=seconds)
        await s.commit()


def alive(r: httpx.Response) -> bool:
    return r.status_code == 200


async def test_each_refresh_hands_out_a_new_refresh_token(api: Api) -> None:
    first = await login(api)
    sid = claims(first["refresh_token"])["sid"]
    second = (await refresh(api, first["refresh_token"])).json()
    third = (await refresh(api, second["refresh_token"])).json()
    seqs = [claims(t["refresh_token"]).get("rs", 0) for t in (first, second, third)]
    assert seqs == [0, 1, 2] and len({t["refresh_token"] for t in (first, second, third)}) == 3
    assert {claims(t["refresh_token"])["sid"] for t in (second, third)} == {sid}
    async with api.c.db.session() as s:
        row = await s.get(AuthSession, sid)
        assert row is not None and row.refresh_seq == 2 and row.revoked_at is None
    # Access tokens are untouched by rotation: each lives out its own few minutes.
    for tokens in (first, second, third):
        me = await api.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert me.status_code == 200


async def test_the_previous_token_is_good_for_a_moment(api: Api) -> None:
    """Two tabs hold the same token and both refresh: the slower one must not be signed out."""
    first = await login(api)
    second = (await refresh(api, first["refresh_token"])).json()
    late = await refresh(api, first["refresh_token"])
    assert alive(late) and claims(late.json()["refresh_token"]).get("rs") == 1
    # It did not rotate again, so the faster tab's token is still the current one.
    assert alive(await refresh(api, second["refresh_token"]))


async def test_a_burst_of_refreshes_with_one_token_all_succeed(api: Api) -> None:
    first = await login(api)
    answers = await asyncio.gather(*(refresh(api, first["refresh_token"]) for _ in range(5)))
    assert all(alive(r) for r in answers)
    assert {claims(r.json()["refresh_token"]).get("rs") for r in answers} == {1}


async def test_replaying_an_old_token_ends_the_session(api: Api) -> None:
    first = await login(api)
    other = await login(api)  # the same person on another device
    sid = claims(first["refresh_token"])["sid"]
    second = (await refresh(api, first["refresh_token"])).json()
    await age_rotation(api, sid, REFRESH_GRACE_S + 1)
    stolen = await refresh(api, first["refresh_token"])
    assert stolen.status_code == 401 and stolen.json()["code"] == "session_revoked"
    # Whoever holds the newer tokens is out as well: one of the two is not the owner of the device.
    assert (await refresh(api, second["refresh_token"])).status_code == 401
    me = await api.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"})
    assert me.status_code == 401
    async with api.c.db.session() as s:
        row = await s.get(AuthSession, sid)
        assert row is not None and row.revoked_at is not None
        logged = [(a.action, a.detail) for a in (await s.execute(select(AuditLog))).scalars()]
    assert ("session.refresh_reuse", {"kind": "api", "label": None}) in logged
    assert first["refresh_token"] not in repr(logged)
    # Their other device is a different session and carries on.
    assert alive(await refresh(api, other["refresh_token"]))


async def test_a_token_two_rotations_old_is_never_in_grace(api: Api) -> None:
    first = await login(api)
    second = (await refresh(api, first["refresh_token"])).json()
    third = (await refresh(api, second["refresh_token"])).json()
    assert (await refresh(api, first["refresh_token"])).status_code == 401
    assert (await refresh(api, third["refresh_token"])).status_code == 401


async def test_a_token_from_before_rotation_is_adopted_once(api: Api) -> None:
    """Tokens minted before this existed carry no counter: the first refresh starts it."""
    first = await login(api)
    old = claims(first["refresh_token"])
    old.pop("rs", None)
    legacy = jwt.encode({**old, "exp": int(time.time()) + 600}, api.c.crypto.jwt_key, algorithm=ALG)
    upgraded = await refresh(api, legacy)
    assert alive(upgraded) and claims(upgraded.json()["refresh_token"])["rs"] == 1
    forged = jwt.encode({**old, "rs": 99, "exp": int(time.time()) + 600}, api.c.crypto.jwt_key, algorithm=ALG)
    assert (await refresh(api, forged)).status_code == 401


async def test_a_shared_device_keeps_its_rotation_across_a_hand_over(api: Api) -> None:
    kid = await member(api, "kid", "Kid")
    assert alive(await kid.refresh()) and alive(await kid.refresh())
    assert claims(kid.tokens["refresh_token"])["rs"] == 2
