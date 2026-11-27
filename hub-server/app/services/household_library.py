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
    options = []
    hidden = [ids.parse(ref) for ref in refs]
    title_clauses = []
    for kind, kinds in (("movie", ids.MOVIE_KINDS), ("tv", ids.SHOW_KINDS)):
        numbers = [int(h.b) for h in hidden if h.scheme == "tmdb" and h.a == kind and h.b.isdigit()]
        if numbers:
            title_clauses.append(and_(Title.kind.in_(kinds), Title.tmdb_id.is_not(None), Title.tmdb_id.in_(numbers)))
    library = [(h.a, h.b) for h in hidden if h.scheme == "lib"]
    if library:
        title_clauses.append(tuple_(Title.source_id, Title.ext_id).in_(library))
        title_clauses.append(
            and_(Title.parent_ext_id.is_not(None), tuple_(Title.source_id, Title.parent_ext_id).in_(library))
        )
    if title_clauses:
        parent = Title.__table__.alias("removed_parent")
        if library:
            parents = select(parent.c.source_id, parent.c.ext_id).where(
                tuple_(parent.c.source_id, parent.c.parent_ext_id).in_(library)
            )
            title_clauses.append(
                and_(Title.parent_ext_id.is_not(None), tuple_(Title.source_id, Title.parent_ext_id).in_(parents))
            )
        options.append(with_loader_criteria(Title, ~or_(*title_clauses), include_aliases=True))
    channels = [(h.a, h.b) for h in hidden if h.scheme == "ch"]
    if channels:
        options.append(
            with_loader_criteria(
                Channel, tuple_(Channel.source_id, Channel.ext_id).not_in(channels), include_aliases=True
            )
        )
    return tuple(options)


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
