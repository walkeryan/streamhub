"""Movies browse: either direction of each sort, no sports replays, and the side rail's jumps."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from app.infra.models import Title
from app.services.catalog import jump_label
from app.services.ids import normalise_title
from tests.conftest import Api
from tests.test_large_catalog_filters import seed_catalog

FILMS = [
    # title, year, rating, added day, category
    ("01. The Shawshank Redemption", 1994, 9.3, 1, "Top 250"),
    ("Alien", 1979, 8.5, 2, "Movies"),
    ("Brazil", 1985, 7.9, 3, "Movies"),
    ("Casablanca", 1942, 8.5, 4, "Older Favorites"),
    ("Zodiac", 2007, None, 5, "Movies"),
    ("Duke at NC State 02.03.2026", 2026, None, 6, "Sports Replays"),
    ("WWE SmackDown (07/03/2025)", 2025, None, 7, "WWE"),
    ("UFC 300", 2024, None, 8, "UFC"),
]


async def seed(api: Api) -> None:
    await seed_catalog(api)
    async with api.c.db.session() as s:
        await s.execute(
            insert(Title),
            [
                dict(
                    source_id="catalog",
                    ext_id=str(i),
                    kind="vod",
                    title=title,
                    norm_title=normalise_title(title),
                    year=year,
                    rating=rating,
                    added_at=datetime(2026, 10, day, tzinfo=UTC),
                    group_name=group,
                    language="eng",
                )
                for i, (title, year, rating, day, group) in enumerate(FILMS)
            ],
        )
        await s.commit()


async def titles(api: Api, **params: str) -> list[str]:
    body = (await api.get("/browse/movies", params={"language": "all", **params})).json()
    return [t["title"] for t in body["tiles"]]


async def test_movies_leave_out_sports_categories(api: Api) -> None:
    await seed(api)
    shown = await titles(api, sort="title")
    assert len(shown) == 5 and not any(w in " ".join(shown) for w in ("Duke", "WWE", "UFC"))
    categories = (await api.get("/browse/movies/categories", params={"language": "all"})).json()
    assert {c["name"] for c in categories} == {"Top 250", "Movies", "Older Favorites"}


async def test_each_sort_runs_both_ways(api: Api) -> None:
    await seed(api)
    # A list's rank is not part of a title's name.
    assert await titles(api, sort="title") == [
        "Alien",
        "Brazil",
        "Casablanca",
        "01. The Shawshank Redemption",
        "Zodiac",
    ]
    assert (await titles(api, sort="title", order="desc"))[0] == "Zodiac"
    assert await titles(api, sort="year") == ["Zodiac", "01. The Shawshank Redemption", "Brazil", "Alien", "Casablanca"]
    assert (await titles(api, sort="year", order="asc"))[0] == "Casablanca"
    # Unrated titles stay last whichever way.
    assert (await titles(api, sort="rating"))[0] == "01. The Shawshank Redemption"
    assert (await titles(api, sort="rating", order="asc"))[:2] == ["Brazil", "Alien"]
    assert (await titles(api, sort="rating", order="asc"))[-1] == "Zodiac"
    assert (await titles(api))[0] == "Zodiac" and (await titles(api, order="asc"))[0] == "01. The Shawshank Redemption"
    assert (await api.get("/browse/movies", params={"order": "up"})).status_code == 422


async def test_jumps_say_where_each_letter_and_year_starts(api: Api) -> None:
    await seed(api)
    body = (await api.get("/browse/movies/jumps", params={"language": "all", "sort": "title"})).json()
    assert body["sort"] == "title" and body["page_size"] == 60
    assert [(j["label"], j["offset"]) for j in body["jumps"]] == [("A", 0), ("B", 1), ("C", 2), ("S", 3), ("Z", 4)]
    years = (await api.get("/browse/movies/jumps", params={"language": "all", "sort": "year", "order": "asc"})).json()
    assert [j["label"] for j in years["jumps"]] == ["1942", "1979", "1985", "1994", "2007"]
    added = (await api.get("/browse/movies/jumps", params={"language": "all"})).json()
    assert [(j["label"], j["offset"]) for j in added["jumps"]] == [("Oct 2026", 0)]


def test_jump_labels() -> None:
    assert [jump_label("title", v) for v in ("alien", "9 to 5", "", None)] == ["A", "#", "#", "#"]
    assert jump_label("rating", 8.7) == "8+" and jump_label("rating", None) == "Unrated"
    assert jump_label("year", None) == "Unknown" and jump_label("added", "2026-11-03 10:00:00") == "Nov 2026"


@pytest.mark.parametrize(("section", "kind"), [("movies", "vod"), ("shows", "series")])
async def test_same_year_release_order_is_independent_of_addition(api: Api, section: str, kind: str) -> None:
    await seed_catalog(api)
    async with api.c.db.session() as session:
        for title, release, added in [("Winter", "2025-01-17", 3), ("Summer", "2025-07-02", 1), ("Undated", None, 2)]:
            session.add(
                Title(
                    source_id="catalog",
                    ext_id=title,
                    kind=kind,
                    title=title,
                    norm_title=title.lower(),
                    year=2025 if release else None,
                    language="eng",
                    added_at=datetime(2026, 1, added, tzinfo=UTC),
                    extra={"originally_available_at": release},
                )
            )
        await session.commit()
    for sort, order, expected in [
        ("release", "desc", ["Summer", "Winter", "Undated"]),
        ("release", "asc", ["Winter", "Summer", "Undated"]),
        ("added", "desc", ["Winter", "Undated", "Summer"]),
        ("title", "asc", ["Summer", "Undated", "Winter"]),
    ]:
        response = await api.get(f"/browse/{section}", params={"sort": sort, "order": order})
        assert [row["title"] for row in response.json()["tiles"]] == expected
    jumps = await api.get(f"/browse/{section}/jumps", params={"sort": "release"})
    assert [r["label"] for r in jumps.json()["jumps"]] == ["Jul 2025", "Jan 2025", "Unknown"]
