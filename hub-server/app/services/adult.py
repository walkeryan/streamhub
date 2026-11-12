"""Adult rows stay out of general surfaces.

Until profiles carry permissions, a row marked ``adult`` at sync is left out of
everything that lists titles nobody asked for by id: home rows and hero, recently
added, suggestions, browse and its facets, search. The one way in is a request
that says ``include_adult=true`` from the admin profile.

This hides; it does not authorize. Opening or playing a title by its exact id is
unaffected.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import ColumnElement, true

from app.infra.models import Title

_included: ContextVar[bool] = ContextVar("adult_included", default=False)


@contextmanager
def adult_scope(include: bool) -> Iterator[None]:
    """Run one request's listing work with adult rows included (admin opt-in) or not."""
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
