from unittest.mock import AsyncMock

import pytest

from app.domain.entities import MetaTitle
from app.infra.models import Title
from app.services.ids import clean_display_title, normalise_title
from tests.conftest import Api
from tests.test_large_catalog_filters import seed_catalog


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("4K-EN-NETFLIX-Star Wars (1977)", "Star Wars"),
        ("D+ | FR | Star Wars: The Force Awakens (2015)", "Star Wars: The Force Awakens"),
        ("Max", "Max"),
        ("Star Wars: The Last Jedi", "Star Wars: The Last Jedi"),
    ],
)
def test_clean_provider_labels(raw, clean):
    assert clean_display_title(raw) == clean


async def setup(api):
    await seed_catalog(api)
    entries = [
        ("original", "EN-NETFLIX-Star Wars (1977)", 11, 1977, "UK| Movies"),
        ("duplicate", "4K-EN-Star Wars (1977)", 11, 1977, "UK| Movies"),
        ("french", "FR-Star Wars: The Force Awakens", 140607, 2015, "FR| Movies"),
        ("parody", "Maggie Simpson in Star Wars", 825597, 2021, "UK| Movies"),
        ("fallback", "EN-NETFLIX-Star Wars: The Last Jedi (2017)", None, 2017, "UK| Movies"),
        ("wrong-year", "Star Wars: The Last Jedi", None, 2020, "UK| Movies"),
    ]
    async with api.c.db.session() as session:
        for ext, title, tmdb, year, group in entries:
            session.add(
                Title(
                    source_id="catalog",
                    ext_id=ext,
                    kind="vod",
                    title=title,
                    norm_title=normalise_title(title),
                    tmdb_id=tmdb,
                    year=year,
                    group_name=group,
                )
            )
        await session.commit()
    fake = AsyncMock()
    fake.enabled = True
    fake.search.return_value = [
        MetaTitle(825597, "movie", "Maggie Simpson in Star Wars", 2021),
        MetaTitle(140607, "movie", "Star Wars: The Force Awakens", 2015),
        MetaTitle(11, "movie", "Star Wars", 1977),
        MetaTitle(181808, "movie", "Star Wars: The Last Jedi", 2017),
        MetaTitle(999, "movie", "Star Wars: Unavailable Fixture", 2030),
    ]
    fake.details.return_value = None
    api.c.catalog.meta = fake
    return fake


async def test_discovery_independent_of_english_and_source_scope(api: Api):
    fake = await setup(api)
    result = (await api.get("/search", params={"q": "Star Wars", "language": "eng", "source_id": "catalog"})).json()
    assert result["metadata_available"]
    fake.search.assert_awaited_once()
    hits = result["results"]
    assert hits[0]["id"] == "tmdb:movie:11"
    assert hits[0]["title"] == "Star Wars"
    assert sum(t["id"] == "tmdb:movie:11" for t in hits) == 1
    assert next(t for t in hits if t["id"] == "tmdb:movie:140607")["sources"] == []
    assert next(t for t in hits if t["id"] == "tmdb:movie:999")["sources"] == []
    assert next(t for t in hits if t["id"] == "tmdb:movie:181808")["sources"]
    assert not any(t["id"] == "lib:catalog:fallback" for t in hits)
    assert hits.index(next(t for t in hits if t["id"] == "tmdb:movie:140607")) < hits.index(
        next(t for t in hits if t["id"] == "tmdb:movie:825597")
    )
    assert all(t["sources"] for t in result["play_here"])
    scoped = (await api.get("/search", params={"q": "Star Wars", "language": "all", "source_id": "disabled"})).json()
    assert scoped["results"] and all(not t["sources"] for t in scoped["results"])


async def test_identity_never_joins_fuzzy_parody_unknown_year_or_conflicting_id(api: Api):
    await setup(api)
    async with api.c.db.session() as session:
        for ext, title, year, tmdb, imdb, kind in [
            ("no-year", "Star Wars: The Last Jedi", None, None, None, "vod"),
            ("wrong-id", "Star Wars: The Last Jedi", 2017, 44, None, "vod"),
            ("wrong-imdb", "Star Wars: The Last Jedi", 2017, None, "ttwrong", "vod"),
            ("parody-no-id", "Star Wars: The Last Jedi Parody", 2017, None, None, "vod"),
            ("series", "Star Wars: The Last Jedi", 2017, None, None, "series"),
        ]:
            session.add(
                Title(
                    source_id="catalog",
                    ext_id=ext,
                    kind=kind,
                    title=title,
                    norm_title=normalise_title(title),
                    year=year,
                    tmdb_id=tmdb,
                    imdb_id=imdb,
                )
            )
        await session.commit()
    matches = await api.c.availability.library_matches(
        "movie", tmdb_id=181808, imdb_id=None, title="Star Wars: The Last Jedi", year=2017
    )
    assert [m.ext_id for m in matches] == ["fallback"]


async def test_metadata_failure_keeps_local_search_honest(api: Api):
    fake = await setup(api)
    fake.search.side_effect = RuntimeError("fixture unavailable")
    result = (await api.get("/search", params={"q": "Star Wars", "language": "all"})).json()
    assert not result["metadata_available"]
    assert "temporarily unavailable" in result["discovery_note"]
    assert result["results"]
