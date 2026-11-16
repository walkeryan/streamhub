from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from starlette.requests import Request

from app.api.routers.playback import image_proxy
from app.infra.crypto import Crypto
from app.services.auth import Principal
from app.services.content_policy import active_principal
from app.services.errors import Forbidden
from app.services.image_grants import UNBOUND_TTL, cache_control, image_payload, validate_image_grant


@pytest.fixture
def ctx() -> Any:
    policy = SimpleNamespace(enabled=True, applies_to=lambda principal: True, require=AsyncMock())
    ptoken = active_principal.set(Principal("kid", False, True, 4, "kid-session"))
    payload = image_payload("plex", "https://fixture.invalid/art", "lib:plex:17", policy)
    active_principal.reset(ptoken)
    prov = SimpleNamespace(image_headers=lambda url: {"fixture": "header"})
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, content=b"fixture-image", headers={"content-type": "image/png"})
    client.__aenter__.return_value = client
    return SimpleNamespace(
        content_policy=policy,
        auth=SimpleNamespace(session_valid=AsyncMock()),
        payload=payload,
        crypto=Crypto("fixture-image-token-key"),
        sources=SimpleNamespace(provider_for=AsyncMock(return_value=(None, prov))),
        http_factory=Mock(return_value=client),
        client=client,
        settings=SimpleNamespace(forwarded_allow_ips=""),
        app_settings=SimpleNamespace(get=lambda key: None),
    )


def request(peer: str = "192.168.1.20") -> Request:
    return Request({"type": "http", "headers": [], "client": (peer, 40000)})


async def test_one_url_per_image_whoever_it_was_signed_for(ctx: Any) -> None:
    """A rating-limited kid and an unrestricted adult get the same URL for the same image: the ceiling
    decides which titles they are shown, not whose copy of a poster they fetch."""
    kid = Principal("kid", False, True, 4, "kid-session")
    adult = Principal("adult", True, True, 1, "adult-session", permissions=frozenset({"remote.access"}))
    signed = []
    for who in (kid, adult, None):
        token = active_principal.set(who)
        try:
            signed.append(image_payload("plex", "https://fixture.invalid/art", "lib:plex:17", ctx.content_policy))
        finally:
            active_principal.reset(token)
    assert signed[0] == signed[1] == signed[2] == ctx.payload
    assert set(ctx.payload) == {"s", "u", "x"}


async def test_any_cache_keeps_the_image_until_its_url_expires(ctx: Any) -> None:
    response = await image_proxy(ctx.crypto.sign_json(ctx.payload), request("203.0.113.9"), ctx)
    assert response.body == b"fixture-image"
    cache = response.headers["cache-control"]
    assert cache.startswith("public, max-age=") and cache.endswith(", immutable")
    # Kept as long as the URL works: a week or more, never past it.
    assert UNBOUND_TTL - 1 <= int(cache.split("max-age=")[1].split(",")[0]) <= 2 * UNBOUND_TTL
    assert cache_control({**ctx.payload, "x": 0}) == "no-store"
    # Nobody's session or policy is consulted for a poster.
    ctx.auth.session_valid.assert_not_called()
    ctx.content_policy.require.assert_not_called()


async def test_an_unbound_grant_has_a_lifetime(ctx: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Artwork signed for nobody in particular is still not a URL that works for ever."""
    now = 1_900_000_000.0
    monkeypatch.setattr("app.services.image_grants.time.time", lambda: now)
    grant = image_payload("plex", "https://fixture.invalid/art", None, None)
    assert grant is not None
    # Good for one to two weeks, and the same URL all week so a browser can keep its copy.
    assert UNBOUND_TTL <= grant["x"] - now <= 2 * UNBOUND_TTL
    monkeypatch.setattr("app.services.image_grants.time.time", lambda: now + 3600)
    assert image_payload("plex", "https://fixture.invalid/art", None, None) == grant
    token = ctx.crypto.sign_json(grant)
    assert (await image_proxy(token, request(), ctx)).status_code == 200
    monkeypatch.setattr("app.services.image_grants.time.time", lambda: grant["x"] + 1)
    with pytest.raises(Forbidden):
        await image_proxy(token, request(), ctx)
    ctx.client.get.reset_mock()
    # One minted before grants had a lifetime, or with one made up, is refused before any fetch.
    monkeypatch.setattr("app.services.image_grants.time.time", lambda: now)
    for forged in (
        {"s": "plex", "u": "https://fixture.invalid/art"},
        {"s": "plex", "u": "https://fixture.invalid/art", "x": now + 10 * UNBOUND_TTL},
        {"s": "plex", "u": "https://fixture.invalid/art", "x": "never"},
        {"s": "plex", "u": "https://fixture.invalid/art", "x": True},
    ):
        with pytest.raises(Forbidden):
            await image_proxy(ctx.crypto.sign_json(forged), request(), ctx)
    ctx.client.get.assert_not_called()


async def test_grants_signed_for_one_viewer_before_are_refused(ctx: Any) -> None:
    """The earlier per-viewer grants (no week, bound to a session) expired within minutes anyway."""
    old = {
        "s": "plex",
        "u": "https://fixture.invalid/art",
        "profile": "kid",
        "version": 4,
        "session": "s",
        "ref": "lib:plex:17",
        "expires": 1e12,
    }
    with pytest.raises(Forbidden):
        await validate_image_grant(old)
    ctx.http_factory.assert_not_called()


async def test_listing_cache_shares_artwork_between_sessions(ctx: Any) -> None:
    from app.infra.result_cache import ResultCache
    from app.services.content_policy import active_policy
    from app.services.listing_cache import listing_cached

    db = SimpleNamespace(result_cache=ResultCache(), catalog_revisions=SimpleNamespace(version=lambda _: 1))
    load = AsyncMock(side_effect=[{"art": "the-one-image"}])
    policy_token = active_policy.set((ctx.content_policy, None))
    principal_token = active_principal.set(Principal("kid", False, True, 4, "one"))
    try:
        one = await listing_cached(db, "kid", "search", (), load)
        # The same profile's next session (a TV's next "Who's watching"): the same listing, same URLs.
        active_principal.set(Principal("kid", False, True, 4, "two"))
        assert await listing_cached(db, "kid", "search", (), load) == one
        assert load.await_count == 1
    finally:
        active_principal.reset(principal_token)
        active_policy.reset(policy_token)
