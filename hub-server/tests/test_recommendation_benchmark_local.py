"""Opt-in local catalog-size benchmark; no network or provider credentials."""

import os
from time import perf_counter

import pytest
from sqlalchemy import event, insert

from app.infra.models import Progress, Title
from tests.test_recommendations import get, seed


@pytest.mark.skipif(
    os.environ.get("STREAMHUB_RECOMMENDATION_BENCHMARK") != "1", reason="opt-in large catalog benchmark"
)
async def test_large_local_recommendation_benchmark(api):
    await seed(api)
    async with api.c.db.session() as s:
        for start in range(0, 212000, 2000):
            await s.execute(
                insert(Title),
                [
                    dict(
                        source_id="s",
                        ext_id=f"bench{i}",
                        kind="vod",
                        title=f"Fixture {i}",
                        tmdb_id=i + 10000,
                        genres=["Action"],
                        group_name="English",
                    )
                    for i in range(start, start + 2000)
                ],
            )
        s.add_all(Progress(profile_id=api.profile_id, ref_id=f"lib:s:bench{i}", completed=True) for i in range(50))
        await s.commit()
    print("Synthetic seed committed", flush=True)

    @event.listens_for(api.c.db.engine.sync_engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, executemany):
        context.benchmark_start = perf_counter()

    @event.listens_for(api.c.db.engine.sync_engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, executemany):
        print({"query_seconds": round(perf_counter() - context.benchmark_start, 3)}, flush=True)

    timings = []
    for _ in range(2):
        start = perf_counter()
        tiles, label = await get(api)
        timings.append(round(perf_counter() - start, 3))
        print({"lookup_seconds": timings[-1]}, flush=True)
        assert len(tiles) == 20 and label == "Because you watched Action"
    print({"catalog_titles": 212006, "history_titles": 50, "returned_tiles": 20, "seconds": timings})
