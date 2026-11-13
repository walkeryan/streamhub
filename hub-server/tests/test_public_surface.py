"""What someone on the internet who is not signed in can reach, and what they are told."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.container import build_container
from app.infra.cache import MemoryCache
from app.infra.settings import AppSettings
from app.main import create_app
from tests.conftest import ADMIN_PASSWORD, Api, http_factory
from tests.people import member
from tests.plex_identity import mock_plex
from tests.test_password_signin import GOOD, person
from tests.test_remote_access import AWAY, HOME, PROXY, TUNNEL, from_address


@pytest.fixture
def settings(settings: AppSettings) -> AppSettings:
    return settings.model_copy(update={"forwarded_allow_ips": "127.0.0.1"})


class Clock:
    def __init__(self) -> None:
        self.now = 50_000.0

    def __call__(self) -> float:
        return self.now


async def ask(
    api: Api, peer: str, method: str, path: str, *, forwarded: str | None = None, **kw: Any
) -> httpx.Response:
    headers = dict(kw.pop("headers", {}))
    if forwarded is not None:
        headers["X-Forwarded-For"] = forwarded
    async with from_address(api, peer) as client:
        return await client.request(method, "/api/v1" + path, headers=headers, **kw)


# ---------------------------------------------------------------- the household convenience
async def test_profile_tiles_and_profile_login_are_for_the_home_network(api: Api) -> None:
    kid = await member(api, "kid", "Kid")
    login = {"profile_id": api.profile_id, "password": ADMIN_PASSWORD}
    for peer, forwarded in ((HOME, None), ("127.0.0.1", None), (PROXY, HOME)):
        listed = await ask(api, peer, "GET", "/profiles", forwarded=forwarded)
        assert listed.status_code == 200 and {p["name"] for p in listed.json()} == {"Admin", "Kid"}
        assert (await ask(api, peer, "POST", "/auth/login", json=login, forwarded=forwarded)).status_code == 200
        config = (await ask(api, peer, "GET", "/auth/config", forwarded=forwarded)).json()
        assert config["profiles_enabled"] is True

    # From anywhere else: no list, and one answer whatever is asked, so nothing is learned.
    for peer, forwarded in ((AWAY, None), (AWAY, HOME), (PROXY, AWAY), (TUNNEL, AWAY), (TUNNEL, HOME), (PROXY, "junk")):
        listed = await ask(api, peer, "GET", "/profiles", forwarded=forwarded)
        assert (listed.status_code, listed.json()["code"]) == (404, "not_found"), (peer, forwarded)
        assert "Admin" not in listed.text and "Kid" not in listed.text
        answers = set()
        for body in (
            login,
            {"profile_id": api.profile_id, "password": "wrong"},
            {"profile_id": kid.profile_id},
            {"profile_id": "no-such-profile", "pin": "1234"},
        ):
            r = await ask(api, peer, "POST", "/auth/login", json=body, forwarded=forwarded)
            answers.add((r.status_code, r.text))
        assert len(answers) == 1 and next(iter(answers))[0] == 403, (peer, forwarded, answers)
        config = (await ask(api, peer, "GET", "/auth/config", forwarded=forwarded)).json()
        assert config["profiles_enabled"] is False
    # Signed in and away: one's own profile, not the household's.
    mine = await ask(api, AWAY, "GET", "/profiles", headers=api.headers)
    assert mine.status_code == 200 and [p["id"] for p in mine.json()] == [api.profile_id]
    everyone = await ask(api, HOME, "GET", "/profiles", headers=api.headers)
    assert len(everyone.json()) == 2


async def test_google_required_keeps_them_closed_even_at_home(api: Api) -> None:
    api.c.settings.google_auth_required = True
    for peer in (HOME, "127.0.0.1", AWAY):
        assert (await ask(api, peer, "GET", "/profiles")).status_code == 401
        r = await ask(api, peer, "POST", "/auth/login", json={"profile_id": api.profile_id, "password": ADMIN_PASSWORD})
        assert r.status_code == 403
        assert (await ask(api, peer, "GET", "/auth/config")).json()["profiles_enabled"] is False


async def test_a_tv_pairs_from_anywhere_through_a_signed_in_browser(api: Api) -> None:
    started = await ask(api, AWAY, "POST", "/auth/device")
    assert started.status_code == 200, started.text
    code = started.json()
    poll = {"device_code": code["device_code"]}
    assert (await ask(api, AWAY, "POST", "/auth/device/poll", json=poll)).status_code == 428
    approved = await ask(
        api, AWAY, "POST", "/auth/device/approve", json={"user_code": code["user_code"]}, headers=api.headers
    )
    assert approved.status_code == 204, approved.text
    tokens = await ask(api, AWAY, "POST", "/auth/device/poll", json=poll)
    assert tokens.status_code == 200 and tokens.json()["profile"]["id"] == api.profile_id


async def test_the_other_ways_in_work_from_anywhere(api: Api) -> None:
    await person(api)
    r = await ask(api, AWAY, "POST", "/auth/password", json={"username": "gran", "password": GOOD})
    assert r.status_code == 200
    fresh = await ask(api, AWAY, "POST", "/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})
    assert fresh.status_code == 200


# ---------------------------------------------------------------- rate limits
@respx.mock
@pytest.mark.parametrize(
    ("bucket", "method", "path", "body"),
    [
        ("oauth", "GET", "/auth/google/start", None),
        ("oauth", "GET", "/auth/google/callback?state=x&code=y", None),
        ("oauth", "POST", "/auth/google/complete", None),
        ("plex", "POST", "/auth/plex/start", {}),
        ("plex", "POST", "/auth/plex/complete", {"state": "x"}),
        ("device", "POST", "/auth/device", None),
        ("device_poll", "POST", "/auth/device/poll", {"device_code": "x"}),
        ("invite", "POST", "/auth/invite/lookup", {"token": "x"}),
        ("invite", "POST", "/auth/invite/accept", {"token": "x", "username": "someone", "password": GOOD}),
        ("password", "POST", "/auth/password", {"username": "nobody", "password": "x"}),
        ("login", "POST", "/auth/login", {"profile_id": "x", "pin": "0000"}),
        ("refresh", "POST", "/auth/refresh", {"refresh_token": "x"}),
        ("profiles", "GET", "/profiles", None),
        ("qr", "GET", "/qr?data=hello", None),
    ],
)
async def test_every_unauthenticated_auth_route_is_limited_per_address(
    api: Api, bucket: str, method: str, path: str, body: dict[str, Any] | None
) -> None:
    clock = Clock()
    api.c.limiter.clock = clock
    api.c.passwords.throttle.clock = clock
    mock_plex()
    limit, window = api.c.limiter.limits[bucket]
    api.c.limiter.limits[bucket] = (limit := min(limit, 4), window)
    kw = {"json": body} if body is not None else {}
    seen = [(await ask(api, "198.51.100.7", method, path, **kw)).status_code for _ in range(limit)]
    assert 429 not in seen, seen
    over = await ask(api, "198.51.100.7", method, path, **kw)
    assert over.status_code == 429 and over.json()["code"] == "rate_limited", (seen, over.text)
    assert 1 <= int(over.headers["retry-after"]) <= window
    # Another address has its own allowance, and this one gets it back when the window has passed.
    assert (await ask(api, "198.51.100.8", method, path, **kw)).status_code != 429
    clock.now += window + 1
    assert (await ask(api, "198.51.100.7", method, path, **kw)).status_code != 429


async def test_the_limit_follows_the_client_behind_a_trusted_proxy(api: Api) -> None:
    api.c.limiter.limits["device"] = (2, 60)
    for _ in range(2):
        assert (await ask(api, PROXY, "POST", "/auth/device", forwarded="198.51.100.7")).status_code == 200
    assert (await ask(api, PROXY, "POST", "/auth/device", forwarded="198.51.100.7")).status_code == 429
    assert (await ask(api, PROXY, "POST", "/auth/device", forwarded="198.51.100.8")).status_code == 200
    # A header from anyone else does not buy a fresh allowance.
    for n in range(2):
        assert (await ask(api, AWAY, "POST", "/auth/device", forwarded=f"198.51.100.{20 + n}")).status_code == 200
    assert (await ask(api, AWAY, "POST", "/auth/device", forwarded="198.51.100.99")).status_code == 429


# ---------------------------------------------------------------- tokens stay out of URLs
async def test_access_and_refresh_tokens_are_not_read_from_the_query_string(api: Api) -> None:
    token = api.headers["Authorization"].removeprefix("Bearer ")
    r = await api.client.post("/api/v1/auth/login", json={"profile_id": api.profile_id, "password": ADMIN_PASSWORD})
    refresh = r.json()["refresh_token"]
    for name in ("access_token", "token", "authorization", "bearer", "jwt"):
        for path in ("/favorites", "/auth/me", "/home", "/users", "/settings"):
            got = await api.client.get(f"/api/v1{path}", params={name: token})
            assert got.status_code == 401, (name, path)
    for name in ("refresh_token", "token"):
        got = await api.client.post("/api/v1/auth/refresh", params={name: refresh}, json={})
        assert got.status_code == 401, name


# ---------------------------------------------------------------- API docs
async def test_api_docs_are_off_unless_asked_for(api: Api, settings: AppSettings, tmp_path: Path) -> None:
    for path in ("/api/docs", "/api/openapi.json", "/docs", "/redoc", "/openapi.json", "/api/redoc"):
        r = await api.client.get(path)
        assert r.status_code == 404 and "openapi" not in r.text.lower(), path
    assert "/api/docs" not in (await api.client.get("/")).text
    on = settings.model_copy(update={"api_docs_enabled": True, "config_dir": tmp_path / "docs-on"})
    app = create_app(
        on, container=build_container(on, http_factory=http_factory, cache=MemoryCache()), start_background=False
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://hub.test") as c,
    ):
        assert (await c.get("/api/docs")).status_code == 200
        assert "paths" in (await c.get("/api/openapi.json")).json()


# ---------------------------------------------------------------- response headers
EVERYWHERE = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "x-frame-options": "DENY",
}


async def test_every_response_carries_the_security_headers(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = api.c.settings.web_dist
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "a.js").write_text("console.log(1)")
    answers = [
        await api.client.get("/api/v1/health"),
        await api.client.get("/api/v1/favorites"),  # 401
        await api.client.get("/api/v1/nope"),  # 404
        await api.client.post("/api/v1/auth/password", json={"username": 7}),  # 422
        await api.client.post("/api/v1/auth/login", json={"profile_id": "x"}),  # refused
        await api.get("/settings"),
        await api.client.get("/api/v1/qr", params={"data": "x"}),
        await api.client.options("/api/v1/health"),
        await api.client.get("/"),
        await api.client.get("/live/guide"),
        await api.client.get("/assets/a.js"),
    ]

    async def broken() -> list[dict[str, Any]]:
        raise RuntimeError("boom")

    monkeypatch.setattr(api.c.themes, "list", broken)
    app = api.client._transport.app  # type: ignore[attr-defined]
    quiet = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=quiet, base_url="http://hub.test") as client:
        crashed = await client.get("/api/v1/themes")
    assert crashed.status_code == 500
    for r in [*answers, crashed]:
        for name, value in EVERYWHERE.items():
            assert r.headers.get(name) == value, (r.request.url.path, r.status_code, name)
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"], r.request.url.path
        assert "strict-transport-security" not in r.headers, r.request.url.path
    # A flow that asked for a stricter referrer policy keeps it.
    callback = await api.client.get("/api/v1/auth/google/callback", params={"state": "x", "code": "y"})
    assert callback.headers["referrer-policy"] == "no-referrer" and callback.headers["x-frame-options"] == "DENY"


async def test_the_web_app_gets_a_content_security_policy_it_can_run_under(api: Api) -> None:
    dist = api.c.settings.web_dist
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    for path in ("/", "/live/guide", "/invite/" + "A" * 43, "/settings"):
        policy = (await api.client.get(path)).headers["content-security-policy"]
        rules = {part.split()[0]: part.split()[1:] for part in policy.split(";") if part.strip()}
        assert rules["default-src"] == ["'self'"] and rules["script-src"] == ["'self'"]
        assert rules["object-src"] == ["'none'"] and rules["frame-ancestors"] == ["'none'"]
        assert rules["base-uri"] == ["'self'"] and rules["form-action"] == ["'self'"]
        # What the app really loads: posters and logos from anywhere, the QR code and icons as
        # data: URLs, HLS through MSE (blob:) with its worker, the room socket.
        assert {"'self'", "data:", "blob:", "https:"} <= set(rules["img-src"])
        assert {"'self'", "blob:"} <= set(rules["media-src"]) and {"'self'", "blob:"} <= set(rules["worker-src"])
        assert {"'self'", "ws:", "wss:"} <= set(rules["connect-src"])
        assert "'unsafe-eval'" not in policy and "'unsafe-inline'" not in " ".join(rules["script-src"])
    # The API's own answers are not pages: they only refuse to be framed.
    assert (await api.client.get("/api/v1/health")).headers["content-security-policy"] == "frame-ancestors 'none'"


async def test_hsts_only_when_a_trusted_proxy_says_https(api: Api) -> None:
    said = {"X-Forwarded-Proto": "https"}
    async with from_address(api, PROXY) as client:
        secure = await client.get("/api/v1/health", headers=said)
        plain = await client.get("/api/v1/health")
    assert secure.headers["strict-transport-security"] == "max-age=31536000"
    assert "strict-transport-security" not in plain.headers
    for peer in (AWAY, HOME, TUNNEL):
        async with from_address(api, peer) as client:
            assert "strict-transport-security" not in (await client.get("/api/v1/health", headers=said)).headers


# ---------------------------------------------------------------- /auth/config
async def test_auth_config_says_what_the_sign_in_screen_needs_and_no_more(api: Api) -> None:
    await person(api)
    config = (await ask(api, AWAY, "GET", "/auth/config")).json()
    assert config == {
        "google_enabled": False,
        "google_required": False,
        "plex_enabled": True,
        "password_enabled": True,
        "profiles_enabled": False,
        "signup_mode": "approval",
        "content_policy_enabled": False,
        "owner_pin_configured": False,
        "owner_pin_setup_required": True,
    }
