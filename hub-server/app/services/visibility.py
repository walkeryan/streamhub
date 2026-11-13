"""What a request may watch, by permission.

``watch.library`` covers titles from library servers (Plex, Jellyfin), ``watch.vod``
titles from IPTV providers (Xtream, M3U), ``watch.live`` channels and the guide.
Like the adult scope, the rule lives in a context variable set for one request; unlike
it, the predicate is attached to every ORM read by ``Database.session`` rather than
written into each listing, so no surface can forget it. A request for an exact id the
caller may not watch is refused outright by ``require_watch``.

No request, no scope: sync jobs and other background work see the whole catalog.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.orm import with_loader_criteria

from app.domain.entities import IPTV_SOURCE_TYPES, LIBRARY_SOURCE_TYPES
from app.infra.models import Channel, ChannelGroup, Programme, Source, Title
from app.services.errors import PermissionDenied

SOURCE_PERMISSION = {
    **{str(t.value): "watch.library" for t in LIBRARY_SOURCE_TYPES},
    **{str(t.value): "watch.vod" for t in IPTV_SOURCE_TYPES},
}

_watch: ContextVar[frozenset[str] | None] = ContextVar("watch_permissions", default=None)


def set_watch(permissions: frozenset[str] | None) -> Token[frozenset[str] | None]:
    return _watch.set(permissions)


def reset_watch(token: Token[frozenset[str] | None]) -> None:
    _watch.reset(token)


@contextmanager
def watch_scope(permissions: frozenset[str] | None) -> Iterator[None]:
    """Run a block as a caller holding ``permissions`` (``None``: unrestricted)."""
    token = _watch.set(permissions)
    try:
        yield
    finally:
        _watch.reset(token)


def blocked_source_types(permissions: frozenset[str] | None = None) -> frozenset[str]:
    held = _watch.get() if permissions is None else permissions
    if held is None:
        return frozenset()
    return frozenset(t for t, permission in SOURCE_PERMISSION.items() if permission not in held)


def live_allowed(permissions: frozenset[str] | None = None) -> bool:
    held = _watch.get() if permissions is None else permissions
    return held is None or "watch.live" in held


def watch_key() -> tuple[tuple[str, ...], bool]:
    """Part of any cache key for results computed under the current scope."""
    return tuple(sorted(blocked_source_types())), live_allowed()


def scope_statement(statement: Any) -> Any:
    """Narrow one ORM SELECT to what the current request may watch. A no-op for full access."""
    blocked = blocked_source_types()
    live = live_allowed()
    if not blocked and live:
        return statement
    options = []
    if blocked:
        hidden = select(Source.id).where(Source.type.in_(sorted(blocked)))
        options.append(with_loader_criteria(Title, Title.source_id.not_in(hidden), include_aliases=True))
    if not live:
        options += [
            with_loader_criteria(entity, false(), include_aliases=True) for entity in (Channel, ChannelGroup, Programme)
        ]
    return statement.options(*options)


async def require_watch(db: Any, permissions: frozenset[str], ref: str, source_id: str | None = None) -> None:
    """403 when ``ref`` names something these permissions do not cover.

    A canonical ``tmdb:`` id passes while at least one source the caller may watch
    carries the title; the scoped queries then pick only among those.
    """
    blocked = blocked_source_types(permissions)
    live = live_allowed(permissions)
    if not blocked and live:
        return
    parts = ref.removeprefix("trailer:").split(":", 2)
    if parts[0] in ("ch", "prog"):
        if not live:
            raise PermissionDenied(["watch.live"])
        return
    if not blocked or len(parts) != 3:
        return
    query = None
    if parts[0] == "lib":
        query = select(Source.type).where(Source.id == parts[1])
    elif parts[0] == "tmdb" and parts[1] in ("movie", "tv") and parts[2].isdigit():
        kinds = ("movie", "vod") if parts[1] == "movie" else ("show", "series")
        query = (
            select(Source.type)
            .join(Title, Title.source_id == Source.id)
            .where(Title.tmdb_id == int(parts[2]), Title.kind.in_(kinds), Source.enabled.is_(True))
            .distinct()
        )
        if source_id:
            query = query.where(Source.id == source_id)
    if query is None:
        return
    with watch_scope(None):  # the question is what exists, not what this caller can see
        async with db.session() as s:
            types = {str(t) for t in (await s.execute(query)).scalars()}
    carried = types & set(SOURCE_PERMISSION)
    if carried and carried <= blocked:
        raise PermissionDenied(sorted({SOURCE_PERMISSION[t] for t in carried}))
