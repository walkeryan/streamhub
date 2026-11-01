"""No network/real credentials: signed synthetic Google ID tokens + mocked OAuth endpoints."""

import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.errors import Forbidden, Unauthorized
from tests.conftest import Api


def configure(api: Api) -> None:
    s = api.c.settings
    s.google_client_id = "test-client"
    s.google_client_secret = "test-secret-not-real"
    s.google_allowed_emails = "owner@example.test"
    s.google_auth_required = True
    api.client.base_url = "https://streamhub.ryan-walker.dev"


def mock_google(expected_nonce: str, **overrides: object) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "test-key"
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "test-client",
        "sub": "subject-123",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "nonce": expected_nonce,
        "email": "owner@example.test",
        "email_verified": True,
        **overrides,
    }
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    respx.post("https://oauth2.googleapis.com/token").mock(return_value=httpx.Response(200, json={"id_token": token}))
    respx.get("https://www.googleapis.com/oauth2/v3/certs").mock(return_value=httpx.Response(200, json={"keys": [jwk]}))


async def begin(api: Api) -> tuple[str, str]:
    r = await api.get("/auth/google/start?return_to=/pair")
    assert r.status_code == 303
    query = parse_qs(urlsplit(r.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://streamhub.ryan-walker.dev/api/v1/auth/google/callback"]
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    return query["state"][0], query["nonce"][0]


async def test_missing_configuration_fails_closed(api: Api) -> None:
    api.c.settings.google_auth_required = True
    assert (await api.get("/auth/config")).json() == {
        "google_enabled": False,
        "google_required": True,
        "content_policy_enabled": False,
    }
    assert (await api.get("/auth/google/start")).status_code == 403
    assert (
        await api.post("/auth/login", json={"profile_id": api.profile_id, "password": "test-admin-pw"})
    ).status_code == 403
    assert (await api.post("/auth/google/complete")).status_code == 403


async def test_state_browser_binding_and_replay(api: Api) -> None:
    configure(api)
    state, _ = await begin(api)
    with pytest.raises(Unauthorized):
        await api.c.google_auth.finish(state, "wrong-browser", "code")
    with pytest.raises(Unauthorized):
        await api.c.google_auth.finish(state, "wrong-browser", "code")
    r = await api.get("/auth/google/callback?state=unknown&code=code")
    assert r.headers["location"] == "/?google_error=1"
    assert "code=" not in r.headers["location"]


@respx.mock
async def test_verified_google_owner_and_tv_pairing(api: Api) -> None:
    configure(api)
    state, nonce = await begin(api)
    mock_google(nonce)
    r = await api.get(f"/auth/google/callback?state={state}&code=synthetic-code")
    assert r.status_code == 303 and r.headers["location"] == "/pair?google_complete=1"
    assert "access_token" not in r.headers["location"]
    # The handoff cannot be consumed cross-origin, nor by a GET, but remains usable by the browser.
    assert (await api.post("/auth/google/complete", headers={"Origin": "https://evil.test"})).status_code == 403
    r = await api.post("/auth/google/complete", headers={"Origin": "https://streamhub.ryan-walker.dev"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["profile"]["id"] == api.profile_id
    token = r.json()["access_token"]
    assert (
        await api.post("/auth/google/complete", headers={"Origin": "https://streamhub.ryan-walker.dev"})
    ).status_code == 401
    device = (await api.post("/auth/device")).json()
    r = await api.post(
        "/auth/device/approve",
        json={"user_code": device["user_code"], "profile_id": api.profile_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204, r.text
    paired = await api.post("/auth/device/poll", json={"device_code": device["device_code"]})
    assert paired.status_code == 200, paired.text
    refreshed = await api.post("/auth/refresh", json={"refresh_token": paired.json()["refresh_token"]})
    assert refreshed.status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"email": "other@example.test"},
        {"email_verified": False},
        {"email_verified": "true"},
        {"nonce": "wrong"},
        {"aud": "wrong"},
        {"iss": "https://evil.test"},
        {"exp": 1},
        {"azp": "wrong"},
    ],
)
@respx.mock
async def test_untrusted_identity_rejected(api: Api, overrides: dict[str, object]) -> None:
    configure(api)
    state, nonce = await begin(api)
    mock_google(nonce, **overrides)
    r = await api.get(f"/auth/google/callback?state={state}&code=synthetic-code")
    assert r.headers["location"] == "/?google_error=1"
    assert not api.c.google_auth.handoffs


async def test_expired_state_and_handoff_no_open_redirect(api: Api) -> None:
    configure(api)
    url, browser = api.c.google_auth.start("https://evil.test")
    state = parse_qs(urlsplit(url).query)["state"][0]
    tx = api.c.google_auth.transactions[state]
    assert tx[4] == "/"
    api.c.google_auth.transactions[state] = (0, *tx[1:])
    with pytest.raises(Unauthorized):
        await api.c.google_auth.finish(state, browser, "code")
    api.c.google_auth.handoffs["expired"] = (0, api.profile_id, "/", None)
    with pytest.raises(Unauthorized):
        await api.c.google_auth.consume("expired", "https://streamhub.ryan-walker.dev")
    with pytest.raises(Forbidden):
        await api.c.google_auth.consume("expired", "")


async def test_required_mode_blocks_passwordless_profiles_and_pair_escalation(api: Api) -> None:
    child = (await api.post("/profiles", json={"name": "Child"})).json()
    child_profile = await api.c.auth.get_profile(child["id"])
    token = api.c.auth.issue(child_profile)["access_token"]
    configure(api)
    assert (await api.client.get("/api/v1/profiles")).status_code == 401
    assert (await api.get("/profiles")).status_code == 200
    assert (await api.client.post("/api/v1/auth/login", json={"profile_id": child["id"]})).status_code == 403
    device = (await api.post("/auth/device")).json()
    response = await api.post(
        "/auth/device/approve",
        json={"user_code": device["user_code"], "profile_id": api.profile_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert (await api.post("/auth/device/poll", json={"device_code": device["device_code"]})).status_code == 428


@respx.mock
async def test_wrong_signature_rejected(api: Api) -> None:
    configure(api)
    state, nonce = await begin(api)
    mock_google(nonce)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(other.public_key()))
    jwk["kid"] = "test-key"
    respx.get("https://www.googleapis.com/oauth2/v3/certs").mock(return_value=httpx.Response(200, json={"keys": [jwk]}))
    response = await api.get(f"/auth/google/callback?state={state}&code=synthetic-code")
    assert response.headers["location"] == "/?google_error=1"
    assert not api.c.google_auth.handoffs


def test_oauth_logs_are_redacted() -> None:
    from app.infra.logging import redact_text, redact_url

    url = "https://streamhub.ryan-walker.dev/api/v1/auth/google/callback?code=private-code&state=private-state"
    for redacted in (redact_text(url), redact_url(url)):
        assert redacted is not None and "private-code" not in redacted and "private-state" not in redacted
    assert "private-secret" not in redact_text("https://example.test/?client_secret=private-secret")


@respx.mock
async def test_pairing_code_survives_google_without_open_redirect(api: Api) -> None:
    configure(api)
    r = await api.get("/auth/google/start", params={"return_to": "/pair?code=ABC123"})
    query = parse_qs(urlsplit(r.headers["location"]).query)
    assert "ABC123" not in r.headers["location"]
    mock_google(query["nonce"][0])
    r = await api.get("/auth/google/callback", params={"state": query["state"][0], "code": "synthetic-code"})
    assert r.headers["location"] == "/pair?code=ABC123&google_complete=1"
    for destination in ("//evil.test", "/pair?code=ABC123&next=https://evil.test", "/pair?code=<script>"):
        url, _ = api.c.google_auth.start(destination)
        state = parse_qs(urlsplit(url).query)["state"][0]
        assert api.c.google_auth.transactions[state][4] == "/"
