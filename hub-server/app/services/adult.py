"""Adult rows stay out of general surfaces.

A row marked ``adult`` at sync is left out of everything that lists titles nobody
asked for by id: home rows and hero, recently added, suggestions, browse and its
facets, search. The one way in is a request that says ``include_adult=true`` from a
user who holds ``watch.adult`` on a session unlocked with their own PIN.

Hiding is not what keeps an adult title shut. A request for one by its exact id (to open
it, play it, or watch it in a room) is refused by ``require_adult`` on the same two
conditions: the permission, and this session's unlock.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import ColumnElement, select, true

from app.infra.models import Title
from app.services.errors import Forbidden, PermissionDenied

_included: ContextVar[bool] = ContextVar("adult_included", default=False)


@contextmanager
def adult_scope(include: bool) -> Iterator[None]:
    """Run one request's listing work with adult rows included (permitted, unlocked opt-in) or not."""
    token = _included.set(bool(include))
    try:
        yield
    finally:
        _included.reset(token)


def adult_included() -> bool:
    return _included.get()


def general_audience() -> ColumnElement[bool]:
    """The predicate every general listing query applies to ``Title``."""
    # "IS NOT true", not "= false": nearly every row passes, and an equality would let
    # SQLite pick the adult index over the selective one (tmdb, kind/added, search).
    return true() if _included.get() else Title.adult.is_not(True)


def is_general_audience(row: Title) -> bool:
    """The same rule for a row already in hand."""
    return _included.get() or not row.adult


async def require_adult(db: Any, principal: Any, ref: str, source_id: str | None = None) -> None:
    """403 when ``ref`` names an adult title and this caller has not both ``watch.adult`` and an
    unlocked session. Knowing an id, or being handed one in a link or a room code, opens nothing."""
    permitted = "watch.adult" in getattr(principal, "permissions", ())
    if permitted and getattr(principal, "adult_unlocked", False):
        return
    parts = ref.removeprefix("trailer:").split(":", 2)
    if len(parts) != 3:
        return
    if parts[0] == "lib":
        query = select(Title.adult).where(Title.source_id == parts[1], Title.ext_id == parts[2])
    elif parts[0] == "tmdb" and parts[1] in ("movie", "tv") and parts[2].isdigit():
        kinds = ("movie", "vod") if parts[1] == "movie" else ("show", "series")
        query = select(Title.adult).where(Title.tmdb_id == int(parts[2]), Title.kind.in_(kinds))
        if source_id:
            query = query.where(Title.source_id == source_id)
    else:
        return
    # A canonical id is adult if any row behind it is: the caller cannot choose which one plays.
    async with db.session() as s:
        marked = any((await s.execute(query.where(Title.adult.is_(True)).limit(1))).scalars())
    if not marked:
        return
    if not permitted:
        raise PermissionDenied(["watch.adult"])
    raise Forbidden("Unlock adult titles with your PIN first", code="adult_locked")
