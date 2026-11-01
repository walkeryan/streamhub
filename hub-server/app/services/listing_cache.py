"""Cache only bounded, authenticated catalog reads, scoped to profile and origin."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.infra.db import Database
from app.services.context import public_base
from app.services.errors import Conflict


def normalized_query(value: str | None) -> str | None:
    return " ".join(value.split()).casefold() if value is not None else None


async def listing_cached(
    db: Database,
    profile: str,
    operation: str,
    params: tuple[Any, ...],
    load: Callable[[], Awaitable[Any]],
    ttl: float = 15,
) -> Any:
    from app.services.content_policy import active_policy, active_principal

    policy = active_policy.get()
    principal = active_principal.get()
    image_session = (principal.session_id, principal.token_version) if policy and principal else None
    for _ in range(2):
        revision = db.catalog_revisions.version(profile)
        key = (operation, profile, image_session, revision, public_base.get(), params)
        valid = lambda revision=revision: revision == db.catalog_revisions.version(profile)  # noqa: E731
        result = await db.result_cache.get(
            key,
            load,
            ttl,
            valid,
            cacheable=lambda value: not (isinstance(value, dict) and value.get("metadata_incomplete", False)),
        )
        if valid():
            return result
    raise Conflict("Catalog changed while loading. Please retry.", code="catalog_changed")
