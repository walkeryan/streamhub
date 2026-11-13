"""Helpers for tests that sign someone in with Plex. plex.tv is always mocked with respx."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import respx
from httpx import AsyncClient

from tests.conftest import Api

PLEX_TV = "https://clients.plex.tv/api/v2"
USER_TOKEN = "plex-user-token-not-real"
OWNER_TOKEN = "plex-owner-token-not-real"


def account(account_id: int = 501, **overrides: Any) -> dict[str, Any]:
    return {
        "id": account_id,
        "uuid": f"uuid-{account_id}",
        "username": f"viewer{account_id}",
        "title": f"Viewer {account_id}",
        "email": f"viewer{account_id}@example.test",
        "confirmed": True,
        "thumb": f"https://plex.example.test/avatars/{account_id}.png",
        **overrides,
    }


def mock_plex(who: dict[str, Any] | None = None, *, pin_id: int = 4242, authorised: bool = True) -> dict[str, Any]:
    """Mock the three plex.tv calls of one sign-in; returns the routes for call assertions."""
    who = who if who is not None else account()
    pins = respx.post(f"{PLEX_TV}/pins").mock(
        return_value=httpx.Response(201, json={"id": pin_id, "code": "strong-code-" + "x" * 20})
    )
    pin = respx.get(f"{PLEX_TV}/pins/{pin_id}").mock(
        return_value=httpx.Response(200, json={"id": pin_id, "authToken": USER_TOKEN if authorised else None})
    )

    def user(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-plex-token") != USER_TOKEN:
            return httpx.Response(401, json={"errors": [{"code": 1001, "message": "User could not be authenticated"}]})
        return httpx.Response(200, json=who)

    return {"pins": pins, "pin": pin, "user": respx.get(f"{PLEX_TV}/user").mock(side_effect=user)}


async def start(client: AsyncClient, **body: Any) -> tuple[str, dict[str, list[str]]]:
    """POST /auth/plex/start. Returns (state, the query Plex's sign-in page was given)."""
    r = await client.post("/api/v1/auth/plex/start", json=body)
    assert r.status_code == 200, r.text
    page, _, fragment = r.json()["auth_url"].partition("#?")
    assert page == "https://app.plex.tv/auth"
    query = parse_qs(fragment)
    forward = urlsplit(query["forwardUrl"][0])
    return parse_qs(forward.query)["plex"][0], query


async def complete(client: AsyncClient, state: str) -> httpx.Response:
    return await client.post("/api/v1/auth/plex/complete", json={"state": state})


async def plex_sign_in(
    api: Api, who: dict[str, Any] | None = None, *, client: AsyncClient | None = None, **body: Any
) -> httpx.Response:
    """Start to finish for one synthetic Plex account, in one browser."""
    client = client or api.client
    mock_plex(who)
    state, _ = await start(client, **body)
    return await complete(client, state)
