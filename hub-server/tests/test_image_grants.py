from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.api.routers.playback import image_proxy
from app.infra.crypto import Crypto
from app.services.auth import Principal
from app.services.content_policy import active_principal
from app.services.errors import Forbidden
from app.services.image_grants import image_payload, validate_image_grant


@pytest.fixture
def ctx() -> Any:
    policy = SimpleNamespace(enabled=True, require=AsyncMock())
    ptoken = active_principal.set(Principal("kid", False, True, 4, "kid-session"))
    payload = image_payload("plex", "https://fixture.invalid/art", "lib:plex:17", policy)
    active_principal.reset(ptoken)
    assert payload is not None
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
    )


async def test_exact_library_and_session_validated(ctx: Any) -> None:
    await validate_image_grant(ctx.payload, ctx.content_policy, ctx.auth)
    ctx.auth.session_valid.assert_awaited_once_with("kid", 4, "kid-session")
    ctx.content_policy.require.assert_awaited_once_with("kid", "lib:plex:17")


@pytest.mark.parametrize(
    "change",
    [
        {"session": None},
        {"version": True},
        {"expires": 0},
        {"expires": float("nan")},
        {"ref": "lib:other:17"},
        {"ref": "tmdb:movie:17"},
    ],
)
async def test_invalid_binding_rejected(ctx: Any, change: dict[str, Any]) -> None:
    with pytest.raises(Forbidden):
        await validate_image_grant({**ctx.payload, **change}, ctx.content_policy, ctx.auth)
    ctx.auth.session_valid.assert_not_called()


async def test_legacy_rejected_before_fetch(ctx: Any) -> None:
    token = ctx.crypto.sign_json({"s": "plex", "u": "https://fixture.invalid/art"})
    with pytest.raises(Forbidden):
        await image_proxy(token, ctx)
    ctx.sources.provider_for.assert_not_called()
    ctx.http_factory.assert_not_called()


async def test_revoked_session_rejected_before_fetch(ctx: Any) -> None:
    ctx.auth.session_valid.side_effect = Forbidden("revoked")
    with pytest.raises(Forbidden):
        await image_proxy(ctx.crypto.sign_json(ctx.payload), ctx)
    ctx.http_factory.assert_not_called()


async def test_policy_change_while_fetching_rejected(ctx: Any) -> None:
    ctx.content_policy.require.side_effect = [None, Forbidden("content restricted")]
    with pytest.raises(Forbidden):
        await image_proxy(ctx.crypto.sign_json(ctx.payload), ctx)
    assert ctx.auth.session_valid.await_count == 2


async def test_enabled_artwork_private_and_rechecked(ctx: Any) -> None:
    response = await image_proxy(ctx.crypto.sign_json(ctx.payload), ctx)
    assert response.body == b"fixture-image"
    assert response.headers["cache-control"] == "private, no-store"
    assert ctx.auth.session_valid.await_count == 2


async def test_disabled_preserves_legacy_artwork(ctx: Any) -> None:
    ctx.content_policy.enabled = False
    token = ctx.crypto.sign_json({"s": "plex", "u": "https://fixture.invalid/art"})
    response = await image_proxy(token, ctx)
    assert response.headers["cache-control"] == "public, max-age=86400"
    ctx.auth.session_valid.assert_not_called()


def test_enabled_mint_without_principal_or_exact_ref_omits_artwork(ctx: Any) -> None:
    assert image_payload("plex", "https://fixture.invalid/art", "lib:plex:17", ctx.content_policy) is None
    token = active_principal.set(Principal("kid", False, True, 4, "session"))
    try:
        assert image_payload("plex", "url", None, ctx.content_policy) is None
        assert image_payload("plex", "url", "lib:other:17", ctx.content_policy) is None
    finally:
        active_principal.reset(token)


async def test_listing_cache_does_not_share_session_bound_images(ctx: Any) -> None:
    from app.infra.result_cache import ResultCache
    from app.services.content_policy import active_policy
    from app.services.listing_cache import listing_cached

    db = SimpleNamespace(result_cache=ResultCache(), catalog_revisions=SimpleNamespace(version=lambda _: 1))
    load = AsyncMock(side_effect=[{"art": "session-one-image"}, {"art": "session-two-image"}])
    policy_token = active_policy.set((ctx.content_policy, None))
    principal_token = active_principal.set(Principal("kid", False, True, 4, "one"))
    try:
        one = await listing_cached(db, "kid", "search", (), load)
        assert await listing_cached(db, "kid", "search", (), load) == one
        active_principal.set(Principal("kid", False, True, 4, "two"))
        two = await listing_cached(db, "kid", "search", (), load)
        assert two != one
        assert load.await_count == 2
    finally:
        active_principal.reset(principal_token)
        active_policy.reset(policy_token)


def test_catalog_factory_carries_exact_reference(ctx: Any) -> None:
    from app.services.catalog import CatalogService

    src = SimpleNamespace(id="plex")
    provider = SimpleNamespace(image_headers=lambda _: {})
    catalog = CatalogService(None, ctx.crypto, SimpleNamespace(provider=lambda _: provider), None, None)
    catalog.content_policy = ctx.content_policy
    token = active_principal.set(Principal("kid", False, True, 4, "kid-session"))
    try:
        image = catalog.image_fn({"plex": src})
        url = image(src, "https://fixture.invalid/art", "lib:plex:17")
        assert url is not None
        payload = ctx.crypto.verify_json(url.rsplit("/", 1)[1])
        assert payload["ref"] == "lib:plex:17"
        assert payload["session"] == "kid-session"
        assert image(src, "https://fixture.invalid/art", None) is None
    finally:
        active_principal.reset(token)
