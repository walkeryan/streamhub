"""An invented catalog of realistic size and shape for search timing: no real titles, channels or people."""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import insert

from app.domain.entities import MetaTitle, ProviderOffer
from app.infra.models import Channel, ChannelGroup, Programme, Source, Title
from app.services.ids import normalise_title
from tests.conftest import Api

_STARTS = ("ha", "di", "bre", "es", "gri", "mi", "lan", "tes", "kor", "vel", "sto", "mea", "nor", "wes", "cal", "dun")
_MIDS = ("l", "ll", "s", "sc", "a", "n", "nd", "r", "rr", "v", "t", "st", "m", "ld", "x", "k")
_ENDS = ("en", "er", "a", "o", "is", "and", "ow", "ine", "ock", "ey", "um", "ar", "eth", "ill", "ora", "ux")
WORDS = [a + b + c for a in _STARTS for b in _MIDS for c in _ENDS]
_PREFIXES = ("", "", "", "", "EN - ", "XX | ", "4K - ")


def _title(rng: random.Random) -> str:
    # A few words are in a great many titles, most in a handful: as in any catalog.
    words = [WORDS[min(len(WORDS) - 1, int(rng.paretovariate(1.1) * 3))] for _ in range(rng.choice((1, 2, 2, 3, 3, 4)))]
    name = " ".join(words).title()
    return ("The " + name) if rng.random() < 0.15 else name


async def seed_search_catalog(
    api: Api, *, films: int = 29000, series: int = 6200, channels: int = 1180, programmes: int = 30000
) -> None:
    """Films and series (two in three matched to a metadata id), channels and two days of guide."""
    rng = random.Random(7)
    rows: list[dict[str, Any]] = []
    for n in range(films + series):
        name, year = _title(rng), 1970 + rng.randrange(56)
        shown = f"{rng.choice(_PREFIXES)}{name} ({year})"
        rows.append(
            dict(
                source_id="catalog",
                ext_id=str(n),
                kind="vod" if n < films else "series",
                title=shown,
                norm_title=normalise_title(shown),
                year=year,
                tmdb_id=n + 1 if n % 3 else None,
                language="eng",
            )
        )
    now = datetime.now(UTC)
    async with api.c.db.session() as s:
        s.add(Source(id="catalog", name="Catalog", type="xtream", config_enc="unused"))
        await s.flush()
        s.add(ChannelGroup(id="g", source_id="catalog", name="Group"))
        await s.flush()
        for start in range(0, len(rows), 5000):
            await s.execute(insert(Title), rows[start : start + 5000])
        await s.execute(
            insert(Channel),
            [
                dict(
                    source_id="catalog",
                    ext_id=f"c{n}",
                    name=f"{WORDS[n % len(WORDS)].title()} TV {n}",
                    norm_name=f"{WORDS[n % len(WORDS)]} tv {n}",
                    number=n + 1,
                    epg_id=f"c{n}",
                    group_id="g",
                    group_name="Group",
                )
                for n in range(channels)
            ],
        )
        for start in range(0, programmes, 5000):
            await s.execute(
                insert(Programme),
                [
                    dict(
                        feed="catalog",
                        epg_channel=f"c{n % max(channels, 1)}",
                        title=_title(rng),
                        start=now + timedelta(minutes=110 * (n // max(channels, 1)) - 30),
                        stop=now + timedelta(minutes=110 * (n // max(channels, 1)) + 80),
                    )
                    for n in range(start, min(start + 5000, programmes))
                ],
            )
        await s.commit()


class SlowMetadata:
    """A metadata provider that answers, slowly: every call to it is counted and takes ``delay`` seconds."""

    enabled = True

    def __init__(self, delay: float = 0.8, results: int = 20) -> None:
        self.delay, self.results = delay, results
        self.calls: list[tuple[str, Any]] = []

    async def _wait(self, name: str, what: Any) -> None:
        self.calls.append((name, what))
        await asyncio.sleep(self.delay)

    async def search(self, query: str, limit: int = 20) -> list[MetaTitle]:
        await self._wait("search", query)
        # Titles nobody has, named after the query, as a provider's answers are.
        return [
            MetaTitle(
                tmdb_id=900000 + n,
                media_type="movie" if n % 2 else "tv",
                title=f"{query.title()} {WORDS[n * 7].title()}",
                year=2000 + n,
                popularity=float(self.results - n),
            )
            for n in range(self.results)
        ][:limit]

    async def details(self, media_type: str, tmdb_id: int) -> MetaTitle | None:
        await self._wait("details", (media_type, tmdb_id))
        return MetaTitle(tmdb_id=tmdb_id, media_type=media_type, title=f"Title {tmdb_id}", year=2001)

    async def watch_providers(self, media_type: str, tmdb_id: int, region: str) -> list[ProviderOffer]:
        await self._wait("watch_providers", (media_type, tmdb_id, region))
        return []
