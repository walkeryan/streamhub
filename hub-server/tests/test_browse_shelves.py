"""GET /browse/{section}/shelves: every shelf with its first titles in one call."""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, update

from app.infra.models import Favorite, Source, Title
from tests.conftest import Api
from tests.test_adult_surfaces import guest_headers
from tests.test_home_shelves import rows_for, seed


async def shelves(api: Api, section: str = "movies", **params: Any) -> list[dict[str, Any]]:
    response = await api.get(f"/browse/{section}/shelves", params=params)
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    listed: list[dict[str, Any]] = response.json()["shelves"]
    return listed


async def unlock_adult(api: Api) -> None:
    me = (await api.get("/auth/me")).json()["user"]["id"]
    assert (await api.put(f"/users/{me}/pin", json={"pin": "1357"})).status_code == 204
    assert (await api.post("/auth/adult-unlock", json={"pin": "1357"})).status_code == 200


async def test_shelves_are_ranked_and_carry_their_first_titles(api: Api) -> None:
    drama = rows_for("Drama", 5)
    drama[0]["poster"] = None  # the newest Drama title has no artwork
    await seed(
        api,
        rows_for("Action", 3)
        + drama
        + rows_for("New Releases", 2)
        + rows_for("Older Favorites", 8, age_days=90)
        + rows_for("TV Shows", 4, kind="series")
        + [{**rows_for("Loose", 1)[0], "group_name": None}],
    )
    listed = await shelves(api)
    assert [(s["name"], s["count"]) for s in listed] == [
        ("New Releases", 2),
        ("Drama", 5),
        ("Older Favorites", 8),  # larger, but stale shelves count half
        ("Action", 3),
    ]
    assert listed[0]["browse_path"] == "/browse/movies?category=New%20Releases"
    assert [t["id"] for t in listed[0]["items"]] == ["lib:catalog:vod-new-releases-0", "lib:catalog:vod-new-releases-1"]
    # Titles with a poster lead; within that, newest first.
    assert [t["id"] for t in listed[1]["items"]] == [f"lib:catalog:vod-drama-{i}" for i in (1, 2, 3, 4, 0)]
    tile = listed[0]["items"][0]
    assert tile["kind"] == "vod" and tile["sources"][0]["source_id"] == "catalog" and tile["poster"]

    assert [s["name"] for s in await shelves(api, "shows")] == ["TV Shows"]
    limited = await shelves(api, limit_rows=2, limit_items=3)
    assert [(s["name"], s["count"], len(s["items"])) for s in limited] == [("New Releases", 2, 2), ("Drama", 5, 3)]
    assert (await api.get("/browse/nope/shelves")).status_code == 404
    assert (await api.get("/browse/movies/shelves", params={"limit_items": 0})).status_code == 422


async def test_shelves_merge_variants_and_keep_profile_state(api: Api) -> None:
    await seed(api, rows_for("Drama", 2, tmdb_id=None))
    async with api.c.db.session() as s:
        s.add(Source(id="second", name="Second", type="xtream", config_enc="unused"))
        await s.flush()
        copies = [
            {**row, "source_id": source, "tmdb_id": 700}
            for row in rows_for("Netflix", 1)
            for source in ("catalog", "second")
        ]
        copies[1]["ext_id"] = "other-copy"
        await s.execute(insert(Title), copies)
        s.add(Favorite(profile_id=api.profile_id, kind="vod", ref_id="lib:catalog:vod-drama-1"))
        await s.commit()
    listed = {s["name"]: s for s in await shelves(api)}
    netflix = listed["Netflix"]
    # Two copies of one film are one title, playable from both sources.
    assert netflix["count"] == 1 and [t["id"] for t in netflix["items"]] == ["tmdb:movie:700"]
    assert sorted(b["source_id"] for b in netflix["items"][0]["sources"]) == ["catalog", "second"]
    assert [t["is_favorite"] for t in listed["Drama"]["items"]] == [False, True]
    only = await shelves(api, source_id="second")
    assert [(s["name"], s["count"]) for s in only] == [("Netflix", 1)]


async def test_shelves_respect_language_and_adult_scope(api: Api) -> None:
    french = [{**row, "language": "fra"} for row in rows_for("Cinema", 3)]
    late = [{**row, "adult": True} for row in rows_for("Late Night", 4)]
    await seed(api, rows_for("Drama", 2) + french + late)

    assert [s["name"] for s in await shelves(api)] == ["Drama"]
    assert [s["name"] for s in await shelves(api, language="all")] == ["Cinema", "Drama"]
    assert [s["name"] for s in await shelves(api, language="fra")] == ["Cinema"]
    # Adult rows need the permission and a session unlocked with the user's own PIN.
    assert [s["name"] for s in await shelves(api, include_adult="true")] == ["Drama"]
    await unlock_adult(api)
    assert [s["name"] for s in await shelves(api, include_adult="true")] == ["Late Night", "Drama"]
    # The opt-in is per request: the general listing that follows is not served from its cache.
    assert [s["name"] for s in await shelves(api)] == ["Drama"]

    headers = await guest_headers(api)
    guest = await api.client.get(
        "/api/v1/browse/movies/shelves", params={"include_adult": "true", "language": "all"}, headers=headers
    )
    assert guest.status_code == 200 and [s["name"] for s in guest.json()["shelves"]] == ["Cinema", "Drama"]
    assert (await api.client.get("/api/v1/browse/movies/shelves")).status_code == 401


async def test_shelves_follow_the_profile_content_policy(api: Api) -> None:
    from tests.test_content_policy import activate
    from tests.test_content_policy import seed as seed_policy

    policy = await seed_policy(api)
    await activate(api)
    async with api.c.db.session() as s:
        await s.execute(update(Title).where(Title.kind == "movie").values(group_name="Family"))
        await s.commit()
    child = {"Authorization": "Bearer " + (await api.c.auth.issue(await policy.profile("child")))["access_token"]}
    restricted = await api.client.get("/api/v1/browse/movies/shelves", params={"language": "all"}, headers=child)
    assert restricted.status_code == 200, restricted.text
    [family] = restricted.json()["shelves"]
    # Only the title this profile's rating policy allows is counted or shown.
    assert family["count"] == 1 and [t["id"] for t in family["items"]] == ["tmdb:movie:1"]
    [everything] = await shelves(api, language="all")
    assert everything["count"] == 4 and len(everything["items"]) == 4
