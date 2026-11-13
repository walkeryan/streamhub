"""``remote.access``: without it a user is served on the home network only, and the client's
address is never taken from a header an untrusted peer wrote."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.infra.models import AuthSession, Recording
from app.infra.settings import AppSettings
from app.services.classification import classification_record, summarize
from app.services.network import (
    DEFAULT_HOME_NETWORKS,
    clean_networks,
    client_address,
    from_home,
    is_home,
    vouched_for,
)
from app.services.streams import VALID_TTL_S
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
    # No trusted proxies at all.
    assert client_address(PROXY, AWAY, "") == PROXY
    # "Trust everyone" cannot tell a proxy from a client. The left-most name is whatever the
    # client typed, so it is never the answer: the last hop is what the nearest proxy saw.
    assert client_address(AWAY, f"{HOME}, 198.51.100.7", "*") == "198.51.100.7"
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


TUNNEL = "172.17.0.5"  # a tunnel connector's container address: private, so inside the default home networks


async def test_a_forwarding_proxy_that_is_not_trusted_is_never_home(api: Api) -> None:
    """Behind a tunnel nobody listed in FORWARDED_ALLOW_IPS, every client arrives from the
    connector's private address. A forwarding header says a proxy is in the way; without
    trust in that proxy the real client is unknown, and unknown is not home."""
    kid = await member(api, "kid", "Kid")
    assert (await get(api, kid, TUNNEL)).status_code == 200  # a device on that network, no proxy
    for header in ("X-Forwarded-For", "Forwarded", "CF-Connecting-IP", "X-Real-IP", "True-Client-IP"):
        for value in (AWAY, HOME, "for=192.168.1.20", f"{TUNNEL}, {AWAY}"):
            async with from_address(api, TUNNEL) as client:
                r = await client.get("/api/v1/favorites", headers={**kid.headers, header: value})
            assert denied(r), (header, value)
    assert denied(await get(api, kid, HOME, forwarded=AWAY))
    # Someone with remote.access is served either way.
    assert (await get(api, api, TUNNEL, forwarded=AWAY)).status_code == 200
    # Behind a trusted proxy under uvicorn --proxy-headers, the peer is already the client it vouched for.
    app = api.client._transport.app  # type: ignore[attr-defined]
    for vouched, served in ((HOME, True), (AWAY, False)):
        async with AsyncClient(transport=ASGITransport(app=app, client=(vouched, 0)), base_url="http://hub.test") as c:
            r = await c.get("/api/v1/favorites", headers={**kid.headers, "X-Forwarded-For": vouched})
        assert (r.status_code == 200) is served and denied(r) is not served


def test_from_home_decision() -> None:
    trust = "127.0.0.1,172.18.0.0/16"
    home = DEFAULT_HOME_NETWORKS

    def at_home(peer: str | None, trusted: str = trust, **headers: str) -> bool:
        return from_home(peer, {k.replace("_", "-"): v for k, v in headers.items()}, trusted, home)

    assert at_home(HOME) and at_home("127.0.0.1") and not at_home(AWAY) and not at_home(None)
    # Through a trusted proxy: the hop it vouches for decides.
    assert at_home("172.18.0.2", x_forwarded_for=HOME) and not at_home("172.18.0.2", x_forwarded_for=AWAY)
    assert not at_home("172.18.0.2", x_forwarded_for=f"{HOME}, {AWAY}")
    # Through anything else that forwards: never.
    assert not at_home(TUNNEL, x_forwarded_for=HOME) and not at_home(TUNNEL, cf_connecting_ip=HOME)
    assert not at_home(TUNNEL, x_forwarded_for=f"{TUNNEL}, {AWAY}") and not at_home(AWAY, x_forwarded_for=AWAY)
    assert not at_home(HOME, forwarded="for=192.168.1.9") and not at_home(HOME, x_real_ip=HOME)
    # A trusted proxy that names the client only in a header nobody can walk is not believed either.
    assert not at_home("172.18.0.2", x_real_ip=HOME) and not at_home("172.18.0.2", cf_connecting_ip=HOME)
    # uvicorn --proxy-headers has already put the vouched-for hop in the peer's place (with port 0)
    # and left the headers on the request. Without that mark the same request is not believed.
    for chain in (HOME, f"{AWAY}, {HOME}, 172.18.0.3"):
        assert from_home(HOME, {"x-forwarded-for": chain, "cf-connecting-ip": HOME}, trust, home, vouched=True)
        assert not at_home(HOME, x_forwarded_for=chain)
    assert not from_home(AWAY, {"x-forwarded-for": f"{HOME}, {AWAY}"}, trust, home, vouched=True)
    assert vouched_for(0) and not vouched_for(40000) and not vouched_for(None)
    # "*": home only when nothing in the chain is from elsewhere.
    assert at_home(HOME, "*") and at_home(PROXY, "*", x_forwarded_for=HOME)
    assert not at_home(PROXY, "*", x_forwarded_for=f"{HOME}, {AWAY}")
    assert not at_home(PROXY, "*", x_forwarded_for=f"{AWAY}, {HOME}")
    assert not at_home(PROXY, "*", x_forwarded_for=f"{HOME}, garbage")
    assert not at_home(PROXY, "*", x_forwarded_for=HOME, cf_connecting_ip=AWAY)
    assert not at_home(AWAY, "*", x_forwarded_for=HOME)


async def test_trust_everyone_is_warned_about_at_startup(
    settings: AppSettings, caplog: pytest.LogCaptureFixture
) -> None:
    from app.container import build_container
    from app.infra.cache import MemoryCache
    from app.main import create_app
    from tests.conftest import http_factory

    wide = settings.model_copy(update={"forwarded_allow_ips": " * ", "config_dir": settings.config_dir / "wide"})
    app = create_app(
        wide, container=build_container(wide, http_factory=http_factory, cache=MemoryCache()), start_background=False
    )
    with caplog.at_level("WARNING"):
        async with app.router.lifespan_context(app):
            pass
    assert sum("FORWARDED_ALLOW_IPS" in r.getMessage() for r in caplog.records) == 1


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
    # Signing in with a profile is for the home network now; away from it, a username and password.
    made = await api.put("/auth/password", json={"username": "boss", "new_password": "a long enough phrase"})
    assert made.status_code == 204, made.text

    async def signed_in_from(peer: str, forwarded: str | None) -> str | None:
        headers = {"X-Forwarded-For": forwarded} if forwarded else {}
        async with from_address(api, peer) as client:
            r = await client.post(
                "/api/v1/auth/password", json={"username": "boss", "password": "a long enough phrase"}, headers=headers
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


# ---------------------------------------------------------------- signed media URLs
@pytest.fixture
def recordings(api: Api, tmp_path: Path) -> Path:
    root = tmp_path / "recordings"
    root.mkdir()
    api.c.recorder.recordings_dir = root
    return root


async def stream_url(api: Api, who: Member, root: Path, rid: str) -> str:
    """A proxied stream of ``who``'s own: a finished recording, so no upstream is involved."""
    (root / f"{rid}.mkv").write_bytes(b"\x1a\x45\xdf\xa3" * 64)
    start = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)
    rating = summarize("xmltv", "programme", [classification_record("US", "US-TVPG", "TV-G")])
    async with api.c.db.session() as s:
        s.add(
            Recording(
                id=rid,
                profile_id=who.profile_id,
                channel_id="ch:gone:1",
                programme_key=rid,
                title="Evening News",
                starts_at=start,
                ends_at=start + timedelta(hours=1),
                state="done",
                path=f"{rid}.mkv",
                age_evidence=rating,
            )
        )
        await s.commit()
    r = await who.post("/playback/resolve", json={"id": f"rec:{rid}", "client": "android_tv"})
    assert r.status_code == 200, r.text
    return str(urlsplit(r.json()["url"]).path)


