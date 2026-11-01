"""Opt-in synthetic 212k search/API benchmark; never contacts providers."""

import os
from collections import Counter
from time import perf_counter
from unittest.mock import AsyncMock

import pytest
import respx
from sqlalchemy import event, insert, text
from sqlalchemy.orm import Session

from app.domain.entities import MetaTitle
from app.infra.models import AgeClassification, Profile, Source, Title
from tests.test_discover_page_ports import evidence
from tests.test_owner_pin_api import pending, setup

pytestmark = pytest.mark.skipif(os.getenv("STREAMHUB_FTS_BENCHMARK") != "1", reason="opt-in 212k synthetic fixture")


@pytest.mark.parametrize("shared_anchor", [False, True], ids=["broader-noise", "shared-star-anchor"])
@respx.mock
async def test_search_212k_cold_warm_and_scope(api, shared_anchor):
    seed_start = perf_counter()
    owner = await setup(api, await pending(api))
    fake = AsyncMock()
    fake.enabled = True
    movies = [MetaTitle(11, "movie", "Star Wars", 1977), MetaTitle(12, "movie", "Star Wars Parody", 2000)]
    shows = [MetaTitle(13, "tv", "Stuart Fails to Save the Universe", 2026)]
    fake.search.side_effect = lambda query, **kwargs: shows if "universe" in query.lower() else movies
    fake.details.return_value = None
    api.c.catalog.meta = fake
    async with api.c.db.session() as session:
        session.add_all(
            [
                Source(id="scale-a", type="plex", name="Synthetic A", config_enc="unused"),
                Source(id="scale-b", type="plex", name="Synthetic B", config_enc="unused"),
                Profile(
                    id="scale-child",
                    name="Child",
                    content_policy={"restricted": True, "allowed_movie_codes": ["PG"], "allowed_tv_codes": []},
                ),
                AgeClassification(subject_key="tmdb:movie:11", provider="tmdb", evidence=evidence("tmdb:movie:11")),
            ]
        )
        await session.flush()
        # Frequent anchor noise and unique works; useful matches arrive last.
        for start in range(0, 212000, 2000):
            await session.execute(
                insert(Title),
                [
                    dict(
                        source_id="scale-a" if i % 2 else "scale-b",
                        ext_id=str(i),
                        kind="vod",
                        title=f"{'Star' if shared_anchor or i % 100 == 0 else 'Synthetic'} Documentary {i:06}",
                        norm_title=f"{'star' if shared_anchor or i % 100 == 0 else 'synthetic'} documentary {i:06}",
                        year=2000 + i % 25,
                    )
                    for i in range(start, start + 2000)
                ],
            )
        for source in ("scale-a", "scale-b"):
            for meta in movies + shows:
                session.add(
                    Title(
                        source_id=source,
                        ext_id=f"match-{meta.tmdb_id}",
                        kind="vod" if meta.media_type == "movie" else "series",
                        title=meta.title,
                        norm_title=meta.title.lower(),
                        year=meta.year,
                        tmdb_id=meta.tmdb_id,
                    )
                )
        await session.commit()
    print(
        {
            "seed_and_pin_seconds": round(perf_counter() - seed_start, 3),
            "titles": 212006,
            "shared_anchor_fraction": 1.0 if shared_anchor else 0.01,
            "metadata": "stub; no network latency",
        },
        flush=True,
    )
    counts = Counter()
    fts_statement = []

    def before(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            counts["selects"] += 1
            if "title_search" in statement:
                counts["fts_selects"] += 1
                if not fts_statement:
                    fts_statement.append((statement, _params))

    def loaded(_session, instance):
        counts[type(instance).__name__] += 1

    async def request(label, auth, query, **params):
        counts.clear()
        start = perf_counter()
        result = await api.get("/search", headers=auth, params={"q": query, "language": "all", **params})
        elapsed = perf_counter() - start
        assert result.status_code == 200, result.text
        print(
            {
                "stage": label,
                "ms": round(elapsed * 1000, 2),
                "sql_and_orm_rows": dict(counts),
                "results": len(result.json()["results"]),
            },
            flush=True,
        )
        assert counts.get("Title", 0) <= 550
        return result.json(), dict(counts)

    event.listen(api.c.db.engine.sync_engine, "before_cursor_execute", before)
    event.listen(Session, "loaded_as_persistent", loaded)
    try:
        for query, expected in [
            ("Star Wars", "tmdb:movie:11"),
            ("Stewart Fails to Save the Universe", "tmdb:tv:13"),
            ("Star War", "tmdb:movie:11"),
        ]:
            cold, cold_counts = await request(query + " cold", owner, query)
            assert cold["results"][0]["id"] == expected
            warm, warm_counts = await request(query + " warm", owner, "  " + query.upper() + "  ")
            assert warm == cold and warm_counts.get("Title", 0) == 0
            assert cold_counts.get("fts_selects", 0) > 0
        scoped, _ = await request("source A", owner, "Star Wars", source_id="scale-a")
        assert scoped["results"][0]["sources"]
        absent, _ = await request("absent source retains metadata", owner, "Star Wars", source_id="absent")
        assert absent["results"] and all(not row["sources"] for row in absent["results"])
        switched = await api.post("/auth/profile-switch", headers=owner, json={"profile_id": "scale-child"})
        assert switched.status_code == 200, switched.text
        child = {"Authorization": "Bearer " + switched.json()["access_token"]}
        permitted, _ = await request("restricted profile", child, "Star Wars")
        assert [row["id"] for row in permitted["results"]] == ["tmdb:movie:11"]
        async with api.c.db.session() as session:
            classification = await session.get(AgeClassification, ("tmdb:movie:11", "tmdb"))
            classification.evidence = evidence("tmdb:movie:11", "R")
            await session.commit()
        revoked, _ = await request("classification invalidation", child, "Star Wars")
        assert not revoked["results"]
        async with api.c.db.session() as session:
            plan = (
                await session.execute(
                    text("EXPLAIN QUERY PLAN SELECT rowid FROM title_search WHERE title_search MATCH 'star AND wars'")
                )
            ).all()
        assert any("VIRTUAL TABLE INDEX" in row[-1] for row in plan)
        print({"fts_query_plan": [row[-1] for row in plan]}, flush=True)
        async with api.c.db.engine.connect() as connection:
            sql, parameters = fts_statement[0]
            actual_plan = (await connection.exec_driver_sql("EXPLAIN QUERY PLAN " + sql, parameters)).all()
        print({"actual_search_query_plan": [row[-1] for row in actual_plan]}, flush=True)
    finally:
        event.remove(api.c.db.engine.sync_engine, "before_cursor_execute", before)
        event.remove(Session, "loaded_as_persistent", loaded)
