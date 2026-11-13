"""Profile-bound access to artwork the hub serves itself (authenticated libraries, proxied sources)."""

from __future__ import annotations

import time
from typing import Any

from app.services.errors import Forbidden

IMAGE_TTL = 300
# A grant bound to nobody in particular (an unrestricted viewer's artwork) is still a URL that
# works without signing in, so it does not work for ever. Its expiry is the end of next week's
# bucket: one to two weeks away, and the same value all week, so the URL stays cacheable.
UNBOUND_TTL = 7 * 86400
WEB_SCHEMES = ("http://", "https://")


def _owned_by(ref: Any, source_id: str) -> bool:
    """A title (``lib:``) or channel (``ch:``) reference that belongs to this source."""
    return isinstance(ref, str) and any(
        ref.startswith(prefix) and len(ref) > len(prefix) for prefix in (f"lib:{source_id}:", f"ch:{source_id}:")
    )


def image_payload(source_id: str, url: str, ref: str | None, policy: Any) -> dict[str, Any] | None:
    """What an artwork URL is signed over. Bound to the caller's session and the exact title
    whenever the content policy applies to them, so the fetch can be checked against it; and
    to the home network for a caller without ``remote.access``, whose session is too."""
    payload: dict[str, Any] = {"s": source_id, "u": url}
    from app.services.content_policy import active_principal

    principal = active_principal.get()
    if principal is not None and "remote.access" not in principal.permissions:
        payload["home"] = True
    if policy is None or not policy.applies_to(principal):
        payload["x"] = (int(time.time()) // UNBOUND_TTL + 2) * UNBOUND_TTL
        return payload
    if principal is None or not principal.session_id or not _owned_by(ref, source_id):
        return None
    payload.update(
        profile=principal.profile_id,
        version=principal.token_version,
        session=principal.session_id,
        ref=ref,
        expires=time.time() + IMAGE_TTL,
    )
    return payload


async def validate_image_grant(payload: dict[str, Any], policy: Any, auth: Any) -> None:
    # An unbound grant was minted for a caller the policy did not apply to. Under the legacy
    # flag there is no such caller; otherwise it is an unrestricted user's artwork.
    if not policy.enabled and "profile" not in payload:
        until = payload.get("x")
        if (
            isinstance(until, bool)
            or not isinstance(until, (int, float))
            or not time.time() < until <= time.time() + 2 * UNBOUND_TTL + 2
        ):
            raise Forbidden("Artwork authorization expired or missing", code="image_expired")
        return
    source, ref = payload.get("s"), payload.get("ref")
    profile, version, session = payload.get("profile"), payload.get("version"), payload.get("session")
    expires = payload.get("expires")
    if (
        not isinstance(source, str)
        or not source
        or not _owned_by(ref, source)
        or not isinstance(profile, str)
        or not profile
        or not isinstance(session, str)
        or not session
        or not isinstance(version, int)
        or isinstance(version, bool)
        or not isinstance(expires, (int, float))
        or isinstance(expires, bool)
        or not time.time() < expires <= time.time() + IMAGE_TTL + 2
    ):
        raise Forbidden("Artwork authorization expired or missing", code="content_restricted")
    await auth.session_valid(profile, version, session)
    await policy.require(profile, ref)
