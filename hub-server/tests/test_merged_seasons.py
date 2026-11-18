"""A series' title page lists the seasons of every library copy, not of the first one found."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.infra.models import Title
from app.services.catalog import CatalogService


def season(source: str, show: str, number: int | None) -> Title:
    return Title(
        source_id=source,
        ext_id=f"{show}:s{number}",
        kind="season",
        title=f"Season {number}",
        parent_ext_id=show,
        season_number=number,
    )


def episode(source: str, show: str, number: int, ep: int) -> Title:
    return Title(
        source_id=source,
        ext_id=f"{show}:s{number}e{ep}",
        kind="episode",
        title=f"Episode {ep}",
        parent_ext_id=f"{show}:s{number}",
        season_number=number,
        episode_number=ep,
    )


def show(source: str, ext: str) -> Title:
    return Title(source_id=source, ext_id=ext, kind="series", title="A Fixture Series")


def catalog(children: dict[tuple[str, str], list[Title]]) -> CatalogService:
    service = CatalogService(None, None, SimpleNamespace(), None, None)  # type: ignore[arg-type]

    async def children_rows(row: Title) -> list[Title]:
        return children.get((row.source_id, row.ext_id), [])

    service.children_rows = children_rows  # type: ignore[method-assign]
    return service


CTX: Any = SimpleNamespace(title_tile=lambda row: {"title": row.title, "sources": []})


async def test_the_copy_with_most_seasons_gives_them_and_another_fills_its_gaps() -> None:
    provider = show("iptv", "p")  # carries only season 2, and first in the source order
    library = show("lib", "l")  # has seasons 1, 3 and 4 (and 2)
    extra = show("iptv2", "x")  # the only one with season 5
    service = catalog(
        {
            ("iptv", "p"): [season("iptv", "p", 2), episode("iptv", "p", 2, 1)],
            ("lib", "l"): [season("lib", "l", n) for n in (1, 2, 3, 4)],
            ("lib", "l:s1"): [episode("lib", "l", 1, 1), episode("lib", "l", 1, 2)],
            ("iptv2", "x"): [season("iptv2", "x", 5)],
        }
    )
    seasons, episodes = await service._merged_seasons([provider, library, extra], CTX)
    assert [s["id"] for s in seasons] == [
        "lib:lib:l:s1",
        "lib:lib:l:s2",
        "lib:lib:l:s3",
        "lib:lib:l:s4",
        "lib:iptv2:x:s5",
    ]
    # Before a season is picked: the first season's episodes, fetched from the copy that gave it.
    assert [e["id"] for e in episodes] == ["lib:lib:l:s1e1", "lib:lib:l:s1e2"]


async def test_one_copy_is_unchanged_and_no_seasons_keeps_its_episodes() -> None:
    only = show("iptv", "p")
    service = catalog({("iptv", "p"): [season("iptv", "p", 2), episode("iptv", "p", 2, 1), episode("iptv", "p", 2, 2)]})
    seasons, episodes = await service._merged_seasons([only], CTX)
    assert [s["title"] for s in seasons] == ["Season 2"]
    assert [e["title"] for e in episodes] == ["Episode 1", "Episode 2"]
    flat = catalog({("iptv", "p"): [episode("iptv", "p", 1, 1)]})
    assert await flat._merged_seasons([only], CTX) == (
        [],
        [
            {
                "title": "Episode 1",
                "sources": [],
                "id": "lib:iptv:p:s1e1",
                "kind": "episode",
                "season_number": 1,
                "episode_number": 1,
                "runtime_min": None,
            }
        ],
    )
