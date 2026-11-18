"""The title page's server side: parent links, the household's activity and "More like this".

Everything here is invented: a fictional series, film and household.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.adapters.tmdb.client import TmdbClient
from app.domain.entities import MetaTitle
from app.domain.ports import AdapterContext
from app.infra.cache import MemoryCache
from app.infra.models import Progress, Source, Title, Watchlist
from app.services.ids import normalise_title
from app.services.title_extras import ACTIVITY_LIMIT, similar_titles, title_activity
from tests.conftest import Api, http_factory
from tests.people import member
from tests.test_rating_ceiling import rated

NOW = datetime.now(UTC)
SHOW = "Harbor Watch"


def title(ext: str, name: str, *, kind: str = "vod", **kw: Any) -> Title:
    return Title(source_id="catalog", ext_id=ext, kind=kind, title=name, norm_title=normalise_title(name), **kw)


def episode(season: int, number: int, name: str, **kw: Any) -> Title:
    return title(
        f"hw-s{season}e{number}",
        f"{SHOW} - S{season:02d}E{number:02d} - {name}",
        kind="episode",
        parent_ext_id=f"hw-s{season}",
        season_number=season,
        episode_number=number,
        duration_ms=50 * 60_000,
        **kw,
    )


@pytest.fixture
async def seeded(api: Api) -> Api:
    async with api.c.db.session() as s:
        s.add(Source(id="catalog", name="Catalog", type="xtream", config_enc="unused", counts={"vod": 4}))
        await s.flush()
        s.add_all(
            [
                title("hw", SHOW, kind="series", tmdb_id=501, backdrop="https://art.example.test/hw.jpg"),
                title("hw-s1", "Season 1", kind="season", parent_ext_id="hw", season_number=1),
                title("hw-s2", "Season 2", kind="season", parent_ext_id="hw", season_number=2),
                episode(1, 1, "Low Tide"),
                episode(1, 2, "Fog Bell"),
                episode(2, 1, "The Crossing", extra={"originally_available_at": "2026-02-14"}),
                episode(2, 2, "Salt Lines"),
                episode(2, 3, "Breakwater"),
                title("lanterns", "Paper Lanterns", tmdb_id=601, year=2024),
                title("quiet", "Quiet Harbor", kind="series", tmdb_id=701),
                title("velvet", "Velvet Hours", kind="series", tmdb_id=703, adult=True),
                title("late", "Late Shift", kind="series", adult=True),
                title("late-s1", "Season 1", kind="season", parent_ext_id="late", season_number=1, adult=True),
                title(
                    "late-e1",
                    "Late Shift - S01E01 - Doors",
                    kind="episode",
                    parent_ext_id="late-s1",
                    season_number=1,
                    episode_number=1,
                    adult=True,
                ),
                rated("tmdb:tv:501", "TV-PG"),
                rated("tmdb:movie:601", "PG"),
                rated("tmdb:movie:801", "PG"),
                rated("tmdb:movie:802", "R"),
            ]
        )
        await s.commit()
    return api


# ------------------------------------------------------------------ parent links
async def test_an_episode_names_its_show_season_and_place(seeded: Api) -> None:
    d = (await seeded.get("/titles/lib:catalog:hw-s2e1")).json()
    assert d["parent"] == {
        "show": {"id": "tmdb:tv:501", "title": SHOW, "backdrop": "https://art.example.test/hw.jpg"},
        "season": {"id": "lib:catalog:hw-s2", "title": "Season 2", "number": 2},
        "episode_number": 1,
        "episode_count": 3,
        "episode_title": "The Crossing",
    }
    assert d["content_rating"] == "TV-PG"
    assert d["release_date"] == "2026-02-14"


async def test_a_season_names_its_show_and_numbers_its_episodes(seeded: Api) -> None:
    d = (await seeded.get("/titles/lib:catalog:hw-s2")).json()
    assert d["parent"]["show"]["id"] == "tmdb:tv:501"
    assert d["parent"]["season"] == {"id": "lib:catalog:hw-s2", "title": "Season 2", "number": 2}
    assert d["parent"]["episode_number"] is None and d["parent"]["episode_count"] is None
    assert [(e["season_number"], e["episode_number"], e["runtime_min"]) for e in d["episodes"]] == [
        (2, 1, 50),
        (2, 2, 50),
        (2, 3, 50),
    ]
    assert [e["title"] for e in d["episodes"]] == ["E1 · The Crossing", "E2 · Salt Lines", "E3 · Breakwater"]


async def test_films_and_series_have_no_parent(seeded: Api) -> None:
    assert (await seeded.get("/titles/lib:catalog:lanterns")).json()["parent"] is None
    assert (await seeded.get("/titles/lib:catalog:hw")).json()["parent"] is None


# ------------------------------------------------------------------ activity
async def household(api: Api) -> dict[str, Any]:
    """An owner (the fixture), a grown-up, two kids, and a friend who is not on shared devices."""
    people = {
        "adult": await member(api, "family", "Avery"),
        "kid": await member(api, "kid", "Robin"),
        "kid2": await member(api, "kid", "Jo"),
        "friend": await member(api, "friend", "Sam"),
    }
    owner = api.profile_id

    def progress(who: str, ref: str, minutes: int, *, done: bool = False, hours_ago: float) -> Progress:
        return Progress(
            profile_id=who,
            ref_id=ref,
            position_ms=0 if done else minutes * 60_000,
            duration_ms=50 * 60_000,
            completed=done,
            updated_at=NOW - timedelta(hours=hours_ago),
        )

    async with api.c.db.session() as s:
        s.add_all(
            [
                progress(people["adult"].profile_id, "lib:catalog:hw-s2e1", 10, hours_ago=1),
                # The grown-up's earlier episode is not a second entry: one per profile, their newest.
                progress(people["adult"].profile_id, "lib:catalog:hw-s1e2", 0, done=True, hours_ago=30),
                Watchlist(profile_id=owner, ref_id="tmdb:tv:501", added_at=NOW - timedelta(hours=3)),
                progress(people["kid2"].profile_id, "lib:catalog:hw-s1e2", 0, done=True, hours_ago=48),
                progress(people["friend"].profile_id, "lib:catalog:hw-s2e2", 20, hours_ago=0.5),
                # An unwatched mark (position 0, not completed) is not activity.
                progress(people["kid"].profile_id, "lib:catalog:hw-s2e3", 0, hours_ago=0.2),
                # Viewing of the adult series, which must never be shown.
                progress(people["adult"].profile_id, "lib:catalog:late-e1", 5, hours_ago=0.1),
            ]
        )
        await s.commit()
    return people


async def test_activity_lists_the_household_newest_first(seeded: Api) -> None:
    people = await household(seeded)
    r = await seeded.get("/titles/lib:catalog:hw-s2e2/activity")
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    shown = [(e["name"], e["action"], e["me"]) for e in entries]
    # The friend is not on "Who's watching?", so their viewing is not shown; the unwatched mark is not activity.
    assert [name for name, _, _ in shown] == ["Avery", seeded_owner_name(entries), "Jo"]
    adult, mine, kid = entries
    assert (adult["action"], adult["season_number"], adult["episode_number"]) == ("watching", 2, 1)
    assert adult["progress_pct"] == 20.0 and adult["minutes_left"] == 40
    assert (mine["action"], mine["me"]) == ("listed", True)
    assert (kid["action"], kid["season_number"], kid["season_finished"]) == ("finished", 1, True)
    assert kid["profile_id"] == people["kid2"].profile_id
    # The series page and its film id say the same.
    assert (await seeded.get("/titles/tmdb:tv:501/activity")).json()["entries"] == entries


def seeded_owner_name(entries: list[dict[str, Any]]) -> str:
    return next(e["name"] for e in entries if e["me"])


async def test_kids_do_not_see_grown_ups_activity(seeded: Api) -> None:
    people = await household(seeded)
    r = await people["kid"].get("/titles/lib:catalog:hw-s2e2/activity")
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    assert [(e["name"], e["action"]) for e in entries] == [("Jo", "finished")]
    # A grown-up still sees the kids.
    names = {e["name"] for e in (await people["adult"].get("/titles/lib:catalog:hw/activity")).json()["entries"]}
    assert "Jo" in names and "Avery" in names


async def test_adult_titles_show_no_activity(seeded: Api) -> None:
    await household(seeded)
    # Whoever asks, the series, its season and its episode have nothing to show...
    for ref in ("lib:catalog:late", "lib:catalog:late-s1", "lib:catalog:late-e1"):
        assert (await title_activity(seeded.c.catalog, ref, seeded.profile_id))["entries"] == []
    # ...and over HTTP a locked session is turned away before anything is said.
    r = await seeded.get("/titles/lib:catalog:late-e1/activity")
    assert r.status_code == 403, r.text


async def test_activity_is_at_most_six_entries(seeded: Api) -> None:
    others = [await member(seeded, "family", f"Viewer {i}") for i in range(ACTIVITY_LIMIT + 2)]
    async with seeded.c.db.session() as s:
        for i, who in enumerate(others):
            s.add(Watchlist(profile_id=who.profile_id, ref_id="tmdb:tv:501", added_at=NOW - timedelta(minutes=i)))
        await s.commit()
    entries = (await seeded.get("/titles/tmdb:tv:501/activity")).json()["entries"]
    assert [e["name"] for e in entries] == [f"Viewer {i}" for i in range(ACTIVITY_LIMIT)]


# ------------------------------------------------------------------ more like this
def meta(tmdb_id: int, name: str, media_type: str = "tv") -> MetaTitle:
    return MetaTitle(tmdb_id=tmdb_id, media_type=media_type, title=name, poster=f"https://img.example.test/{tmdb_id}")


def fake_metadata(api: Api, metas: list[MetaTitle]) -> AsyncMock:
    recommendations = AsyncMock(return_value=metas)
    api.c.catalog.meta = SimpleNamespace(enabled=True, recommendations=recommendations)  # type: ignore[assignment]
    return recommendations


async def test_more_like_this_ticks_what_plays_here_and_leaves_adult_out(seeded: Api) -> None:
    asked = fake_metadata(
        seeded,
        [
            meta(701, "Quiet Harbor"),
            meta(702, "Night Market"),
            meta(703, "Velvet Hours"),  # a library row of it is marked adult
            meta(501, SHOW),  # the title itself
            meta(905, "Some Film", "movie"),  # another media type
        ],
    )
    r = await seeded.get("/titles/lib:catalog:hw-s2e1/similar")
    assert r.status_code == 200, r.text
    tiles = r.json()["tiles"]
    assert [t["id"] for t in tiles] == ["tmdb:tv:701", "tmdb:tv:702"]
    assert [any(s["playable_in_app"] for s in t["sources"]) for t in tiles] == [True, False]
    # An episode asks for its series' recommendations.
    asked.assert_awaited_with("tv", 501, 20)


async def test_more_like_this_is_empty_for_adult_titles_and_without_metadata(seeded: Api) -> None:
    asked = fake_metadata(seeded, [meta(701, "Quiet Harbor")])
    for ref in ("lib:catalog:late", "lib:catalog:late-e1", "lib:catalog:velvet"):
        assert (await similar_titles(seeded.c.catalog, ref, seeded.profile_id))["tiles"] == []
    asked.assert_not_awaited()
    seeded.c.catalog.meta = SimpleNamespace(enabled=False)  # type: ignore[assignment]
    assert (await seeded.get("/titles/tmdb:tv:501/similar")).json()["tiles"] == []


async def test_more_like_this_follows_the_rating_ceiling(seeded: Api) -> None:
    fake_metadata(seeded, [meta(801, "Kite Season", "movie"), meta(802, "Night Shift Blues", "movie")])
    kid = await member(seeded, "kid", "Robin")
    r = await kid.get("/titles/tmdb:movie:601/similar")
    assert r.status_code == 200, r.text
    assert [t["id"] for t in r.json()["tiles"]] == ["tmdb:movie:801"]
    owner = (await seeded.get("/titles/tmdb:movie:601/similar")).json()["tiles"]
    assert [t["id"] for t in owner] == ["tmdb:movie:801", "tmdb:movie:802"]


async def test_tmdb_recommendations_are_cached_and_drop_adult(tmp_path: Path) -> None:
    client = TmdbClient(
        AdapterContext(http_factory=http_factory, cache=MemoryCache(), data_dir=tmp_path), lambda: "k" * 32
    )
    body = {
        "results": [
            {"id": 11, "name": "Tide Clock", "first_air_date": "2025-03-01", "media_type": "tv"},
            {"id": 12, "name": "Adult Thing", "adult": True, "media_type": "tv"},
            {"id": 13, "title": "A Film", "media_type": "movie"},
        ]
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://api.themoviedb.org/3/tv/501/recommendations").mock(
            return_value=httpx.Response(200, json=body)
        )
        first = await client.recommendations("tv", 501)
        second = await client.recommendations("tv", 501)
    assert [(m.tmdb_id, m.title, m.release_date) for m in first] == [(11, "Tide Clock", "2025-03-01")]
    assert second == first
    assert route.call_count == 1
