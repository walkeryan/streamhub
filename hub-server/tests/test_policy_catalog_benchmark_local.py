"""Opt-in synthetic policy/API benchmark. No providers, production data, or media."""

import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest
from sqlalchemy import event, insert, text
from sqlalchemy.orm import Session

from app.infra.models import AgeClassification, Channel, ChannelGroup, Profile, Programme, Source, Title
from app.services.classification import classification_record, summarize

pytestmark = pytest.mark.skipif(
    os.environ.get("STREAMHUB_POLICY_BENCHMARK") != "1", reason="opt-in synthetic policy scale check"
)


async def test_restricted_catalog_guide_session_cache_scale(api):
    started = perf_counter()
    now = datetime.now(UTC)
    policy = api.c.content_policy
    api.c.settings.content_policy_enabled = True

    def movie_evidence(n):
        key = f"tmdb:movie:{n}"
        code = "G" if n <= 2500 else "PG" if n <= 5000 else "R"
        return {**summarize("tmdb", "movie", [classification_record("US", "US-MPA", code)]), "subject_key": key}

    async with api.c.db.session() as session:
        session.add(
            Profile(
                id="scale-child",
                name="Synthetic child",
                content_policy={"restricted": True, "allowed_movie_codes": ["G", "PG"], "allowed_tv_codes": ["TV-G"]},
            )
        )
        session.add(Source(id="scale", type="xtream", name="Synthetic source", config_enc="unused"))
        await session.commit()
        session.add(ChannelGroup(id="scale-group", source_id="scale", name="Synthetic"))
        await session.commit()
        for start in range(1, 10001, 1000):
            await session.execute(
                insert(Title),
                [
                    dict(
                        source_id="scale",
                        ext_id=str(n),
                        kind="movie",
                        title=f"Fixture {n:05d}",
                        norm_title=f"fixture {n:05d}",
                        tmdb_id=n,
                    )
                    for n in range(start, start + 1000)
                ],
            )
        for start in range(1, 7501, 500):
            await session.execute(
                insert(AgeClassification),
                [
                    dict(subject_key=f"tmdb:movie:{n}", provider="tmdb", evidence=movie_evidence(n), observed_at=now)
                    for n in range(start, start + 500)
                ],
            )
        await session.execute(
            insert(Channel),
            [
                dict(
                    source_id="scale",
                    ext_id=str(n),
                    name=f"Channel {n:03d}",
                    number=n,
                    epg_id=f"epg-{n}",
                    group_name="Synthetic",
                    group_id="scale-group",
                )
                for n in range(200)
            ],
        )
        for channel in range(200):
            evidence = summarize(
                "xmltv", "programme", [classification_record("US", "US-TVPG", "TV-G" if channel < 100 else "TV-MA")]
            )
            await session.execute(
                insert(Programme),
                [
                    dict(
                        feed="scale",
                        epg_channel=f"epg-{channel}",
                        start=now + timedelta(minutes=30 * slot - 5),
                        stop=now + timedelta(minutes=30 * (slot + 1) - 5),
                        title=f"Programme {channel}:{slot}",
                        age_evidence=evidence,
                    )
                    for slot in range(-25, 25)
                ],
            )
        await session.commit()
    print(
        {
            "seed_seconds": round(perf_counter() - started, 3),
            "titles": 10000,
            "classifications": 7500,
            "channels": 200,
            "programmes": 10000,
        },
        flush=True,
    )
    counts = Counter()

    def before_cursor(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            counts["selects"] += 1

    def loaded(_session, instance):
        counts[type(instance).__name__] += 1

    event.listen(api.c.db.engine.sync_engine, "before_cursor_execute", before_cursor)
    event.listen(Session, "loaded_as_persistent", loaded)

    async def headers():
        token = api.c.auth.issue(await policy.profile("scale-child"))
        return {"Authorization": "Bearer " + token["access_token"]}

    async def request(label, path, auth, **params):
        counts.clear()
        start = perf_counter()
        response = await api.client.get("/api/v1" + path, params=params, headers=auth)
        elapsed = perf_counter() - start
        assert response.status_code == 200, response.text
        print(
            {"stage": label, "milliseconds": round(elapsed * 1000, 2), "queries_and_orm_rows": dict(counts)}, flush=True
        )
        return response.json(), dict(counts)

    try:
        first_auth = await headers()
        first, cold = await request("browse-cold", "/browse/movies", first_auth, language="all", sort="title")
        assert first["total"] == 5000 and len(first["tiles"]) == 60
        assert cold.get("Title", 0) <= 120  # Page-bounded ORM materialization, not 10k title objects.
        warm, warm_counts = await request("browse-warm", "/browse/movies", first_auth, language="all", sort="title")
        assert warm == first and warm_counts.get("Title", 0) == 0
        second, _ = await request("browse-page-two", "/browse/movies", first_auth, language="all", sort="title", page=2)
        assert second["total"] == 5000 and len(second["tiles"]) == 60
        assert not ({tile["id"] for tile in first["tiles"]} & {tile["id"] for tile in second["tiles"]})
        cache_before = set(api.c.db.result_cache.entries)
        second_auth = await headers()
        same_profile, second_cold = await request(
            "browse-other-session", "/browse/movies", second_auth, language="all", sort="title"
        )
        assert same_profile["total"] == 5000 and second_cold.get("Title", 0) > 0
        assert set(api.c.db.result_cache.entries) - cache_before  # Session-bound output is not reused across sessions.
        channels, channel_counts = await request("channels-page", "/channels", first_auth, language="all", limit=20)
        assert channels["total"] == 100 and len(channels["channels"]) == 20
        assert channel_counts.get("Channel", 0) <= 40
        guide, _ = await request(
            "guide-ten-channels",
            "/epg",
            first_auth,
            channel_ids=",".join([f"ch:scale:{n}" for n in range(5)] + [f"ch:scale:{n}" for n in range(100, 105)]),
            **{"from": now.isoformat(), "to": (now + timedelta(hours=1)).isoformat()},
        )
        assert all(not rows for key, rows in guide.items() if key in {f"ch:scale:{n}" for n in range(100, 105)})
        assert all(guide.get(f"ch:scale:{n}") for n in range(5))
        assert sum(len(rows) for rows in guide.values()) == 15
        owner, _ = await request(
            "browse-unrestricted-owner", "/browse/movies", api.headers, language="all", sort="title"
        )
        assert owner["total"] == 10000
        await policy.update(
            "scale-child", {"restricted": True, "allowed_movie_codes": ["G"], "allowed_tv_codes": ["TV-G"]}
        )
        stale = await api.client.get("/api/v1/browse/movies", headers=first_auth)
        assert stale.status_code == 401
        narrower, _ = await request(
            "browse-after-restriction-change", "/browse/movies", await headers(), language="all", sort="title"
        )
        assert narrower["total"] == 2500
        async with api.c.db.session() as session:
            plans = (
                await session.execute(
                    text(
                        "EXPLAIN QUERY PLAN SELECT id FROM programme WHERE epg_channel = 'epg-1' AND start <= :at AND stop > :at AND feed = 'scale'"
                    ),
                    {"at": now.replace(tzinfo=None)},
                )
            ).all()
        assert any("ix_prog_channel_start" in row[-1] for row in plans)
        print({"epg_query_plan": [row[-1] for row in plans]}, flush=True)
    finally:
        event.remove(api.c.db.engine.sync_engine, "before_cursor_execute", before_cursor)
        event.remove(Session, "loaded_as_persistent", loaded)
