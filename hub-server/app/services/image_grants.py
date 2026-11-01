"""Profile-bound access to authenticated library artwork."""

from __future__ import annotations

import time
from typing import Any

from app.services.errors import Forbidden

IMAGE_TTL = 300


def image_payload(source_id: str, url: str, ref: str | None, policy: Any) -> dict[str, Any] | None:
    payload: dict[str, Any] = {"s": source_id, "u": url}
    if policy is None or not policy.enabled:
        return payload
    from app.services.content_policy import active_principal

    principal = active_principal.get()
    if principal is None or not principal.session_id or not ref or not ref.startswith(f"lib:{source_id}:"):
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
    if not policy.enabled:
        return
    source, ref = payload.get("s"), payload.get("ref")
    profile, version, session = payload.get("profile"), payload.get("version"), payload.get("session")
    expires = payload.get("expires")
    if (
        not isinstance(source, str)
        or not source
        or not isinstance(ref, str)
        or not ref.startswith(f"lib:{source}:")
        or not ref.removeprefix(f"lib:{source}:")
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
