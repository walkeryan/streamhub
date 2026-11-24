"""POST /auth/player-token: a longer-lived access token for a native player, so playback keeps
recording progress (and can start the next episode) without the page that owns the session.

The chain of player tokens ends a fixed time after the access token it started from, and every
request is still validated against the session, so a sign-out ends them at once."""

from __future__ import annotations

import time

import jwt

from tests.conftest import Api


def _claims(token: str) -> dict[str, object]:
    return jwt.decode(token, options={"verify_signature": False})


async def _mint(api: Api, access: str) -> object:
    return await api.client.post("/api/v1/auth/player-token", headers={"Authorization": f"Bearer {access}"})


async def test_player_token_lasts_an_hour_and_works(api: Api) -> None:
    access = api.headers["Authorization"].split(" ", 1)[1]
    r = await _mint(api, access)
    assert r.status_code == 200, r.text
    body = r.json()
    assert r.headers["cache-control"] == "private, no-store"
    token = body["access_token"]
    claims, original = _claims(token), _claims(access)
    assert body["expires_in"] == 3600
    assert claims["exp"] - claims["iat"] == 3600
    # Same session, person and generation as the token it came from; the chain start is recorded.
    for key in ("sub", "sid", "ver", "uid", "uv", "opv", "typ"):
        assert claims.get(key) == original.get(key), key
    assert claims["pti"] == original["iat"]
    me = await api.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


async def test_player_token_renews_but_keeps_its_chain_start(api: Api) -> None:
    access = api.headers["Authorization"].split(" ", 1)[1]
    first = (await _mint(api, access)).json()["access_token"]
    second = (await _mint(api, first)).json()["access_token"]
    assert _claims(second)["pti"] == _claims(access)["iat"]


async def test_player_token_chain_ends_twelve_hours_after_it_started(api: Api) -> None:
    access = api.headers["Authorization"].split(" ", 1)[1]
    key = api.c.auth._key
    now = int(time.time())
    late = jwt.encode({**_claims(access), "pti": now - 11 * 3600 - 1800}, key, algorithm="HS256")
    r = await _mint(api, late)
    assert r.status_code == 200
    assert 1790 <= r.json()["expires_in"] <= 1800
    ended = jwt.encode({**_claims(access), "pti": now - 12 * 3600 + 30}, key, algorithm="HS256")
    r = await _mint(api, ended)
    assert r.status_code == 401 and r.json()["code"] == "token_expired"


async def test_refresh_token_cannot_mint(api: Api) -> None:
    r = await api.client.post("/api/v1/auth/login", json={"profile_id": api.profile_id, "password": "test-admin-pw"})
    refresh = r.json()["refresh_token"]
    assert (await _mint(api, refresh)).status_code == 401


async def test_player_token_ends_with_the_session(api: Api) -> None:
    access = api.headers["Authorization"].split(" ", 1)[1]
    token = (await _mint(api, access)).json()["access_token"]
    sid = _claims(access)["sid"]
    assert (await api.client.delete(f"/api/v1/auth/sessions/{sid}", headers=api.headers)).status_code == 204
    me = await api.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401
    assert (await _mint(api, token)).status_code == 401
