import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import respx

from app.services.errors import Forbidden, Gone, PreconditionRequired, Unauthorized
from app.services.owner_reauth import OwnerReauthService
from tests.conftest import Api
from tests.test_google_auth import configure, mock_google


@pytest.mark.parametrize("auth_time", [None, 0, True, float("inf")])
@respx.mock
async def test_stale_auth_time(api: Api, auth_time: Any) -> None:
    configure(api)
    g = api.c.google_auth
    url, browser = g.start_owner_reauth("challenge", time.time(), time.time() + 300)
    query = parse_qs(urlsplit(url).query)
    assert "auth_time" in query["claims"][0]
    mock_google(query["nonce"][0], auth_time=auth_time)
    with pytest.raises(Forbidden):
        await g.finish_owner_reauth(query["state"][0], browser, "code")
    assert not g.handoffs


@respx.mock
async def test_purpose_isolation(api: Api) -> None:
    configure(api)
    g = api.c.google_auth
    url, browser = g.start_owner_reauth("challenge", time.time(), time.time() + 300)
    query = parse_qs(urlsplit(url).query)
    state = query["state"][0]
    with pytest.raises(Unauthorized):
        await g.finish(state, browser, "code")
    mock_google(query["nonce"][0], auth_time=int(time.time()))
    assert await g.finish_owner_reauth(state, browser, "code") == "challenge"
    assert not g.handoffs
    with pytest.raises(Unauthorized):
        await g.finish_owner_reauth(state, browser, "code")


async def test_binding_cancel_expiry_and_single_use(api: Api) -> None:
    configure(api)
    service = OwnerReauthService(api.c.auth, api.c.google_auth)
    profile = await api.c.auth.get_profile(api.profile_id)
    p: Any = SimpleNamespace(profile_id=profile.id, token_version=profile.token_version, session_id="original")
    challenge = await service.start(p, profile.id)
    cid, secret = challenge["challenge_id"], challenge["poll_token"]
    assert secret not in challenge["verification_uri"]
    with pytest.raises(PreconditionRequired):
        await service.poll(p, cid, secret)
    with pytest.raises(Forbidden):
        await service.poll(p, cid, "wrong")
    other: Any = SimpleNamespace(profile_id=profile.id, token_version=profile.token_version, session_id="other")
    with pytest.raises(Forbidden):
        await service.poll(other, cid, secret)
    service.challenges[cid].status = "approved"
    result, tokens = await service.poll(p, cid, secret)
    assert result.id == profile.id and tokens["access_token"]
    with pytest.raises(Gone):
        await service.poll(p, cid, secret)
    p.session_id = "replacement"
    challenge = await service.start(p, profile.id)
    cid, secret = challenge["challenge_id"], challenge["poll_token"]
    await service.cancel(p, cid, secret)
    with pytest.raises(Forbidden):
        await service.poll(p, cid, secret)
    service.challenges[cid].expires = time.time() - 1
    with pytest.raises(Gone):
        await service.poll(p, cid, secret)


@pytest.mark.parametrize("auth_time", [None, 0, True, float("inf"), time.time() + 600])
@respx.mock
async def test_normal_owner_login_policy_requires_fresh_proof(api: Api, auth_time: Any) -> None:
    configure(api)
    object.__setattr__(api.c.settings, "content_policy_enabled", True)
    # An unauthenticated browser after app storage clearing must still provide fresh Google proof.
    api.client.cookies.clear()
    g = api.c.google_auth
    url, browser = g.start("/")
    query = parse_qs(urlsplit(url).query)
    assert len(query["claims"]) == 1 and "auth_time" in query["claims"][0]
    assert query["max_age"] == ["0"]
    mock_google(query["nonce"][0], auth_time=auth_time)
    with pytest.raises(Forbidden):
        await g.finish(query["state"][0], browser, "code")
    assert not g.handoffs


@respx.mock
async def test_normal_owner_proof_survives_handoff(api: Api) -> None:
    configure(api)
    object.__setattr__(api.c.settings, "content_policy_enabled", True)
    g = api.c.google_auth
    url, browser = g.start("/")
    query = parse_qs(urlsplit(url).query)
    authenticated = int(time.time())
    mock_google(query["nonce"][0], auth_time=authenticated)
    handoff, destination = await g.finish(query["state"][0], browser, "code")
    assert destination == "/"
    profile = await g.consume(handoff, g.origin)
    assert getattr(profile, "_owner_auth_time", None) == authenticated
    assert getattr(await api.c.auth.get_profile(profile.id), "_owner_auth_time", None) is None
    with pytest.raises(Unauthorized):
        await g.consume(handoff, g.origin)
