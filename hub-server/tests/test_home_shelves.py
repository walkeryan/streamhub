"""Home is composed from shelves: row order, the hero's source and the owner's override."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import insert

from app.infra.models import Channel, ChannelGroup, Favorite, Programme, Progress, Setting, Source, Title
from app.services.ids import normalise_title
from tests.conftest import Api

NOW = datetime.now(UTC)


def rows_for(shelf: str, count: int, *, kind: str = "vod", age_days: float = 1, **kw: Any) -> list[dict[str, Any]]:
    """``count`` titles on one shelf, the first being the most recently added."""
    slug = f"{kind}-{shelf.lower().replace(' ', '-')}"
    return [
        {
            "source_id": "catalog",
            "ext_id": f"{slug}-{i}",
            "kind": kind,
            "title": f"{shelf} {kind} {i}",
            "norm_title": normalise_title(f"{shelf} {kind} {i}"),
            "group_name": shelf,
            "language": "eng",
            "genres": [],
            "poster": f"https://art.example.test/{slug}-{i}.jpg",
            "added_at": NOW - timedelta(days=age_days, minutes=i),
            **kw,
        }
        for i in range(count)
    ]


async def seed(api: Api, titles: list[dict[str, Any]]) -> None:
    async with api.c.db.session() as s:
        s.add(Source(id="catalog", name="Catalog", type="xtream", config_enc="unused", counts={"vod": len(titles)}))
        await s.flush()
        await s.execute(insert(Title), titles)
        await s.commit()
    await api.patch(f"/profiles/{api.profile_id}", json={"languages": ["eng"]})


async def home_rows(api: Api, **params: str) -> tuple[dict[str, Any], list[str]]:
    home = (await api.get("/home", params=params)).json()
    return home, [row["id"] for row in home["rows"]]


async def test_rows_follow_shelves_then_recently_added(api: Api) -> None:
    await seed(
        api,
        rows_for("New Releases", 3)
        + rows_for("Drama", 5)
        + rows_for("Action", 4)
        + rows_for("Netflix", 2)
        # Larger than Drama, but nothing new for months: it counts half.
        + rows_for("Older Favorites", 6, age_days=200)
        + rows_for("TV Shows", 6, kind="series")
        + rows_for("Netflix", 3, kind="series"),
    )
    async with api.c.db.session() as s:
        s.add(ChannelGroup(id="news", source_id="catalog", name="News"))
        await s.flush()
        for number, ext in enumerate(["plain", "starred"], start=1):
            s.add(
                Channel(
                    source_id="catalog",
                    ext_id=ext,
                    name=f"Channel {ext}",
                    norm_name=f"channel {ext}",
                    number=number,
                    group_id="news",
                    group_name="News",
                    language="eng",
                    epg_id=f"epg-{ext}",
                )
            )
            s.add(
                Programme(
                    feed="catalog",
                    epg_channel=f"epg-{ext}",
                    title=f"Show {ext}",
                    start=NOW - timedelta(minutes=10),
                    stop=NOW + timedelta(hours=2),
                )
            )
        s.add(Favorite(profile_id=api.profile_id, kind="channel", ref_id="ch:catalog:starred"))
        s.add(
            Progress(
                profile_id=api.profile_id, ref_id="lib:catalog:vod-drama-1", position_ms=60_000, duration_ms=90_000
            )
        )
        await s.commit()

    home, ids = await home_rows(api)
    assert ids == [
        "continue_watching",
        "on_now",
        "shelf:movies:New Releases",
        "shelf:shows:TV Shows",
        "shelf:movies:Drama",
        "shelf:shows:Netflix",
        "shelf:movies:Action",
        "shelf:movies:Older Favorites",
        "shelf:movies:Netflix",
        "recommendations",
        "recently_added",
    ]
    rows = {row["id"]: row for row in home["rows"]}
    assert rows["on_now"]["title"] == "On Now" and rows["on_now"]["browse_path"] == "/live"
    assert [t["id"] for t in rows["on_now"]["tiles"]] == ["ch:catalog:starred", "ch:catalog:plain"]
    new = rows["shelf:movies:New Releases"]
    assert new["title"] == "New Releases" and new["layout"] == "poster"
    assert new["browse_path"] == "/browse/movies?category=New%20Releases"
    assert [t["id"] for t in new["tiles"]] == [f"lib:catalog:vod-new-releases-{i}" for i in range(3)]
    # A name on both sides says which one this row is.
    assert rows["shelf:shows:Netflix"]["title"] == "Netflix · Shows"
    assert rows["shelf:movies:Netflix"]["title"] == "Netflix · Movies"
    assert rows["shelf:shows:Netflix"]["browse_path"] == "/browse/shows?category=Netflix"
    assert rows["continue_watching"].get("browse_path") is None
    assert len(rows["recently_added"]["tiles"]) == 29


async def test_at_most_eight_shelf_rows_alternate_after_new_releases(api: Api) -> None:
    titles = rows_for("New Releases", 2)
    for i in range(6):
        titles += rows_for(f"Movie Shelf {i}", 12 - i) + rows_for(f"Show Shelf {i}", 12 - i, kind="series")
    await seed(api, titles)
    _, ids = await home_rows(api)
    shelves = [rid for rid in ids if rid.startswith("shelf:")]
    assert shelves == ["shelf:movies:New Releases"] + [
        f"shelf:{section}:{name} Shelf {i}"
        for i in range(4)
        for section, name in (("shows", "Show"), ("movies", "Movie"))
    ]
    assert ids[-1] == "recently_added"


async def test_hero_comes_from_new_releases_with_backdrop_and_rating(api: Api) -> None:
    art = {"backdrop": "https://art.example.test/wide.jpg"}
    new = rows_for("New Releases", 6, **art, rating=7.5)
    new[1]["rating"] = 5.9  # rated too low
    new[2]["backdrop"] = None  # nothing to fill a billboard with
    new[3]["rating"] = None
    new[4]["adult"] = True
    await seed(api, new + rows_for("Sports Replays", 9, **art, rating=9.0) + rows_for("Drama", 4, **art, rating=8.0))

    home, _ = await home_rows(api)
    assert [t["id"] for t in home["hero"]] == ["lib:catalog:vod-new-releases-0", "lib:catalog:vod-new-releases-5"]
    # The admin's opt-in lists adult rows; the billboard still never shows one.
    adult, _ = await home_rows(api, include_adult="true")
    assert [t["id"] for t in adult["hero"]] == ["lib:catalog:vod-new-releases-0", "lib:catalog:vod-new-releases-5"]


async def test_hero_uses_the_largest_movie_shelf_but_never_sports_replays(api: Api) -> None:
    art = {"backdrop": "https://art.example.test/wide.jpg", "rating": 7.0}
    await seed(
        api,
        rows_for("Sports Replays", 12, **art)
        + rows_for("Drama", 10, **art)
        + rows_for("Action", 3, **art)
        + rows_for("TV Shows", 20, kind="series", **art),
    )
    home, _ = await home_rows(api)
    assert [t["id"] for t in home["hero"]] == [f"lib:catalog:vod-drama-{i}" for i in range(8)]


async def test_hero_falls_back_when_the_shelf_has_no_billboard_art(api: Api) -> None:
    await seed(api, rows_for("New Releases", 2) + rows_for("Drama", 5, age_days=0.5))
    home, _ = await home_rows(api)
    # Posters only: stay on the shelf rather than whatever was added last.
    assert [t["id"] for t in home["hero"]] == ["lib:catalog:vod-new-releases-0", "lib:catalog:vod-new-releases-1"]


async def test_owner_setting_overrides_the_automatic_shelves(api: Api) -> None:
    await seed(
        api,
        rows_for("New Releases", 2)
        + rows_for("Drama", 9)
        + rows_for("Action", 3)
        + rows_for("Netflix", 4)
        + rows_for("Netflix", 5, kind="series")
        + rows_for("TV Shows", 8, kind="series"),
    )
    async with api.c.db.session() as s:
        s.add(Setting(key="home_shelves", value=["Action", "shows:Netflix", "No Such Shelf", "New Releases"]))
        await s.commit()
    _, ids = await home_rows(api)
    assert [rid for rid in ids if rid.startswith("shelf:")] == [
        "shelf:movies:New Releases",
        "shelf:movies:Action",
        "shelf:shows:Netflix",
    ]
    # An unqualified name stands for that shelf wherever it exists.
    async with api.c.db.session() as s:
        (await s.get(Setting, "home_shelves")).value = ["Netflix"]
        await s.commit()
    _, ids = await home_rows(api)
    assert [rid for rid in ids if rid.startswith("shelf:")] == [
        "shelf:movies:New Releases",
        "shelf:movies:Netflix",
        "shelf:shows:Netflix",
    ]


async def test_saved_layout_can_hide_and_move_shelf_rows(api: Api) -> None:
    await seed(api, rows_for("New Releases", 2) + rows_for("Drama", 4) + rows_for("TV Shows", 3, kind="series"))
    layout = [{"id": "shelf:movies:Drama", "hidden": True}, {"id": "recently_added", "sort": 1}]
    assert (await api.put(f"/profiles/{api.profile_id}/home_layout", json={"rows": layout})).status_code == 200
    _, ids = await home_rows(api)
    assert ids == ["recently_added", "shelf:movies:New Releases", "shelf:shows:TV Shows", "recommendations"]


# ---------------------------------------------------------------- the setting itself
async def test_home_shelves_is_set_through_the_settings_api(api: Api) -> None:
    await seed(
        api,
        rows_for("New Releases", 2)
        + rows_for("Drama", 9)
        + rows_for("Action", 3)
        + rows_for("Netflix", 4)
        + rows_for("Netflix", 5, kind="series"),
    )
    before = (await api.get("/settings")).json()
    assert (before["home_shelves"], before["home_shelves_unknown"]) == ([], [])

    wanted = [" Action ", "shows:Netflix", "No Such Shelf", "movies:TV Classics", "shows:Drama", "Action", " "]
    r = await api.patch("/settings", json={"home_shelves": wanted})
    assert r.status_code == 200, r.text
    # Trimmed, blanks and repeats dropped, order kept. Names no shelf has are kept and pointed out:
    # a shelf can turn up with the next sync. "shows:Drama" is unknown because Drama is a movie shelf.
    kept = ["Action", "shows:Netflix", "No Such Shelf", "movies:TV Classics", "shows:Drama"]
    assert r.json()["home_shelves"] == kept
    assert r.json()["home_shelves_unknown"] == ["No Such Shelf", "movies:TV Classics", "shows:Drama"]
    assert (await api.get("/settings")).json()["home_shelves"] == kept
    _, ids = await home_rows(api)
    assert [rid for rid in ids if rid.startswith("shelf:")] == [
        "shelf:movies:New Releases",
        "shelf:movies:Action",
        "shelf:shows:Netflix",
    ]
    # Once the shelf exists it is no longer unknown, and Home shows it where the list has it.
    async with api.c.db.session() as s:
        await s.execute(insert(Title), rows_for("No Such Shelf", 2))
        await s.commit()
    assert (await api.get("/settings")).json()["home_shelves_unknown"] == ["movies:TV Classics", "shows:Drama"]
    _, ids = await home_rows(api)
    assert [rid for rid in ids if rid.startswith("shelf:")][-1] == "shelf:movies:No Such Shelf"
    # Other settings leave it alone; null or an empty list hands the choice back to Home.
    assert (await api.patch("/settings", json={"region": "GB"})).json()["home_shelves"] == kept
    for cleared in (None, []):
        assert (await api.patch("/settings", json={"home_shelves": ["Action"]})).json()["home_shelves"] == ["Action"]
        r = await api.patch("/settings", json={"home_shelves": cleared})
        assert (r.json()["home_shelves"], r.json()["home_shelves_unknown"]) == ([], [])
    _, ids = await home_rows(api)
    assert "shelf:movies:Drama" in ids


async def test_home_shelves_is_validated_and_the_administrators(api: Api) -> None:
    from tests.people import member

    for bad in ("Action", [1, 2], ["x" * 201], [f"Shelf {i}" for i in range(25)], {"names": ["Action"]}):
        r = await api.patch("/settings", json={"home_shelves": bad})
        assert r.status_code == 422, (bad, r.text)
    assert (await api.get("/settings")).json()["home_shelves"] == []
    assert (await api.patch("/settings", json={"home_shelves": ["Action"]})).status_code == 200
    parent = await member(api, "family", "Parent")
    r = await parent.req("PATCH", "/settings", json={"home_shelves": ["Drama"]})
    assert r.status_code == 403 and r.json()["missing"] == ["system.manage"]
    seen = (await parent.get("/settings")).json()
    assert seen["home_shelves"] is None and seen["home_shelves_unknown"] is None
    assert (await api.get("/settings")).json()["home_shelves"] == ["Action"]
    # It is a stored setting: a restart reads it back.
    await api.c.app_settings.load()
    assert api.c.app_settings.public()["home_shelves"] == ["Action"]
