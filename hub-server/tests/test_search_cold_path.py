"""A cold search answers from the hub at once: the metadata provider is optional, budgeted and remembered."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import event

from app.domain.entities import MetaTitle
from app.services import catalog as catalog_module
from tests.conftest import Api
from tests.search_catalog import SlowMetadata, seed_search_catalog
from tests.test_unified_search import channel, programme, seed, title

ALL = {"language": "all"}
# Generous: the local half takes tens of milliseconds here. Seconds would mean a remote call or a scan came back.
LOCAL_BOUND = 1.0


def provider(api: Api, **kw: Any) -> SlowMetadata:
    meta = SlowMetadata(**kw)
    api.c.catalog.meta = meta  # type: ignore[assignment]
    api.c.availability._meta = meta  # type: ignore[assignment]
    return meta


async def timed(api: Api, q: str, **params: Any) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    response = await api.get("/search", params={"q": q, **ALL, **params})
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return elapsed, found


async def test_the_local_half_of_a_cold_query_is_fast_on_a_real_sized_catalog_and_asks_no_provider(api: Api) -> None:
    await seed_search_catalog(api)
    meta = provider(api, delay=0.8)
    statements: list[str] = []

    def before(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(api.c.db.engine.sync_engine, "before_cursor_execute", before)
    try:
        counts, took = [], {}
        # A common prefix, rarer ones, two letters, two words, a typo and a query nothing matches.
        for q in ("hall", "disc", "bres", "es", "grin", "the mill", "hallen meaow", "halen dixum", "zzqq"):
            statements.clear()
            elapsed, found = await timed(api, q, discover="false")
            counts.append(len(statements))
            took[q] = round(elapsed * 1000)
            assert elapsed < LOCAL_BOUND, (q, elapsed)
            assert found["metadata_available"] is True and found["discovered"] == []
            assert all(tile["sources"] for tile in found["results"]), q
        assert meta.calls == []
        # A handful of reads whatever the query finds, session and policy lookups included.
        assert max(counts) <= 30, counts
        print(f"cold local search, 35,200 titles, ms: {took}")
    finally:
        event.remove(api.c.db.engine.sync_engine, "before_cursor_execute", before)
    common = (await timed(api, "ha", discover="false"))[1]
    assert len(common["results"]) == 40 and common["sections"]["titles"]["more"] is True

    # The same queries with discovery wait for the provider, and bring what only it knows.
    elapsed, full = await timed(api, "grin")
    assert elapsed >= 0.8 and ("search", "grin") in meta.calls
    assert full["discovered"] and set(full["discovered"]) <= {tile["id"] for tile in full["results"]}
    local = (await timed(api, "grin", discover="false"))[1]
    assert not set(full["discovered"]) & {tile["id"] for tile in local["results"]}


async def test_without_discovery_the_answer_is_everything_the_hub_holds_and_nothing_else(api: Api) -> None:
    await seed(
        api,
        title("owned", "Lantern Song", year=2020),
        title("matched", "Lantern Road", year=2019, tmdb_id=77),
        channel("one", "Lantern One", 7),
        programme("one", "Lantern Tales", timedelta(minutes=-5)),
    )
    meta = provider(api, delay=0.0, results=3)
    local = (await timed(api, "lantern", discover="false"))[1]
    assert sorted(tile["title"] for tile in local["results"]) == ["Lantern Road", "Lantern Song"]
    assert [row["name"] for row in local["channels"]] == ["Lantern One"]
    assert [row["title"] for row in local["programmes"]] == ["Lantern Tales"]
    assert local["sections"]["titles"] == {"count": 2, "more": False}
    assert local["metadata_available"] is True and local["discovered"] == [] and meta.calls == []

    full = (await timed(api, "lantern"))[1]
    ids = [tile["id"] for tile in full["results"]]
    # The library's titles are all still there under the ids the local answer gave them...
    assert {"lib:catalog:owned", "tmdb:movie:77"} <= set(ids)
    assert {tile["id"] for tile in local["results"]} <= set(ids)
    # ...and what the provider added is named, so a client can append exactly that.
    assert full["discovered"] == [i for i in ids if i.startswith("tmdb:") and i != "tmdb:movie:77"]
    assert len(full["discovered"]) == 3
    assert all(not tile["sources"] for tile in full["results"] if tile["id"] in full["discovered"])
    assert full["channels"] == local["channels"] and full["programmes"] == local["programmes"]

    # No provider configured: both answers are the same, and say so.
    api.c.catalog.meta = type("Off", (), {"enabled": False})()  # type: ignore[assignment]
    off = (await timed(api, "lantern song", discover="false"))[1]
    assert off["metadata_available"] is False and (await timed(api, "lantern song"))[1]["results"] == off["results"]


async def test_discovery_has_a_budget_and_an_answer_that_comes_late_is_there_next_time(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(api, title("owned", "Lantern Song", year=2020))
    meta = provider(api, delay=0.6, results=2)
    monkeypatch.setattr(catalog_module, "DISCOVERY_BUDGET", 0.15)
    elapsed, found = await timed(api, "lantern")
    assert elapsed < 0.5, elapsed
    assert [tile["title"] for tile in found["results"]] == ["Lantern Song"] and found["discovered"] == []
    # Asked again while the provider is still answering: the same search is waited on, not repeated.
    again = (await timed(api, "lantern"))[1]
    assert again["discovered"] == [] and [name for name, _ in meta.calls] == ["search"]
    await asyncio.sleep(0.7)
    elapsed, found = await timed(api, "lantern")
    assert elapsed < 0.15 and len(found["discovered"]) == 2
    assert [name for name, _ in meta.calls] == ["search"]


async def test_lookups_per_title_share_the_budget_and_what_is_late_keeps_the_library_tile(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Library titles the provider's search does not list are looked up one by one for their proper name.
    await seed(api, *(title(f"t{n}", f"Lantern Road {n}", year=2000 + n, tmdb_id=500 + n) for n in range(12)))
    meta = provider(api, delay=0.25, results=2)
    monkeypatch.setattr(catalog_module, "DISCOVERY_BUDGET", 0.4)
    elapsed, found = await timed(api, "lantern road")
    # One search, then the lookups side by side until the budget is spent: not 12 x 0.25 s.
    assert elapsed < 0.9, elapsed
    assert len([call for call in meta.calls if call[0] == "details"]) == 12
    assert {f"tmdb:movie:{500 + n}" for n in range(12)} <= {tile["id"] for tile in found["results"]}
    assert all(tile["sources"] for tile in found["results"] if tile["id"] not in found["discovered"])


async def test_the_provider_is_asked_once_per_normalised_query_for_a_few_minutes(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(api, title("owned", "Lantern Song", year=2020))
    meta = provider(api, delay=0.0, results=2)
    first = (await timed(api, "Lantern  SONG"))[1]
    # Another spelling, another page size, another profile's languages: the same question to the provider.
    for params in ({"q": " lantern song "}, {"q": "lantern song", "limit": 5}, {"q": "LANTERN SONG", "kinds": "movie"}):
        response = await api.get("/search", params={**ALL, **params})
        assert response.status_code == 200, response.text
    assert [call for call in meta.calls if call[0] == "search"] == [("search", "lantern song")]
    assert len(first["discovered"]) == 2
    # A failure is not remembered: the next request asks again.
    meta.calls.clear()

    async def down(query: str, limit: int = 20) -> list[MetaTitle]:
        meta.calls.append(("search", query))
        raise RuntimeError("fixture provider down")

    working, meta.search = meta.search, down  # type: ignore[method-assign]
    broken = (await timed(api, "lantern tales"))[1]
    assert broken["metadata_available"] is False and broken["discovered"] == []
    meta.search = working  # type: ignore[method-assign]
    assert len((await timed(api, "lantern tales "))[1]["discovered"]) == 2
    assert [call[1] for call in meta.calls] == ["lantern tales", "lantern tales"]
    # Kept for DISCOVERY_TTL, then asked again.
    monkeypatch.setattr(catalog_module, "DISCOVERY_TTL", 0.0)
    await timed(api, "lantern dawn")
    await timed(api, "lantern dawn", limit=7)
    assert [call[1] for call in meta.calls].count("lantern dawn") == 2


async def test_a_typo_is_guessed_only_when_little_else_was_found(api: Api) -> None:
    await seed(
        api,
        title("typo", "Stuart Saves the Harbour"),
        *(title(f"s{n}", f"Stewart Saves the Harbour {n}") for n in range(6)),
        title("lone", "Meadow Lantern Tales"),
    )
    api.c.catalog.meta = type("Off", (), {"enabled": False})()  # type: ignore[assignment]
    many = (await timed(api, "stewart saves harbour"))[1]
    assert len(many["results"]) == 6 and "Stuart Saves the Harbour" not in {t["title"] for t in many["results"]}
    few = (await timed(api, "meadow lanturn tales"))[1]
    assert [t["title"] for t in few["results"]] == ["Meadow Lantern Tales"]