async def fetch(api: Api, peer: str, path: str, forwarded: str | None = None) -> Any:
    async with from_address(api, peer) as client:
        return await client.get(path, headers={"X-Forwarded-For": forwarded} if forwarded is not None else {})


async def test_stream_urls_follow_their_users_remote_access(api: Api, recordings: Path) -> None:
    kid = await member(api, "kid", "Kid")
    parent = await member(api, "family", "Parent")
    theirs, hers = await stream_url(api, kid, recordings, "kids"), await stream_url(api, parent, recordings, "hers")
    assert (await fetch(api, HOME, theirs)).status_code == 200
    assert denied(await fetch(api, AWAY, theirs))
    # Every route under the token is the same door.
    for rest in ("/index.m3u8", "/hls/anything/seg_00000.ts", "/seg/anything"):
        assert denied(await fetch(api, AWAY, theirs + rest)), rest
    # The address is the proxy's word only when the proxy is one we trust.
    assert (await fetch(api, PROXY, theirs, forwarded=HOME)).status_code == 200
    assert denied(await fetch(api, PROXY, theirs, forwarded=AWAY))
    assert denied(await fetch(api, PROXY, theirs, forwarded=f"{HOME}, {AWAY}"))
    assert denied(await fetch(api, AWAY, theirs, forwarded=HOME))
    assert denied(await fetch(api, AWAY, theirs, forwarded=f"{HOME}, 127.0.0.1"))
    # With the permission the same kind of URL plays anywhere.
    assert (await fetch(api, AWAY, hers)).status_code == 200
    assert (await fetch(api, PROXY, hers, forwarded=AWAY)).status_code == 200


async def test_the_stream_check_costs_no_lookup_of_its_own(api: Api, recordings: Path, monkeypatch: Any) -> None:
    kid = await member(api, "kid", "Kid")
    path = await stream_url(api, kid, recordings, "kids")
    api.c.guard._valid.clear()
    validate, calls = api.c.auth.validate, []

    async def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return await validate(*args, **kwargs)

    monkeypatch.setattr(api.c.auth, "validate", counted)
    for _ in range(3):
        assert (await fetch(api, HOME, path)).status_code == 200
        assert denied(await fetch(api, AWAY, path))
    assert len(calls) == 1  # the guard's own validation, remembered; nothing more per request


async def test_losing_remote_access_ends_streams_away_from_home(api: Api, recordings: Path) -> None:
    from tests.test_stream_sessions import Clock

    clock = Clock()
    api.c.guard.clock = clock
    parent = await member(api, "family", "Parent")
    path = await stream_url(api, parent, recordings, "hers")
    assert (await fetch(api, AWAY, path)).status_code == 200
    assert (await api.patch(f"/users/{parent.id}", json={"denials": ["remote.access"]})).status_code == 200
    clock.now += VALID_TTL_S + 0.1
    # The grant is out of date wherever it is used; a new one is theirs at home only.
    assert (await fetch(api, AWAY, path)).status_code == 401
    await parent.refresh()
    again = await stream_url(api, parent, recordings, "again")
    assert (await fetch(api, HOME, again)).status_code == 200 and denied(await fetch(api, AWAY, again))
