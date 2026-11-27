from unittest.mock import AsyncMock, patch

from app.adapters.tmdb.client import TmdbClient
from tests.test_canonical_discovery import setup


async def test_cast_roles_known_titles_and_missing_year_come_from_the_matched_person():
    client = object.__new__(TmdbClient)
    movie = {
        "id": 801,
        "media_type": "movie",
        "title": "Fixture movie",
        "character": "Mara",
        "release_date": "2014-03-10",
    }
    tv = {"id": 802, "media_type": "tv", "name": "Fixture series", "character": ""}
    person = {
        "name": "Alex Fixture",
        "combined_credits": {
            "cast": [movie, {**movie, "character": "Narrator"}, tv, {**movie, "id": 805, "adult": True}]
        },
    }
    search = {
        "results": [
            {"id": 999, "name": person["name"], "known_for": [tv]},
            {"id": 42, "name": person["name"], "known_for": [movie]},
        ]
    }
    with patch.object(client, "_get", AsyncMock(side_effect=[person, search])):
        data = await client.actor_filmography(42)
    assert data
    assert [row["id"] for row in data["known_for"]] == ["tmdb:movie:801"]
    assert [(row["year"], row["roles"]) for row in data["credits"]] == [(2014, ["Mara", "Narrator"]), (None, [])]


async def test_http_filmography_filters_known_for_and_filmography_identically(api):
    meta = await setup(api)
    movie = {"id": "tmdb:movie:11", "kind": "movie", "title": "Visible fixture", "roles": ["Ada"], "year": 2020}
    series = {"id": "tmdb:tv:22", "kind": "tv", "title": "Hidden fixture", "roles": ["Sam"], "year": None}
    meta.actor_filmography.return_value = {
        "name": "Fixture Person",
        "photo": None,
        "known_for": [series, movie],
        "credits": [series, movie],
    }
    with patch.object(api.c.content_policy, "allowed_refs", AsyncMock(return_value={movie["id"]})):
        response = await api.get("/people/42/credits")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert [r["id"] for r in response.json()["known_for"]] == [movie["id"]]
    assert [r["id"] for r in response.json()["credits"]] == [movie["id"]]
