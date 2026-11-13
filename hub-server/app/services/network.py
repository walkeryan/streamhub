"""Where a request comes from, and whether that is the household's own network.

One helper answers "what is the client's address" for everything that asks (the session
list, the remote-access rule): the peer of the connection, or, only when that peer is a
proxy named in ``FORWARDED_ALLOW_IPS``, what the proxy says it forwarded for.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from functools import lru_cache

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# RFC 1918, loopback, link-local and the shared range overlay networks hand out
# (100.64.0.0/10), then the same four ideas in IPv6.
DEFAULT_HOME_NETWORKS = (
    "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,169.254.0.0/16,100.64.0.0/10,::1/128,fc00::/7,fe80::/10"
)


def parse_address(value: str | None) -> Address | None:
    """An IP address as a header or a socket gives it: maybe bracketed, with a port or a zone."""
    text = (value or "").strip()
    if text.startswith("["):  # [v6]:port
        text = text[1:].partition("]")[0]
    elif text.count(":") == 1:  # v4:port
        text = text.partition(":")[0]
    try:
        address = ipaddress.ip_address(text.partition("%")[0])
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped if mapped is not None else address


@lru_cache(maxsize=32)
def parse_networks(value: str) -> tuple[Network, ...]:
    """Comma-separated addresses and CIDR blocks. Raises ``ValueError`` on anything else."""
    return tuple(ipaddress.ip_network(part.strip(), strict=False) for part in value.split(",") if part.strip())


def clean_networks(value: object) -> str:
    """A ``home_networks`` setting in canonical form, or ``ValueError``. Never empty: that would be nobody's home."""
    try:
        networks = parse_networks(str(value or ""))
    except ValueError as exc:
        raise ValueError("invalid home_networks") from exc
    if not networks or len(networks) > 64:
        raise ValueError("invalid home_networks")
    return ",".join(str(n) for n in networks)


def _within(address: Address, networks: tuple[Network, ...]) -> bool:
    return any(address.version == network.version and address in network for network in networks)


@lru_cache(maxsize=8)
def _trusted(spec: str) -> tuple[bool, tuple[Network, ...]]:
    """``FORWARDED_ALLOW_IPS`` as uvicorn reads it: ``*`` is everyone; names that are not addresses are skipped."""
    parts = [part.strip() for part in spec.split(",") if part.strip()]
    networks: list[Network] = []
    for part in parts:
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return "*" in parts, tuple(networks)


def client_address(peer: str | None, forwarded_for: str | None, trusted_proxies: str) -> str | None:
    """The address a request really comes from, or None when that cannot be known.

    ``X-Forwarded-For`` is anyone's to write, so it counts only when the connection itself
    comes from a trusted proxy, and then only as far as trusted proxies vouch for it: the
    list is read from the right, past each trusted hop, to the first address that is not one.
    """
    everyone, proxies = _trusted(trusted_proxies or "")
    direct = parse_address(peer)
    hops = [hop.strip() for hop in (forwarded_for or "").split(",") if hop.strip()]
    if not hops or not (everyone or (direct is not None and _within(direct, proxies))):
        return str(direct) if direct is not None else None
    if everyone:
        # Nothing tells a proxy from a client here. The left-most name is the client's own claim,
        # so never that one: the last hop is at least what the nearest proxy saw.
        last = parse_address(hops[-1])
        return str(last) if last is not None else None
    return _vouched(hops, proxies)


def _vouched(hops: list[str], proxies: tuple[Network, ...]) -> str | None:
    """The hop a chain of trusted proxies vouches for: right to left, the first that is not one of them."""
    for hop in reversed(hops):
        address = parse_address(hop)
        if address is None:
            return None  # a trusted proxy passed on something that is not an address
        if not _within(address, proxies):
            return str(address)
    return str(parse_address(hops[0]))


# What a proxy adds to say whom it forwards for. A device on the home network sends none of them.
FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "cf-connecting-ip", "x-real-ip", "true-client-ip")


def is_trusted_proxy(peer: str | None, trusted_proxies: str) -> bool:
    """Is the connection itself from a proxy named in ``FORWARDED_ALLOW_IPS``?"""
    everyone, proxies = _trusted(trusted_proxies or "")
    direct = parse_address(peer)
    return everyone or (direct is not None and _within(direct, proxies))


def vouched_for(port: int | None) -> bool:
    """Has the ASGI server already put a trusted proxy's client in the peer's place?

    ``uvicorn --proxy-headers`` does, for a connection from a proxy in ``FORWARDED_ALLOW_IPS``
    and for no other, and the address it writes carries port 0, which no real connection has.
    """
    return port == 0


def from_home(
    peer: str | None, headers: Mapping[str, str], trusted_proxies: str, home_networks: str, *, vouched: bool = False
) -> bool:
    """Does this request come from the household's own network? Any doubt is no.

    The trap this closes: a tunnel or reverse proxy on the same machine connects from a private
    address, which the default home networks contain. Unless that proxy is trusted, its
    forwarding headers cannot be read, the real client is unknown, and unknown is not home.

    ``vouched``: ``peer`` is already the hop a trusted proxy vouched for (see ``vouched_for``).
    """
    everyone, proxies = _trusted(trusted_proxies or "")
    forwarding = {name: headers.get(name) or "" for name in FORWARDING_HEADERS if headers.get(name)}
    hops = [hop.strip() for hop in forwarding.pop("x-forwarded-for", "").split(",") if hop.strip()]
    if not forwarding and not hops:
        return is_home(peer, home_networks)
    if everyone:
        # Everyone is "trusted", so nothing is: home only if no address anywhere in sight is from elsewhere.
        return not forwarding and all(is_home(address, home_networks) for address in (peer, *hops))
    if vouched:
        return is_home(peer, home_networks)
    if not hops or not is_trusted_proxy(peer, trusted_proxies):
        # A proxy is in the way that is not trusted, or that names the client in a header nobody here can walk.
        return False
    return is_home(_vouched(hops, proxies), home_networks)


def is_home(address: str | None, home_networks: str) -> bool:
    """Is this client on the household's own network? An unknown address is not."""
    parsed = parse_address(address)
    try:
        networks = parse_networks(home_networks or DEFAULT_HOME_NETWORKS)
    except ValueError:
        networks = parse_networks(DEFAULT_HOME_NETWORKS)
    return parsed is not None and _within(parsed, networks)
