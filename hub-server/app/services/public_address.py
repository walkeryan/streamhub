"""The household's own internet address, for a hub reached through a tunnel or a public proxy.

A TV on the home network that opens the hub by its public name goes out through the home router and
comes back through the tunnel: the hub sees the router's internet address, never a private one, so
"home network only" refuses the household's own devices. With the ``home_public_address`` setting on,
the hub looks up the address it reaches the internet from and counts that one address as home too.
It follows the address when the provider changes it.

Off by default: behind a shared address (carrier-grade NAT, a campus, a hotspot) the neighbours would
count as home as well.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
from collections.abc import Callable

import httpx

from app.services.network import DEFAULT_HOME_NETWORKS, is_home

log = logging.getLogger(__name__)

# Two independent answers to "what address do I come from?", tried in order. Each returns the caller's
# address and nothing about anyone else.
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
PLAIN_URL = "https://api.ipify.org"
REFRESH_S = 600.0
REQUEST_TIMEOUT_S = 5.0


def _public(text: str) -> str | None:
    try:
        address = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    # A lookup that answers with a home-network address has not seen the internet side.
    return None if is_home(str(address), DEFAULT_HOME_NETWORKS) or address.is_unspecified else str(address)


def parse_trace(body: str) -> str | None:
    """The ``ip=`` line of a trace answer."""
    for line in body.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "ip":
            return _public(value)
    return None


def parse_plain(body: str) -> str | None:
    return _public(body)


class PublicAddress:
    def __init__(self, client_factory: Callable[..., httpx.AsyncClient], *, enabled: Callable[[], bool]) -> None:
        self._client_factory = client_factory
        self._enabled = enabled
        # The last address found. A failed lookup keeps it: a brief outage must not lock the household out.
        self.address: str | None = None
        self._task: asyncio.Task[None] | None = None

    def network(self) -> str | None:
        """The address as a one-address network, for the home networks list; None until one is known."""
        if self.address is None:
            return None
        return f"{self.address}/{32 if ':' not in self.address else 128}"

    async def refresh(self) -> str | None:
        for url, parse in ((TRACE_URL, parse_trace), (PLAIN_URL, parse_plain)):
            try:
                async with self._client_factory(timeout=REQUEST_TIMEOUT_S) as client:
                    resp = await client.get(url)
            except httpx.HTTPError:
                continue
            found = parse(resp.text) if resp.status_code == 200 else None
            if found is not None:
                if found != self.address:
                    log.info("public address found", extra={"changed": self.address is not None})
                self.address = found
                return found
        log.warning("public address lookup failed", extra={"kept": self.address is not None})
        return self.address

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            if self._enabled():
                with contextlib.suppress(Exception):
                    await self.refresh()
            await asyncio.sleep(REFRESH_S)

    async def shutdown(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def with_public(home_networks: str, public: PublicAddress | None) -> str:
    """``home_networks`` plus the household's internet address, when it is known."""
    extra = public.network() if public is not None else None
    if extra is None:
        return home_networks
    return f"{home_networks or DEFAULT_HOME_NETWORKS},{extra}"
