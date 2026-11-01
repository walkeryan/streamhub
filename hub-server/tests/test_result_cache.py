from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert

from app.infra.models import Favorite, Profile, Progress, Source
from app.infra.result_cache import ResultCache
from app.services.listing_cache import listing_cached, normalized_query


async def test_cache_bound_copy_expiry_and_error():
    cache = ResultCache(capacity=2, max_bytes=100)
    load = AsyncMock(return_value={"value": [1]})
    first = await cache.get("a", load, 60)
    first["value"].append(2)
    assert await cache.get("a", load, 60) == {"value": [1]}
    assert load.await_count == 1
    await cache.get("b", load, 60)
    await cache.get("c", load, 60)
    assert list(cache.entries) == ["b", "c"]
    await cache.get("expired", load, -1)
    await cache.get("expired", load, 10)
    assert load.await_count == 5
    huge = AsyncMock(return_value="x" * 200)
    await cache.get("huge", huge, 60)
    assert "huge" not in cache.entries
    bad = AsyncMock(side_effect=ValueError("test"))
    with pytest.raises(ValueError):
        await cache.get("bad", bad, 60)
    assert not cache.pending and "bad" not in cache.entries
    assert cache.bytes <= 100


async def test_coalesced_cancellation_and_orphan_cleanup():
    cache = ResultCache()
    entered, finish, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        entered.set()
        try:
            await finish.wait()
            return [1]
        finally:
            cancelled.set()

    a = asyncio.create_task(cache.get("same", load, 60))
    await entered.wait()
    b = asyncio.create_task(cache.get("same", load, 60))
    await asyncio.sleep(0)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    finish.set()
    assert await b == [1]
    assert calls == 1 and not cache.pending
    finish.clear()
    cancelled.clear()
    task = asyncio.create_task(cache.get("orphan", load, 60))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await cancelled.wait()
    assert not cache.pending and "orphan" not in cache.entries


async def test_commit_revisions_scope_bulk_upsert_and_rollback(api):
    db = api.c.db
    own = api.profile_id
    before = db.catalog_revisions.version(own)
    other_before = db.catalog_revisions.version("other")
    async with db.session() as s:
        s.add(Favorite(profile_id=own, kind="title", ref_id="example"))
        await s.flush()
        assert db.catalog_revisions.version(own) == before
        await s.commit()
    assert db.catalog_revisions.version(own) != before
    assert db.catalog_revisions.version("other") == other_before
    before = db.catalog_revisions.version(own)
    async with db.session() as s:
        await s.execute(delete(Favorite).where(Favorite.profile_id == own))
        await s.rollback()
    assert db.catalog_revisions.version(own) == before
    async with db.session() as s:
        await s.execute(delete(Favorite).where(Favorite.profile_id == own))
        await s.commit()
    assert db.catalog_revisions.version(own) != before
    before = db.catalog_revisions.version(own)
    async with db.session() as s:
        await s.execute(
            insert(Progress).values(profile_id=own, ref_id="a", kind="movie", position_ms=10).on_conflict_do_nothing()
        )
        await s.commit()
    assert db.catalog_revisions.version(own) != before
    assert db.catalog_revisions.version("other") == other_before
    async with db.session() as s:
        profile = await s.get(Profile, own)
        profile.prefs = {"test": "new"}
        await s.commit()
    assert db.catalog_revisions.version("other") == other_before
    async with db.session() as s:
        await s.execute(delete(Source).where(Source.id == "nonexistent"))
        await s.commit()
    assert db.catalog_revisions.version("other") != other_before


async def test_inflight_revision_change_reloads_and_profile_isolation(api):
    db = api.c.db
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        if calls == 1:
            db.catalog_revisions.catalog += 1
            return {"stale": True}
        return {"stale": False}

    assert await listing_cached(db, "a", "search", ("test",), load) == {"stale": False}
    assert calls == 2
    await listing_cached(db, "a", "search", ("test",), load)
    assert calls == 2
    await listing_cached(db, "b", "search", ("test",), load)
    assert calls == 3
    db.catalog_revisions.profiles["b"] = 1
    await listing_cached(db, "a", "search", ("test",), load)
    assert calls == 3
    await listing_cached(db, "b", "search", ("test",), load)
    assert calls == 4
    assert normalized_query("  STAR   Wars ") == "star wars"


async def test_search_api_normalized_hit_and_source_invalidation(api, monkeypatch):
    load = AsyncMock(return_value={"play_here": [], "open_in_app": [], "channels": [], "attribution": ""})
    monkeypatch.setattr(api.c.catalog, "search", load)
    first = await api.client.get("/api/v1/search", params={"q": " STAR   Wars "}, headers=api.headers)
    second = await api.client.get("/api/v1/search", params={"q": "star wars"}, headers=api.headers)
    assert first.status_code == second.status_code == 200
    assert second.headers["cache-control"] == "private, no-store"
    assert load.await_count == 1
    assert load.call_args.args[0] == "star wars"
    async with api.c.db.session() as s:
        await s.execute(delete(Source).where(Source.id == "none"))
        await s.commit()
    await api.client.get("/api/v1/search", params={"q": "star wars"}, headers=api.headers)
    assert load.await_count == 2


async def test_catalog_scale_cold_warm(api):
    """Actual API/service path, synthetic full-schema catalog, no provider access."""
    import time

    from app.infra.models import Title

    async with api.c.db.session() as session:
        session.add(Source(id="synthetic", type="xtream", name="Synthetic", config_enc="unused"))
        await session.commit()
        for start in range(0, 212021, 2000):
            rows = []
            for i in range(start, min(start + 2000, 212021)):
                title = f"Star Wars Synthetic {i}" if i >= 211981 else f"Synthetic catalog movie {i}"
                rows.append(
                    dict(
                        source_id="synthetic",
                        ext_id=str(i),
                        kind="movie",
                        title=title,
                        norm_title=title.lower(),
                        year=2000 + i % 25,
                        genres=["Adventure"],
                        overview="Synthetic fixture description " * 5,
                        extra={"language": "en"},
                    )
                )
            await session.execute(insert(Title), rows)
        await session.commit()
    params = {"q": "star wars", "kinds": "movie", "language": "all"}
    started = time.perf_counter()
    cold = await api.get("/search", params=params)
    cold_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    warm = await api.get("/search", params=params)
    warm_ms = (time.perf_counter() - started) * 1000
    print(f"synthetic212021 API cold={cold_ms:.2f}ms warm={warm_ms:.2f}ms")
    assert cold.status_code == warm.status_code == 200
    assert cold.json() == warm.json()
    assert len(warm.json()["play_here"]) == 40
    assert len(api.c.db.result_cache.entries) == 1


async def test_bulk_sync_and_enabled_source_invalidate(api):
    from sqlalchemy import update

    from app.infra.models import Title

    db = api.c.db
    async with db.session() as session:
        session.add(Source(id="sync", type="xtream", name="Synthetic", config_enc="unused"))
        await session.commit()
    previous = db.catalog_revisions.version(api.profile_id)
    async with db.session() as session:
        await session.execute(insert(Title), [dict(source_id="sync", ext_id="a", kind="movie", title="A")])
        await session.commit()
    assert db.catalog_revisions.version(api.profile_id) != previous
    previous = db.catalog_revisions.version(api.profile_id)
    async with db.session() as session:
        await session.execute(update(Source).where(Source.id == "sync").values(enabled=False))
        await session.commit()
    assert db.catalog_revisions.version(api.profile_id) != previous
