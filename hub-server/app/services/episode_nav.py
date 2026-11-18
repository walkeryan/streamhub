"""The episodes before and after one episode, for the player's Up next and previous/next buttons.

Next is the following episode of the same season, else the first episode of the next season that
has any; previous mirrors it. The current episode's library copy is preferred throughout. A season
that copy does not hold still counts when another copy of the series has it (the merged season list
of a title page, ``CatalogService._merged_seasons``), so a series split across copies plays through.
Specials (season 0) are never crossed into from a regular season, nor out of.

Rows come from what the catalog has stored; the source is asked only when a season it should have
returned nothing, and then briefly. Nothing here may hold up or fail playback: the caller bounds it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.infra.models import Title

SHOW_KINDS = ("show", "series")
# A source asked for a season it should already have answered is given this long.
FETCH_TIMEOUT_S = 3.0

Rows = Callable[[Title], Awaitable[list[Title]]]


@dataclass(slots=True)
class Neighbours:
    show: Title | None = None
    previous: Title | None = None
    next: Title | None = None


def _ep_key(row: Title) -> tuple[bool, int, int]:
    return (row.episode_number is None, row.episode_number or 0, row.id or 0)


class EpisodeNavigator:
    def __init__(
        self,
        *,
        title_row: Callable[[str, str], Awaitable[Title | None]],
        stored_children: Rows,
        fetch_children: Rows,
        copies: Rows,
    ) -> None:
        """``copies(show)``: the other library copies of the same series (same TMDB id), in source order."""
        self._title_row = title_row
        self._stored = stored_children
        self._fetch = fetch_children
        self._copies = copies

    async def neighbours(self, episode: Title) -> Neighbours:
        season, show = await self._parents(episode)
        out = Neighbours(show=show)
        parent = season or show
        if parent is None:
            return out
        siblings = await self._episodes(parent)
        if season is None:
            # Episodes straight under the show: one list across its seasons.
            siblings.sort(key=lambda e: (e.season_number is None, e.season_number or 0, *_ep_key(e)))
        out.previous, out.next = _around(siblings, episode)
        number = episode.season_number
        if season is None or show is None or number is None or number <= 0 or (out.previous and out.next):
            return out
        seasons = await self._merged_seasons(show)
        if out.next is None:
            for n in sorted(k for k in seasons if k > number):
                eps = await self._episodes(seasons[n])
                if eps:
                    out.next = eps[0]
                    break
        if out.previous is None:
            for n in sorted((k for k in seasons if 0 < k < number), reverse=True):
                eps = await self._episodes(seasons[n])
                if eps:
                    out.previous = eps[-1]
                    break
        return out

    async def _parents(self, episode: Title) -> tuple[Title | None, Title | None]:
        if not episode.parent_ext_id:
            return None, None
        parent = await self._title_row(episode.source_id, episode.parent_ext_id)
        if parent is None:
            return None, None
        if parent.kind in SHOW_KINDS:
            return None, parent
        if parent.kind != "season":
            return None, None
        show = await self._title_row(parent.source_id, parent.parent_ext_id) if parent.parent_ext_id else None
        return parent, show if show is not None and show.kind in SHOW_KINDS else None

    async def _children(self, row: Title) -> list[Title]:
        kids = await self._stored(row)
        if not kids:
            try:
                kids = await asyncio.wait_for(self._fetch(row), FETCH_TIMEOUT_S)
            except Exception:
                kids = []
        return kids

    async def _episodes(self, parent: Title) -> list[Title]:
        """``parent``'s own episodes (a season's, or a show's that has no season rows), in episode order."""
        eps = [k for k in await self._children(parent) if k.kind == "episode"]
        if parent.kind == "season":
            eps = [e for e in eps if e.parent_ext_id == parent.ext_id]
        eps.sort(key=_ep_key)
        return eps

    async def _merged_seasons(self, show: Title) -> dict[int, Title]:
        """Season number → season row: this copy's where it has one, else the copy with the most seasons."""
        own = [k for k in await self._children(show) if k.kind == "season"]
        chosen = {s.season_number: s for s in own if s.season_number is not None}
        others: list[list[Title]] = []
        for copy in await self._copies(show):
            if (copy.source_id, copy.ext_id) == (show.source_id, show.ext_id):
                continue
            others.append([k for k in await self._children(copy) if k.kind == "season"])
        others.sort(key=lambda seasons: -len(seasons))  # stable: equal copies keep the source order
        for seasons in others:
            for s in seasons:
                if s.season_number is not None:
                    chosen.setdefault(s.season_number, s)
        return chosen


def _around(siblings: list[Title], episode: Title) -> tuple[Title | None, Title | None]:
    """The episodes either side of ``episode`` in its own list: by number when it has one, else by place."""
    others = [e for e in siblings if (e.source_id, e.ext_id) != (episode.source_id, episode.ext_id)]
    if (
        episode.episode_number is not None
        and episode.parent_ext_id
        and all(e.season_number == episode.season_number for e in others if e.season_number is not None)
    ):
        n = episode.episode_number
        before = [e for e in others if e.episode_number is not None and e.episode_number < n]
        after = [e for e in others if e.episode_number is not None and e.episode_number > n]
        return (before[-1] if before else None), (after[0] if after else None)
    keys = [(e.source_id, e.ext_id) for e in siblings]
    try:
        i = keys.index((episode.source_id, episode.ext_id))
    except ValueError:
        return None, None
    return (siblings[i - 1] if i > 0 else None), (siblings[i + 1] if i + 1 < len(siblings) else None)
