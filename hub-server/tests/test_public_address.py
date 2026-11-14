"""``home_public_address``: a hub reached through a tunnel counts the household's own internet
address as home, looked up by the hub itself, and nothing else besides."""

from __future__ import annotations

from typing import Any

import httpx

from app.services.network import DEFAULT_HOME_NETWORKS
from app.services.public_address import PLAIN_URL, TRACE_URL, PublicAddress, parse_plain, parse_trace, with_public
from tests.conftest import Api
from tests.people import member
from tests.test_remote_access import AWAY, HOME, PROXY, denied, get

OURS = "198.51.100.23"  # documentation range: the household's address in these tests


class Lookup:
    def __init__(
        self, trace: tuple[int, str] | None = (200, f"fl=1\nip={OURS}\nts=1\n"), plain: tuple[int, str] | None = None
    ) -> None:
        self.trace, self.plain, self.asked = trace, plain, []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(str(request.url))
        answer = self.trace if str(request.url) == TRACE_URL else self.plain if str(request.url) == PLAIN_URL else None
        if answer is None:
            raise httpx.ConnectError("unreachable", request=request)
        return httpx.Response(answer[0], text=answer[1])

    def factory(self, **kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def test_answers_are_read_strictly() -> None:
    assert parse_trace(f"fl=1\nip={OURS}\nts=1") == OURS
    assert parse_trace("ip=2001:db8::1") == "2001:db8::1"
    assert parse_trace("ip=192.168.1.4") is None and parse_trace("ip=0.0.0.0") is None
    assert parse_trace("nothing here") is None
    assert parse_plain(f" {OURS}\n") == OURS and parse_plain("<html>") is None


async def test_lookup_falls_back_and_keeps_the_last_address() -> None:
    lookup = Lookup(trace=(500, "busy"), plain=(200, OURS))
    found = PublicAddress(lookup.factory, enabled=lambda: True)
    assert found.network() is None
    assert await found.refresh() == OURS and found.network() == f"{OURS}/32"
    assert lookup.asked == [TRACE_URL, PLAIN_URL]
    # Both lookups failing keeps what was known: an outage must not lock the household out.
    lookup.plain = None
    assert await found.refresh() == OURS
    assert with_public("", found) == f"{DEFAULT_HOME_NETWORKS},{OURS}/32"
    assert with_public("10.0.0.0/8", found) == f"10.0.0.0/8,{OURS}/32"
    assert with_public("10.0.0.0/8", None) == "10.0.0.0/8"


async def test_the_households_address_counts_as_home_only_when_switched_on(api: Api) -> None:
    lookup = Lookup()
    api.c.public_address._client_factory = lookup.factory
    kid = await member(api, "kid", "Kid")
    # Through the tunnel (a trusted proxy), from the household's own address: refused while off.
    assert denied(await get(api, kid, PROXY, forwarded=OURS))
    assert (await api.get("/settings")).json()["home_public_address"] is False
    on = await api.patch("/settings", json={"home_public_address": True})
    assert on.status_code == 200
    assert on.json()["home_public_address"] is True and on.json()["home_public_address_seen"] == OURS
    assert (await get(api, kid, PROXY, forwarded=OURS)).status_code == 200
    # The home network itself still is home; anywhere else still is not.
    assert (await get(api, kid, PROXY, forwarded=HOME)).status_code == 200
    assert denied(await get(api, kid, PROXY, forwarded=AWAY))
    # A header from someone who is not a trusted proxy is never read, whatever it claims.
    assert denied(await get(api, kid, AWAY, forwarded=OURS))
    # The administrator's to see and set.
    seen = (await kid.get("/settings")).json()
    assert seen["home_public_address"] is None and seen["home_public_address_seen"] is None
    assert (await kid.req("PATCH", "/settings", json={"home_public_address": True})).status_code == 403
    off = await api.patch("/settings", json={"home_public_address": False})
    assert off.json()["home_public_address"] is False and off.json()["home_public_address_seen"] is None
    assert denied(await get(api, kid, PROXY, forwarded=OURS))
