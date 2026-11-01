from unittest.mock import AsyncMock

from sqlalchemy import insert

from app.domain.entities import MetaTitle
from app.infra.models import Favorite, Progress, Source, Title
from app.services.ids import normalise_title
from tests.test_large_catalog_filters import seed_catalog


async def seeded(api, count=65):
    await seed_catalog(api)
    async with api.c.db.session() as session:
        session.add(Source(id="second", name="Second", type="xtream", config_enc="unused"))
        await session.flush()
        rows = []
        for source, prefix, group in [("catalog", "EN-NETFLIX-", "UK| Movies"), ("second", "FR-", "FR| Movies")]:
            for variant in range(2):
                for i in range(count):
                    title = f"{prefix}Film {i:03} (2000)"
                    rows.append(
                        dict(
                            source_id=source,
                            ext_id=f"{i}-{variant}",
                            kind="vod",
                            title=title,
                            norm_title=normalise_title(title),
                            tmdb_id=1000 + i,
                            year=2000,
                            group_name=group,
                            genres=["Drama"],
                        )
                    )
        await session.execute(insert(Title), rows)
        session.add(Favorite(profile_id=api.profile_id, kind="title", ref_id="lib:second:0-1"))
        session.add(Progress(profile_id=api.profile_id, ref_id="lib:second:0-1", position_ms=25, duration_ms=100))
        await session.commit()
    fake = AsyncMock()
    fake.enabled = True
    fake.details.side_effect = lambda kind, tid: MetaTitle(
        tid, kind, f"Canonical {tid}", year=2000, poster="https://fixture.invalid/poster"
    )
    api.c.catalog.meta = fake
    return fake


async def test_canonical_paging_metadata_and_variant_state(api):
    meta = await seeded(api)
    first = (await api.get("/browse/movies", params={"language": "all", "sort": "title"})).json()
    assert first["total"] == 65 and len(first["tiles"]) == 60 and first["has_more"]
    assert meta.details.await_count == 60
    assert all(t["title"].startswith("Canonical ") and len(t["sources"]) == 2 for t in first["tiles"])
    saved = next(t for t in first["tiles"] if t["id"] == "tmdb:movie:1000")
    assert saved["is_favorite"] and saved["progress_pct"] == 25
    second = (await api.get("/browse/movies", params={"language": "all", "sort": "title", "page": 2})).json()
    assert second["total"] == 65 and len(second["tiles"]) == 5 and not second["has_more"]
    assert not ({t["id"] for t in first["tiles"]} & {t["id"] for t in second["tiles"]})
    assert meta.details.await_count == 65
    english = (
        await api.get("/browse/movies", params={"language": "eng", "source_id": "catalog", "sort": "title"})
    ).json()
    assert english["total"] == 65 and all(
        len(t["sources"]) == 1 and t["sources"][0]["source_id"] == "catalog" for t in english["tiles"]
    )
    assert next(t for t in english["tiles"] if t["id"] == "tmdb:movie:1000")["is_favorite"]
    facets = (await api.get("/browse/movies/genres", params={"language": "all"})).json()
    assert facets == [{"name": "drama", "count": 65}]
    languages = (await api.get("/browse/movies/languages")).json()
    assert {x["code"]: x["count"] for x in languages} == {"eng": 65, "fra": 65}


async def test_unavailable_metadata_and_no_id_titles_remain_honest(api):
    meta = await seeded(api, 1)
    meta.details.side_effect = RuntimeError("offline fixture")
    async with api.c.db.session() as session:
        for ext, year in [("unknown", None), ("remake", 2020)]:
            session.add(
                Title(
                    source_id="catalog",
                    ext_id=ext,
                    kind="vod",
                    title="EN-NETFLIX-Film 000",
                    norm_title="en netflix film 000",
                    year=year,
                    tmdb_id=0 if ext == "unknown" else None,
                    group_name="UK| Movies",
                )
            )
        await session.commit()
    result = (await api.get("/browse/movies", params={"language": "all"})).json()
    assert result["total"] == 3 and len(result["tiles"]) == 3
    assert all(t["title"] == "Film 000" for t in result["tiles"])
    assert len({t["id"] for t in result["tiles"]}) == 3


async def test_detail_preserves_variant_history_and_metadata_failure(api):
    meta = await seeded(api, 1)
    detail = await api.c.catalog.detail("tmdb:movie:1000", api.profile_id)
    assert detail["title"] == "Canonical 1000"
    assert detail["is_favorite"] and detail["progress_pct"] == 25
    meta.details.side_effect = RuntimeError("offline fixture")
    fallback = await api.c.catalog.detail("tmdb:movie:1000", api.profile_id)
    assert fallback["title"] == "Film 000"
    assert fallback["is_favorite"] and fallback["progress_pct"] == 25
    meta.details.side_effect = lambda kind, tid: MetaTitle(999, kind, "Wrong title")
    mismatched = await api.get("/browse/movies", params={"language": "all"})
    assert mismatched.json()["tiles"][0]["title"] == "Film 000"


async def test_large_canonical_browse_cold_warm(api):
    import time

    await seed_catalog(api)
    async with api.c.db.session() as session:
        for start in range(0, 212000, 2000):
            await session.execute(
                insert(Title),
                [
                    dict(
                        source_id="catalog",
                        ext_id=str(i),
                        kind="vod",
                        title=f"EN-Title {i // 4:06}",
                        norm_title=f"title {i // 4:06}",
                        tmdb_id=1000 + i // 4,
                        year=2000,
                        overview="Fixture description " * 5,
                    )
                    for i in range(start, start + 2000)
                ],
            )
        await session.commit()
    fake = AsyncMock()
    fake.enabled = True
    fake.details.side_effect = lambda kind, tid: MetaTitle(tid, kind, f"Canonical {tid}")
    api.c.catalog.meta = fake
    started = time.perf_counter()
    cold = await api.get("/browse/movies", params={"language": "all", "sort": "title"})
    cold_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    warm = await api.get("/browse/movies", params={"language": "all", "sort": "title"})
    warm_ms = (time.perf_counter() - started) * 1000
    print(f"canonical browse212000 cold={cold_ms:.2f}ms warm={warm_ms:.2f}ms")
    assert cold.status_code == warm.status_code == 200
    assert cold.json() == warm.json()
    assert cold.json()["total"] == 53000 and len(cold.json()["tiles"]) == 60
    assert fake.details.await_count == 60
    assert cold_ms < 5000
