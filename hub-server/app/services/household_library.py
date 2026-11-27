"""Apply household removals to catalog reads, including new copies of the same film."""

from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.orm import with_loader_criteria

from app.infra.models import Channel, LibraryRemoval, Title
from app.services import ids

removed_media: ContextVar[frozenset[str]] = ContextVar("removed_media", default=frozenset())


async def removed_refs(db: Any) -> frozenset[str]:
    async with db.session() as session:
        rows = (await session.execute(select(LibraryRemoval.ref_id, LibraryRemoval.aliases))).all()
    return frozenset(ref for key, aliases in rows for ref in (key, *aliases))


def catalog_read(path: str) -> bool:
    path = path.removeprefix("/api/v1/")
    return (
        path.split("/", 1)[0]
        in {
            "home",
            "browse",
            "search",
            "titles",
            "for-you",
            "favorites",
            "watchlist",
            "history",
            "channels",
            "epg",
            "sports",
            "discover",
            "actors",
            "sources",
            "rows",
            "people",
        }
        and path != "for-you/feedback"
    )


def scope_statement(statement: Any) -> Any:
    refs = removed_media.get()
    if not refs:
        return statement
    return statement.options(*_criteria(refs))


@lru_cache(maxsize=8)
def _criteria(refs: frozenset[str]) -> tuple[Any, ...]:
    predicate = _removed_titles(refs)
    options = [with_loader_criteria(Title, ~predicate, include_aliases=True)] if predicate is not None else []
    hidden = [ids.parse(ref) for ref in refs]
    channels = [(h.a, h.b) for h in hidden if h.scheme == "ch"]
    if channels:
        options.append(
            with_loader_criteria(
                Channel, tuple_(Channel.source_id, Channel.ext_id).not_in(channels), include_aliases=True
            )
        )
    return tuple(options)


@lru_cache(maxsize=8)
def _removed_titles(refs: frozenset[str]) -> Any:
    hidden = [ids.parse(ref) for ref in refs]
    library = [(h.a, h.b) for h in hidden if h.scheme == "lib"]
    # Use the underlying table for the two ancestry queries so ORM loader criteria
    # do not filter out the very parents whose descendants we are looking for.
    parent = Title.__table__.alias("removed_root")
    roots = []
    direct = []
    for kind, kinds in (("movie", ids.MOVIE_KINDS), ("tv", ids.SHOW_KINDS)):
        numbers = [int(h.b) for h in hidden if h.scheme == "tmdb" and h.a == kind and h.b.isdigit()]
        if numbers:
            direct.append(and_(Title.kind.in_(kinds), Title.tmdb_id.is_not(None), Title.tmdb_id.in_(numbers)))
            if kind == "tv":
                roots.append(and_(parent.c.kind.in_(kinds), parent.c.tmdb_id.in_(numbers)))
    if library:
        direct.append(tuple_(Title.source_id, Title.ext_id).in_(library))
        # Include stored aliases even when synchronization has already deleted their rows.
        roots.append(tuple_(parent.c.source_id, parent.c.ext_id).in_(library))
    if roots:
        root_ids = select(parent.c.source_id, parent.c.ext_id).where(or_(*roots))
        child = Title.__table__.alias("removed_child")
        parent_id = tuple_(Title.source_id, Title.parent_ext_id)
        child_root = tuple_(child.c.source_id, child.c.parent_ext_id)
        parents = [parent_id.in_(root_ids)]
        child_parents = [child_root.in_(root_ids)]
        if library:
            parents.append(parent_id.in_(library))
            child_parents.append(child_root.in_(library))
        child_ids = select(child.c.source_id, child.c.ext_id).where(or_(*child_parents))
        direct.append(and_(Title.parent_ext_id.is_not(None), or_(*parents, parent_id.in_(child_ids))))
    return or_(*direct) if direct else None


async def removed_playback(db: Any, refs: list[str], hidden: frozenset[str]) -> bool:
    if not hidden:
        return False
    ids_to_check = [ref.removeprefix("trailer:") for ref in refs]
    if not hidden.isdisjoint(ids_to_check):
        return True
    library = [ids.parse(ref) for ref in ids_to_check if ref.startswith("lib:") and ref.count(":") >= 2]
    predicate = _removed_titles(hidden)
    if not library or predicate is None:
        return False
    async with db.session() as session:
        found = await session.scalar(
            select(Title.id)
            .where(tuple_(Title.source_id, Title.ext_id).in_([(h.a, h.b) for h in library]), predicate)
            .limit(1)
        )
    return found is not None


def filter_metadata(value: Any, hidden: frozenset[str]) -> Any:
    """External discovery and related titles can originate outside the local SQL catalog."""
    if isinstance(value, list):
        return [filtered for item in value if (filtered := filter_metadata(item, hidden)) is not None]
    if isinstance(value, dict):
        if isinstance(value.get("id"), str) and value["id"] in hidden:
            return None
        if isinstance(value.get("show"), dict) and value["show"].get("id") in hidden:
            return None
        return {
            key: filter_metadata(item, hidden) if isinstance(item, (list, dict)) else item
            for key, item in value.items()
        }
    return value
