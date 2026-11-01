from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import respx

from app.infra.models import AgeClassification, Profile
from tests.test_canonical_discovery import setup
from tests.test_content_policy import evidence
from tests.test_owner_pin_api import pending
from tests.test_owner_pin_api import setup as enroll_owner


async def test_discovery_is_independent_of_source_and_language_with_bound_pagination(api):
    fake = await setup(api)
    fake.discover.return_value = (fake.search.return_value, 12000)
    scoped = await api.get("/discover/movies", params={"source_id": "missing", "language": "eng"})
    assert scoped.status_code == 200, scoped.text
    body = scoped.json()
    assert len(body["tiles"]) == 5 and all(not row["sources"] for row in body["tiles"])
    assert body["total"] == 12000 and body["page_size"] == 20 and body["has_more"]
    available = await api.get("/discover/movies", params={"source_id": "catalog", "language": "eng"})
    assert next(row for row in available.json()["tiles"] if row["id"] == "tmdb:movie:11")["sources"]
    assert not next(row for row in available.json()["tiles"] if row["id"] == "tmdb:movie:140607")["sources"]
    assert (await api.get("/discover/movies", params={"page": 500})).json()["has_more"] is False
    for params in ({"page": 501}, {"page": 0}, {"year": 1800}, {"sort": "untrusted"}):
        assert (await api.get("/discover/movies", params=params)).status_code == 422
    assert (await api.get("/discover/channels")).status_code == 404


async def test_discovery_failure_recovers_and_favorites_invalidate_cached_overlay(api):
    fake = await setup(api)
    fake.discover.side_effect = [RuntimeError("fixture failure"), (fake.search.return_value, 5)]
    failed = await api.get("/discover/movies")
    assert failed.status_code == 502
    recovered = await api.get("/discover/movies")
    assert recovered.status_code == 200
    await api.get("/discover/movies")
    assert fake.discover.await_count == 2
    fake.discover.side_effect = None
    fake.discover.return_value = (fake.search.return_value, 5)
    assert (await api.put("/favorites", json={"id": "tmdb:movie:11", "kind": "movie"})).status_code == 204
    changed = (await api.get("/discover/movies")).json()
    assert next(row for row in changed["tiles"] if row["id"] == "tmdb:movie:11")["is_favorite"]
    assert fake.discover.await_count == 3
    fake.enabled = False
    # A fresh parameter key ensures the metadata-disabled path is exercised.
    disabled = await api.get("/discover/movies", params={"year": 2001})
    assert disabled.status_code == 502 and "Settings" in disabled.json()["message"]


@respx.mock
async def test_restricted_discovery_uses_real_pin_session_and_never_counts_hidden_titles(api):
    owner = await enroll_owner(api, await pending(api))
    fake = await setup(api)
    fake.discover.return_value = (fake.search.return_value, 100)
    async with api.c.db.session() as session:
        session.add(
            Profile(
                id="discover-child",
                name="Child",
                content_policy={"restricted": True, "allowed_movie_codes": ["PG"], "allowed_tv_codes": []},
            )
        )
        session.add(AgeClassification(subject_key="tmdb:movie:11", provider="tmdb", evidence=evidence("tmdb:movie:11")))
        await session.commit()
    switched = await api.post("/auth/profile-switch", headers=owner, json={"profile_id": "discover-child"})
    assert switched.status_code == 200, switched.text
    child = {"Authorization": "Bearer " + switched.json()["access_token"]}
    response = await api.get("/discover/movies", headers=child, params={"language": "all"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body["tiles"]] == ["tmdb:movie:11"]
    assert body["total"] is None and "permitted total is unavailable" in body["coverage_note"]
    async with api.c.db.session() as session:
        row = await session.get(AgeClassification, ("tmdb:movie:11", "tmdb"))
        row.evidence = evidence("tmdb:movie:11", "R")
        await session.commit()
    changed = await api.get("/discover/movies", headers=child, params={"language": "all"})
    assert changed.status_code == 200 and changed.json()["tiles"] == [] and changed.json()["total"] is None


@pytest.mark.parametrize("kind", ["movie", "tv"])
@pytest.mark.parametrize("sort", ["popular", "rating", "newest"])
async def test_adapter_discovery_uses_media_specific_dates_and_no_actual_network(api, monkeypatch, kind, sort):
    adapter = api.c.catalog.meta
    get = AsyncMock(return_value={"results": [], "total_results": 0})
    monkeypatch.setattr(adapter, "_get", get)
    assert await adapter.discover(kind, page=3, sort=sort, year=2020) == ([], 0)
    args = get.call_args
    assert args.args[0] == f"/discover/{kind}"
    assert args.kwargs["page"] == 3 and args.kwargs["include_adult"] == "false"
    assert args.kwargs["primary_release_year" if kind == "movie" else "first_air_date_year"] == 2020
    if sort == "rating":
        assert args.kwargs["vote_count.gte"] == 100
    if sort == "newest":
        field = "primary_release_date" if kind == "movie" else "first_air_date"
        assert args.kwargs[f"{field}.lte"] == datetime.now(UTC).date().isoformat()
    get.return_value = None
    with pytest.raises(RuntimeError):
        await adapter.discover(kind)
