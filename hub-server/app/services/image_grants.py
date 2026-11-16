"""Signed URLs for artwork the hub serves itself (authenticated libraries, proxied sources).

One URL per image, the same for every viewer: what a viewer may see is decided where it matters,
in which titles a listing shows and whether they play. Artwork is not checked a second time per
viewer, so every browser, TV and the hub's own disk cache keep one copy of each image instead of
fetching it again whenever the URL changed with a session (as it did for rating-limited profiles).
"""

from __future__ import annotations

import time
from typing import Any

from app.services.errors import Forbidden

# The URL still works without signing in, so it does not work for ever: its expiry is the end of
# next week's bucket, one to two weeks away and the same value all week. Viewers keep the image
# until then (``browser_lifetime``); artwork rarely changes, and a new upstream image has a new URL.
UNBOUND_TTL = 7 * 86400
WEB_SCHEMES = ("http://", "https://")


def image_payload(source_id: str, url: str, ref: str | None = None, policy: Any = None) -> dict[str, Any]:
    """What an artwork URL is signed over: the image and the week it expires in. ``ref`` and
    ``policy`` are accepted for callers that still pass them; neither changes the URL."""
    return {"s": source_id, "u": url, "x": (int(time.time()) // UNBOUND_TTL + 2) * UNBOUND_TTL}


def browser_lifetime(payload: dict[str, Any]) -> int:
    """Seconds anyone may keep this image: until its grant expires."""
    until = payload.get("x")
    if isinstance(until, bool) or not isinstance(until, (int, float)):
        return 0
    return max(0, int(until - time.time()))


def cache_control(payload: dict[str, Any]) -> str:
    """The URL never names different bytes, so caches may keep it to the end and skip revalidating."""
    lifetime = browser_lifetime(payload)
    return f"public, max-age={lifetime}, immutable" if lifetime else "no-store"


async def validate_image_grant(payload: dict[str, Any], policy: Any = None, auth: Any = None) -> None:
    """Refuses a grant past its week (or without one: grants signed before this scheme, bound to
    one viewer, expired within minutes and are simply signed again with the next listing)."""
    until = payload.get("x")
    now = time.time()
    if isinstance(until, bool) or not isinstance(until, (int, float)) or not now < until <= now + 2 * UNBOUND_TTL + 2:
        raise Forbidden("Artwork authorization expired or missing", code="image_expired")
