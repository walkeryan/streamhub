"""``remote.access``: without it a user is served on the home network only, and the client's
address is never taken from a header an untrusted peer wrote."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.infra.models import AuthSession
from app.infra.settings import AppSettings
from app.services.network import DEFAULT_HOME_NETWORKS, clean_networks, client_address, is_home
from tests.conftest import Api
from tests.people import Member, member

HOME, AWAY, PROXY = "192.168.1.20", "203.0.113.9", "127.0.0.1"


@pytest.fixture
def settings(settings: AppSettings) -> AppSettings:
    return settings.model_copy(update={"forwarded_allow_ips": "127.0.0.1"})


# ---------------------------------------------------------------- the helper
def test_forwarded_for_counts_only_from_a_trusted_proxy() -> None:
    trust = "127.0.0.1,172.18.0.0/16"
    assert client_address(AWAY, None, trust) == AWAY
    # An untrusted peer may write whatever it likes in the header; it is not read.
    assert client_address(AWAY, HOME, trust) == AWAY
    assert client_address(HOME, AWAY, trust) == HOME
    # A trusted proxy's word is taken, for the hop it appended and no further left.
    assert client_address(PROXY, AWAY, trust) == AWAY
    assert client_address(PROXY, f"{HOME}, {AWAY}", trust) == AWAY
    assert client_address("172.18.0.2", f"{HOME},{AWAY}", trust) == AWAY
    # Trusted proxies in a chain are stepped over.
    assert client_address(PROXY, f"{AWAY}, 172.18.0.3", trust) == AWAY
    assert client_address(PROXY, f"{HOME}, {AWAY}, 172.18.0.3, 127.0.0.1", trust) == AWAY
    assert client_address(PROXY, "172.18.0.3, 127.0.0.1", trust) == "172.18.0.3"
    assert client_address(PROXY, "", trust) == PROXY
    # Ports, brackets, IPv6 and IPv4-mapped addresses.
    assert client_address(PROXY, "198.51.100.7:4711", trust) == "198.51.100.7"
    assert client_address(PROXY, "[2001:db8::7]:4711", trust) == "2001:db8::7"
    assert client_address("::ffff:127.0.0.1", AWAY, trust) == AWAY
    assert client_address("::1", AWAY, "127.0.0.1,::1") == AWAY
    assert client_address("2001:db8::1", HOME, trust) == "2001:db8::1"
    # What cannot be known is unknown, never a guess.
    assert client_address(PROXY, f"{HOME}, not-an-address", trust) is None
    assert client_address(None, HOME, trust) is None and client_address("testclient", None, trust) is None
    # No trusted proxies at all; and uvicorn's "trust everyone".
    assert client_address(PROXY, AWAY, "") == PROXY
    assert client_address(AWAY, f"{HOME}, 198.51.100.7", "*") == HOME
    assert client_address(None, HOME, "*") == HOME and client_address(AWAY, None, "*") == AWAY
    assert client_address(PROXY, AWAY, "/run/hub.sock, 127.0.0.1") == AWAY


def test_home_networks() -> None:
    for inside in ("10.1.2.3", "172.16.0.1", "172.31.255.254", "192.168.0.9", "127.0.0.1", "169.254.10.1"):
        assert is_home(inside, DEFAULT_HOME_NETWORKS), inside
    for inside in ("100.64.0.1", "100.127.255.254", "::1", "fd00::5", "fe80::1", "::ffff:192.168.1.4"):
        assert is_home(inside, DEFAULT_HOME_NETWORKS), inside
    for outside in (AWAY, "172.32.0.1", "100.128.0.1", "198.51.100.7", "2001:db8::1", None, "", "nonsense"):
        assert not is_home(outside, DEFAULT_HOME_NETWORKS), outside
    assert is_home("198.51.100.7", "198.51.100.0/24") and not is_home(HOME, "198.51.100.0/24")
    assert is_home(HOME, "") and is_home(HOME, "garbage")  # a broken setting falls back to the defaults
    assert clean_networks(" 192.168.1.0/24 , 198.51.100.7,2001:db8::/32 ") == (
        "192.168.1.0/24,198.51.100.7/32,2001:db8::/32"
    )
    for bad in ("", " , ", "192.168.1.0/33", "home", "192.168.1.0/24;10.0.0.0/8", None):
        with pytest.raises(ValueError):
            clean_networks(bad)


# ---------------------------------------------------------------- over HTTP
@asynccontextmanager
async def from_address(api: Api, peer: str) -> AsyncIterator[AsyncClient]:
    """A client whose connection comes from ``peer``."""
    app = api.client._transport.app  # type: ignore[attr-defined]
    async with AsyncClient(transport=ASGITransport(app=app, client=(peer, 40000)), base_url="http://hub.test") as c:
        yield c


async def get(api: Api, who: Member | Api, peer: str, url: str = "/favorites", forwarded: str | None = None) -> Any:
    headers = dict(who.headers)
    if forwarded is not None:
        headers["X-Forwarded-For"] = forwarded
    async with from_address(api, peer) as client:
        return await client.get("/api/v1" + url, headers=headers)


def denied(r: Any) -> bool:
    return bool(r.status_code == 403 and r.json()["code"] == "remote_access_denied")


async def test_without_remote_access_only_the_home_network_is_served(api: Api) -> None:
    kid = await member(api, "kid", "Kid")
    assert "remote.access" not in (await kid.get("/auth/me")).json()["permissions"]
    assert (await get(api, kid, HOME)).status_code == 200
    assert (await get(api, kid, "100.64.3.2")).status_code == 200
    for url in (
        "/favorites",
        "/home",
        "/settings",
        "/auth/sessions",
        "/playback/options?id=ch:x:1",
        "/profiles/x/prefs",
    ):
        r = await get(api, kid, AWAY, url)
        assert denied(r), (url, r.status_code, r.text)
    r = await api.client.post("/api/v1/playback/resolve", json={"id": "ch:x:1"}, headers=kid.headers)
    assert r.status_code != 403 or r.json()["code"] != "remote_access_denied"
    async with from_address(api, AWAY) as client:
        r = await client.post("/api/v1/playback/resolve", json={"id": "ch:x:1"}, headers=kid.headers)
        assert denied(r)
        # The one call that still answers, so the client can say who is signed in and why nothing loads.
        me = await client.get("/api/v1/auth/me", headers=kid.headers)
        assert me.status_code == 200 and me.json()["user"]["id"] == kid.id
        # Signing in and refreshing are not the gate; using the session is.
        fresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": kid.tokens["refresh_token"]})
        assert fresh.status_code == 200
        again = {"Authorization": f"Bearer {fresh.json()['access_token']}"}
        assert denied(await client.get("/api/v1/favorites", headers=again))


async def test_with_remote_access_anywhere_is_fine(api: Api) -> None:
    parent = await member(api, "family", "Parent")
    friend = await member(api, "friend", "Friend")
    for who in (parent, friend, api):
        assert (await get(api, who, AWAY)).status_code == 200
        assert (await get(api, who, "2001:db8::44")).status_code == 200
    # Taking the permission away applies on the next request.
    assert (await api.patch(f"/users/{parent.id}", json={"denials": ["remote.access"]})).status_code == 200
    await parent.refresh()
    assert denied(await get(api, parent, AWAY)) and (await get(api, parent, HOME)).status_code == 200


async def test_an_untrusted_peer_cannot_forward_itself_home(api: Api) -> None:
    kid = await member(api, "kid", "Kid")
    for spoof in (HOME, "127.0.0.1", f"{HOME}, 127.0.0.1", "10.0.0.1"):
        assert denied(await get(api, kid, AWAY, forwarded=spoof)), spoof
    # And a home peer is home whatever the header claims.
    assert (await get(api, kid, HOME, forwarded=AWAY)).status_code == 200


async def test_a_trusted_proxy_is_believed_for_the_hop_it_added(api: Api) -> None:
    kid = await member(api, "kid", "Kid")
    assert (await get(api, kid, PROXY, forwarded=HOME)).status_code == 200
    assert denied(await get(api, kid, PROXY, forwarded=AWAY))
    # The client wrote the first entry, the proxy appended the second: only the second counts.
    assert denied(await get(api, kid, PROXY, forwarded=f"{HOME}, {AWAY}"))
    assert (await get(api, kid, PROXY, forwarded=f"{AWAY}, {HOME}")).status_code == 200
    assert denied(await get(api, kid, PROXY, forwarded=f"{HOME}, garbage"))
    # Two header lines are one list.
    async with from_address(api, PROXY) as client:
        r = await client.get(
            "/api/v1/favorites",
            headers=[
                ("Authorization", kid.headers["Authorization"]),
                ("X-Forwarded-For", HOME),
                ("X-Forwarded-For", AWAY),
            ],
        )
        assert denied(r)


async def test_home_networks_is_a_setting(api: Api) -> None:
    kid = await member(api, "kid", "Kid")
    assert (await api.get("/settings")).json()["home_networks"] == DEFAULT_HOME_NETWORKS
    assert (await kid.get("/settings")).json()["home_networks"] is None  # the administrator's to see and set
    changed = await api.patch("/settings", json={"home_networks": "198.51.100.0/24, 127.0.0.1"})
    assert changed.status_code == 200 and changed.json()["home_networks"] == "198.51.100.0/24,127.0.0.1/32"
    assert (await get(api, kid, "198.51.100.7")).status_code == 200
    assert denied(await get(api, kid, HOME))
    for bad in ("", "not-a-network", "10.0.0.0/40"):
        r = await api.patch("/settings", json={"home_networks": bad})
        assert r.status_code == 400, (bad, r.text)
    assert (await api.get("/settings")).json()["home_networks"] == "198.51.100.0/24,127.0.0.1/32"
    # Null puts the defaults back.
    assert (await api.patch("/settings", json={"home_networks": None})).json()["home_networks"] == DEFAULT_HOME_NETWORKS
    assert (await get(api, kid, HOME)).status_code == 200
    assert (await kid.req("PATCH", "/settings", json={"home_networks": "0.0.0.0/0"})).status_code == 403


async def test_session_rows_record_the_address_the_same_way(api: Api) -> None:
    async def signed_in_from(peer: str, forwarded: str | None) -> str | None:
        headers = {"X-Forwarded-For": forwarded} if forwarded else {}
        async with from_address(api, peer) as client:
            r = await client.post(
                "/api/v1/auth/login", json={"profile_id": api.profile_id, "password": "test-admin-pw"}, headers=headers
            )
        sid = jwt.decode(r.json()["access_token"], options={"verify_signature": False})["sid"]
        async with api.c.db.session() as s:
            row = await s.get(AuthSession, sid)
            assert row is not None
            return row.ip

    assert await signed_in_from(AWAY, HOME) == AWAY
    assert await signed_in_from(AWAY, None) == AWAY
    assert await signed_in_from(PROXY, f"192.0.2.1, {AWAY}") == AWAY
    assert await signed_in_from(PROXY, None) == PROXY


async def test_legacy_owner_pin_transport_is_gated_too(api: Api) -> None:
    kid = await member(api, "kid", "Kid")
    async with from_address(api, AWAY) as client:
        r = await client.post(
            "/api/v1/auth/owner-pin/start",
            json={"profile_id": kid.profile_id, "action": "profile_switch"},
            headers=kid.headers,
        )
    assert denied(r)
