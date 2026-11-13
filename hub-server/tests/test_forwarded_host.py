"""X-Forwarded-Host, -Proto and -Prefix shape the URLs the server hands out and the cookies it
sets, so they are read only from a proxy named in FORWARDED_ALLOW_IPS."""

from __future__ import annotations

from urllib.parse import parse_qs

import respx
from httpx import ASGITransport, AsyncClient

from tests.conftest import Api
from tests.plex_identity import mock_plex

SPOOF = {"X-Forwarded-Host": "evil.example.test", "X-Forwarded-Proto": "https", "X-Forwarded-Prefix": "/x"}


async def forward_url(api: Api, peer: str, headers: dict[str, str], port: int = 40000) -> tuple[str, str]:
    app = api.client._transport.app  # type: ignore[attr-defined]
    async with AsyncClient(transport=ASGITransport(app=app, client=(peer, port)), base_url="http://hub.test") as c:
        r = await c.post("/api/v1/auth/plex/start", json={}, headers=headers)
    assert r.status_code == 200, r.text
    return parse_qs(r.json()["auth_url"].partition("#?")[2])["forwardUrl"][0], r.headers["set-cookie"]


@respx.mock
async def test_forwarded_host_and_proto_are_only_a_trusted_proxys_to_set(api: Api) -> None:
    mock_plex()
    # Anyone else writing them changes nothing: the URL is built from the connection itself.
    for peer in ("203.0.113.9", "192.168.1.20", "172.17.0.5"):
        url, cookie = await forward_url(api, peer, SPOOF)
        assert url.startswith("http://hub.test/?plex="), (peer, url)
        assert cookie.startswith("sh-plex-state=") and "Secure" not in cookie
    # The trusted proxy (127.0.0.1 by default) is believed.
    url, cookie = await forward_url(api, "127.0.0.1", SPOOF)
    assert url.startswith("https://evil.example.test/x/?plex=")
    assert cookie.startswith("__Host-sh-plex-state=") and "Secure" in cookie
    # So is the peer uvicorn --proxy-headers wrote in a trusted proxy's place (port 0).
    url, _ = await forward_url(api, "203.0.113.9", SPOOF, port=0)
    assert url.startswith("https://evil.example.test/x/?plex=")
    api.c.settings.forwarded_allow_ips = ""
    url, _ = await forward_url(api, "127.0.0.1", SPOOF)
    assert url.startswith("http://hub.test/?plex=")
