"""Movies browse: either direction of each sort, no sports replays, and the side rail's jumps."""

from datetime import UTC, date, datetime

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


async def test_jumps_say_where_each_letter_and_year_starts(api: Api, monkeypatch) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 15, tzinfo=tz)

    monkeypatch.setattr("app.services.catalog.datetime", Clock)
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
    assert (
        jump_label("year", None) == "Unknown"
        and jump_label("added", "2026-11-03 10:00:00", today=date(2026, 11, 5)) == "Nov 2026"
    )


@pytest.mark.parametrize(("section", "kind"), [("movies", "vod"), ("shows", "series")])
async def test_same_year_release_order_is_independent_of_addition(
    api: Api, section: str, kind: str, monkeypatch
) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 11, 15, tzinfo=tz)

    monkeypatch.setattr("app.services.catalog.datetime", Clock)
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


@pytest.mark.parametrize(("section", "kind", "canonical_kind"), [("movies", "vod", "movie"), ("shows", "series", "tv")])
async def test_release_grid_uses_canonical_date_before_paging_and_sparse_calendar_jumps(
    api: Api, section, kind, canonical_kind, monkeypatch
):
    from app.services.release_dates import ReleaseDates

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2027, 1, 6, tzinfo=tz)

    monkeypatch.setattr("app.services.catalog.datetime", Clock)
    await seed_catalog(api)
    rows = [
        ("Future project", 2055, None, None),
        ("Newest", 2027, "2027-01-01", None),
        ("December", 2026, "2026-12-31", None),
        ("Lower monthly boundary", 2026, "2026-02-01", None),
        ("Just before boundary", 2026, "2026-01-31", None),
        ("Same older year", 2026, "2026-01-01", None),
        ("Death Race 2050" if section == "movies" else "Series 2050", 2050, None, 116),
        ("Undated", None, None, None),
    ]
    async with api.c.db.session() as s:
        for i, (title, year, day, tmdb) in enumerate(rows):
            s.add(
                Title(
                    source_id="catalog",
                    ext_id=str(i),
                    kind=kind,
                    tmdb_id=tmdb,
                    title=title,
                    year=year,
                    language="eng",
                    extra={"originally_available_at": day},
                )
            )
        await s.commit()
    indexed = ReleaseDates(api.c.db)
    await indexed.record("tv" if canonical_kind == "movie" else "movie", 116, "2080-01-01")
    await indexed.record(canonical_kind, 116, "2017-01-17")
    for direction, labels in [
        (
            "desc",
            [("2055", 0), ("Jan 2027", 1), ("Dec 2026", 2), ("Feb 2026", 3), ("2026", 4), ("2017", 6), ("Unknown", 7)],
        ),
        (
            "asc",
            [("2017", 0), ("2026", 1), ("Feb 2026", 3), ("Dec 2026", 4), ("Jan 2027", 5), ("2055", 6), ("Unknown", 7)],
        ),
    ]:
        options = {"sort": "release", "order": direction}
        jumps = (await api.get(f"/browse/{section}/jumps", params=options)).json()["jumps"]
        assert [(j["label"], j["offset"]) for j in jumps] == labels
        tiles = (await api.get(f"/browse/{section}", params=options)).json()["tiles"]
        matched = tiles[next(offset for label, offset in labels if label == "2017")]
        assert (matched["title"], matched["year"]) == (rows[6][0], 2017)
        assert tiles[-1]["year"] is None
    years = (await api.get(f"/browse/{section}/jumps", params={"sort": "year"})).json()["jumps"]
    assert [j["label"] for j in years] == ["2055", "2027", "2026", "2017", "Unknown"]


def test_upcoming_month_and_year_only_dates_keep_separate_anchors():
    today = date(2026, 11, 26)
    assert [
        jump_label("release", v, today=today)
        for v in ("2027-04-03", "2026-12-14", "2026-11-25", "2026", "2025-12-01", "2025-11-30")
    ] == ["2027", "Later 2026", "Nov 2026", "2026", "Dec 2025", "2025"]
