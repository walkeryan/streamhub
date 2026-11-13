"""Adult rows stay out of general surfaces; a permitted, unlocked session can ask for them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.domain.entities import MetaTitle
from app.infra.models import Favorite, Progress, Source, Title, Watchlist
from app.services.ids import normalise_title
from tests.conftest import Api

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
ADULT_IDS = {"lib:catalog:night", "lib:catalog:hours", "lib:catalog:hours-e1", "tmdb:movie:900"}
ADULT_TITLES = {"Night Ferry", "After Hours", "Closing Time", "Late Tide"}


def title(ext: str, name: str, *, kind: str = "vod", age_h: int = 5, **kw: Any) -> Title:
    return Title(
        source_id="catalog",
        ext_id=ext,
        kind=kind,
        title=name,
        norm_title=normalise_title(name),
        genres=["Drama"],
        poster=f"https://art.example.test/{ext}.jpg",
        added_at=NOW - timedelta(hours=age_h),
        **kw,
    )


@pytest.fixture
async def seeded(api: Api) -> Api:
    async with api.c.db.session() as s:
        s.add(Source(id="catalog", name="Catalog", type="xtream", config_enc="unused", counts={"vod": 5}))
        await s.flush()
        s.add_all(
            [
                title("harbour", "Harbour Lights", group_name="New Releases", age_h=9),
                title("watched", "Lighthouse Road", group_name="New Releases", age_h=8),
                # Everything adult is newer than the rest, so it would lead every "recent" row.
                title("night", "Night Ferry", group_name="Late Night", age_h=1, adult=True),
                title("tide", "Late Tide", group_name="Late Night", age_h=2, adult=True, tmdb_id=900, year=2020),
                title("hours", "After Hours", kind="series", group_name="Late Night", age_h=3, adult=True),
                title("hours-e1", "Closing Time", kind="episode", parent_ext_id="hours", adult=True),
                # Viewing history that seeds the Drama suggestions, plus saved adult items.
                Progress(
                    profile_id=api.profile_id, ref_id="lib:catalog:watched", position_ms=600_000, duration_ms=900_000
                ),
                Progress(
                    profile_id=api.profile_id, ref_id="lib:catalog:night", position_ms=600_000, duration_ms=900_000
                ),
                Favorite(profile_id=api.profile_id, kind="vod", ref_id="lib:catalog:night"),
                Watchlist(profile_id=api.profile_id, ref_id="tmdb:movie:900"),
            ]
        )
        await s.commit()
    return api


async def guest_headers(api: Api) -> dict[str, str]:
    created = await api.post("/profiles", json={"name": "Guest", "pin": "1234"})
    assert created.status_code == 201, created.text
    login = await api.client.post("/api/v1/auth/login", json={"profile_id": created.json()["id"], "pin": "1234"})
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["access_token"]}


def shown(payload: Any) -> tuple[set[str], set[str]]:
    """Every tile id and title anywhere in a response."""
    found_ids: set[str] = set()
    titles: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            if isinstance(value.get("id"), str) and "title" in value:
                found_ids.add(value["id"])
                titles.add(value["title"])
            for item in value.values():
                walk(item)

    walk(payload)
    return found_ids, titles


def assert_clean(payload: Any) -> None:
    found_ids, titles = shown(payload)
    assert not (found_ids & ADULT_IDS) and not (titles & ADULT_TITLES), (found_ids, titles)


async def test_browse_and_its_facets_hide_adult_rows(seeded: Api) -> None:
    api = seeded
    movies = (await api.get("/browse/movies", params={"language": "all"})).json()
    assert movies["total"] == 2
    assert_clean(movies)
    assert_clean((await api.get("/browse/shows", params={"language": "all"})).json())
    assert_clean((await api.get("/browse/movies", params={"language": "all", "category": "Late Night"})).json())
    categories = (await api.get("/browse/movies/categories", params={"language": "all"})).json()
    assert categories == [{"name": "New Releases", "count": 2}]
    assert (await api.get("/browse/movies/genres", params={"language": "all"})).json() == [
        {"name": "drama", "count": 2}
    ]
    assert (await api.get("/browse/movies/languages")).json() == [{"code": "und", "name": "Unknown", "count": 2}]


async def test_search_hides_adult_rows_and_their_availability(seeded: Api) -> None:
    api = seeded
    for query in ("Night Ferry", "After Hours", "Closing Time", "Late Tide"):
        result = (await api.get("/search", params={"q": query, "language": "all"})).json()
        assert_clean(result)
    # Metadata may still know the film exists; it must not be shown as playable here.
    meta = AsyncMock()
    meta.enabled = True
    meta.search.return_value = [MetaTitle(900, "movie", "Late Tide (Metadata)", 2020)]
    meta.details.return_value = None
    meta.discover.return_value = ([MetaTitle(900, "movie", "Late Tide (Metadata)", 2020)], 1)
    api.c.catalog.meta = meta
    found = (await api.get("/search", params={"q": "Tide", "language": "all"})).json()  # not the cached query
    assert [(t["id"], t["sources"]) for t in found["results"]] == [("tmdb:movie:900", [])]
    assert found["play_here"] == []
    discovered = (await api.get("/discover/movies")).json()
    assert [(t["id"], t["sources"]) for t in discovered["tiles"]] == [("tmdb:movie:900", [])]


async def test_home_rows_and_hero_hide_adult_rows(seeded: Api) -> None:
    api = seeded
    home = (await api.get("/home")).json()
    assert_clean(home)
    rows = {row["id"]: [t["id"] for t in row["tiles"]] for row in home["rows"]}
    assert rows["recently_added"] == ["lib:catalog:watched", "lib:catalog:harbour"]
    assert rows["shelf:movies:New Releases"] == rows["recently_added"]
    assert not any("Late Night" in rid for rid in rows)  # the adult shelf gets no row
    assert "source:catalog" not in rows  # one library source would only repeat Recently Added
    assert rows["continue_watching"] == ["lib:catalog:watched"]
    assert rows["recommendations"] == ["lib:catalog:harbour"]
    assert "favorites" not in rows and "watchlist" not in rows  # only adult items were saved
    assert [t["id"] for t in home["hero"]] == ["lib:catalog:watched", "lib:catalog:harbour"]
    for path in ("/favorites", "/watchlist", "/history"):
        assert_clean((await api.get(path)).json())


async def unlock_adult(api: Api) -> None:
    """The owner holds watch.adult; the session still has to be unlocked with their own PIN."""
    me = (await api.get("/auth/me")).json()["user"]["id"]
    assert (await api.put(f"/users/{me}/pin", json={"pin": "1357"})).status_code == 204
    assert (await api.post("/auth/adult-unlock", json={"pin": "1357"})).status_code == 200


async def test_admin_can_include_adult_rows(seeded: Api) -> None:
    api = seeded
    flag = {"include_adult": "true", "language": "all"}
    # Asking is not enough until this session is unlocked.
    assert_clean((await api.get("/browse/movies", params=flag)).json())
    await unlock_adult(api)
    movies = (await api.get("/browse/movies", params=flag)).json()
    assert movies["total"] == 4 and {"lib:catalog:night", "tmdb:movie:900"} <= shown(movies)[0]
    assert shown((await api.get("/browse/shows", params=flag)).json())[0] == {"lib:catalog:hours"}
    categories = (await api.get("/browse/movies/categories", params=flag)).json()
    assert categories == [{"name": "Late Night", "count": 2}, {"name": "New Releases", "count": 2}]
    assert (await api.get("/browse/movies/genres", params=flag)).json() == [{"name": "drama", "count": 4}]
    assert (await api.get("/browse/movies/languages", params={"include_adult": "true"})).json()[0]["count"] == 4
    found = (await api.get("/search", params={"q": "Night Ferry", **flag})).json()
    assert [t["id"] for t in found["results"]] == ["lib:catalog:night"]

    home = (await api.get("/home", params={"include_adult": "true"})).json()
    rows = {row["id"]: [t["id"] for t in row["tiles"]] for row in home["rows"]}
    assert rows["recently_added"][:3] == ["lib:catalog:night", "tmdb:movie:900", "lib:catalog:hours"]
    assert rows["favorites"] == ["lib:catalog:night"] and rows["watchlist"] == ["tmdb:movie:900"]
    assert "shelf:movies:Late Night" in rows
    # The opt-in lists adult rows; the billboard still never features one.
    assert [t["id"] for t in home["hero"]] == ["lib:catalog:watched", "lib:catalog:harbour"]
    assert shown((await api.get("/favorites", params={"include_adult": "true"})).json())[0] == {"lib:catalog:night"}

    # The flag is per request and never leaks into a cached general listing.
    assert_clean((await api.get("/browse/movies", params={"language": "all"})).json())
    assert_clean((await api.get("/search", params={"q": "Night Ferry", "language": "all"})).json())


async def test_include_adult_is_ignored_for_other_profiles(seeded: Api) -> None:
    api = seeded
    headers = await guest_headers(api)
    flag = {"include_adult": "true", "language": "all"}
    for path, params in [
        ("/browse/movies", flag),
        ("/browse/shows", flag),
        ("/browse/movies/categories", flag),
        ("/search", {"q": "Night Ferry", **flag}),
        ("/search", {"q": "Closing Time", **flag}),
        ("/home", {"include_adult": "true"}),
    ]:
        response = await api.client.get("/api/v1" + path, params=params, headers=headers)
        assert response.status_code == 200, response.text
        assert_clean(response.json())
        assert "Late Night" not in response.text


def refused(response: Any, code: str) -> bool:
    return bool(response.status_code == 403 and response.json()["code"] == code)


async def test_an_adult_title_is_not_opened_by_its_id_without_the_permission_and_the_unlock(seeded: Api) -> None:
    """Hiding a row is not what keeps it shut: knowing (or guessing) its id opens nothing."""
    api = seeded
    guest = await guest_headers(api)
    for ref in ("lib:catalog:night", "tmdb:movie:900", "lib:catalog:hours", "lib:catalog:hours-e1"):
        theirs = await api.client.get(f"/api/v1/titles/{ref}", headers=guest)
        assert theirs.status_code == 403 and theirs.json()["missing"] == ["watch.adult"], (ref, theirs.text)
        assert "Night Ferry" not in theirs.text and "Late Tide" not in theirs.text
        # Holding the permission is half of it: this session has not been unlocked.
        assert refused(await api.get(f"/titles/{ref}"), "adult_locked"), ref
    # Everything else opens as it always did.
    assert (await api.client.get("/api/v1/titles/lib:catalog:harbour", headers=guest)).status_code == 200
    await unlock_adult(api)
    detail = await api.get("/titles/lib:catalog:night")
    assert detail.status_code == 200 and detail.json()["title"] == "Night Ferry"
    assert [b["source_id"] for b in detail.json()["sources"]] == ["catalog"]
    assert (await api.get("/titles/tmdb:movie:900")).status_code == 200
    # Locked again, shut again; and the unlock was this session's, not the permission's.
    assert (await api.post("/auth/adult-lock")).status_code == 204
    assert refused(await api.get("/titles/lib:catalog:night"), "adult_locked")


async def test_an_adult_title_is_not_played_by_its_id_either(seeded: Api) -> None:
    api = seeded
    guest = await guest_headers(api)
    epoch = api.c.proxy.recovery_epoch

    async def play(headers: dict[str, str], ref: str, **extra: Any) -> Any:
        body = {"id": ref, "client": "android_tv", "recovery_generation": epoch, **extra}
        return await api.client.post("/api/v1/playback/resolve", json=body, headers=headers)

    for ref in ("lib:catalog:night", "tmdb:movie:900", "trailer:lib:catalog:night", "lib:catalog:hours-e1"):
        assert (await play(guest, ref)).json().get("missing") == ["watch.adult"], ref
        assert refused(await play(api.headers, ref), "adult_locked"), ref
    for headers in (guest, api.headers):
        options = await api.client.get("/api/v1/playback/options", params={"id": "lib:catalog:night"}, headers=headers)
        assert options.status_code == 403
        panes = {"panes": [{"id": "lib:catalog:harbour"}, {"id": "lib:catalog:night"}]}
        assert (await api.client.post("/api/v1/playback/multiview", json=panes, headers=headers)).status_code == 403
    await unlock_adult(api)
    assert not refused(await play(api.headers, "lib:catalog:night"), "adult_locked")
    assert (await play(guest, "lib:catalog:night")).status_code == 403


async def test_a_room_is_no_way_around_the_adult_lock(seeded: Api) -> None:
    api = seeded
    guest = await guest_headers(api)
    assert refused(await api.post("/rooms", json={"id": "lib:catalog:night"}), "adult_locked")
    assert (await api.client.post("/api/v1/rooms", json={"id": "lib:catalog:night"}, headers=guest)).status_code == 403
    await unlock_adult(api)
    made = await api.post("/rooms", json={"id": "lib:catalog:night"})
    assert made.status_code == 201, made.text
    code = made.json()["code"]
    # The code is an invitation to watch together, not a way in for someone the title is shut to.
    for method, path, body in (
        ("GET", f"/rooms/{code}", None),
        ("POST", f"/rooms/{code}/join", {"client": "android_tv"}),
    ):
        r = await api.client.request(method, "/api/v1" + path, json=body, headers=guest)
        assert r.status_code == 403 and r.json().get("missing") == ["watch.adult"], (path, r.text)
        assert "Night Ferry" not in r.text


async def test_predicate_leaves_the_selective_index_in_charge(seeded: Api) -> None:
    from sqlalchemy import select, text
    from sqlalchemy.dialects import sqlite

    from app.services.adult import general_audience

    # Nearly every row is not adult; a plan driven by the adult index reads the whole table.
    query = select(Title.id).where(Title.tmdb_id == 900, general_audience())
    sql = str(query.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    async with seeded.c.db.session() as s:
        plan = " ".join(str(row[3]) for row in await s.execute(text("EXPLAIN QUERY PLAN " + sql)))
    assert "ix_title_tmdb" in plan and "ix_title_adult" not in plan
