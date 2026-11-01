from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.adapters.tmdb.client import _meta_from_json, to_meta
from app.adapters.xmltv.parser import parse_bytes
from app.domain.entities import EpgSourceSpec, ProgrammeInfo
from app.infra.models import AgeClassification, Programme
from app.services.classification import tmdb_age_evidence, xmltv_age_evidence
from tests.test_epg_integrity import add_source


def movie(*codes, country="US"):
    return {
        "id": 11,
        "title": "Fixture",
        "vote_average": 9.8,
        "release_dates": {
            "results": [
                {"iso_3166_1": country, "release_dates": [{"certification": code, "type": 3} for code in codes]}
            ]
        },
    }


@pytest.mark.parametrize(
    ("codes", "country", "status"),
    [
        (["PG-13"], "US", "known"),
        (["PG-13", "R"], "US", "conflicting"),
        (["NR"], "US", "unknown"),
        (["PG-13", "NR"], "US", "unknown"),
        ([""], "US", "unknown"),
        (["99"], "US", "unknown"),
        (["12A"], "GB", "unknown"),
        (["PG-13", ""], "US", "known"),
    ],
)
def test_country_codes_conflicts_and_unrated(codes, country, status):
    result = tmdb_age_evidence(movie(*codes, country=country), "movie")
    assert result["status"] == status
    assert result["records"][0]["country"] == country
    assert result["scope"] == "movie"


def test_scores_and_old_cached_meta_do_not_become_age_classifications():
    meta = to_meta({"id": 11, "title": "Fixture", "vote_average": 10, "adult": False}, "movie")
    assert meta.rating == 10 and meta.age_evidence["status"] == "unknown"
    assert meta.age_evidence["reason"] == "not_loaded"
    old = _meta_from_json({"tmdb_id": 11, "media_type": "movie", "title": "Old cached title", "rating": 9})
    assert old.age_evidence == {}
    tv = tmdb_age_evidence(
        {"id": 11, "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-Y7-FV"}]}}, "tv"
    )
    assert tv["status"] == "known" and tv["scope"] == "series"
    assert tv["subject_key"] == "tmdb:tv:11"
    assert tv["records"][0]["code"] == "TV-Y7" and tv["records"][0]["descriptors"] == ["FV"]


def test_xmltv_age_rating_is_not_star_rating():
    data = b"""<tv><programme channel="fixture" start="20261101100000 +0000" stop="20261101110000 +0000">
    <title>Children genre is not age evidence</title><category>Kids</category>
    <star-rating system="IMDb"><value>10/10</value></star-rating>
    <rating system="MPAA"><value>R</value></rating><rating system="BBFC"><value>15</value></rating>
    </programme></tv>"""
    result = parse_bytes(data)[0].age_evidence
    assert result["status"] == "known" and result["provider"] == "xmltv"
    assert len(result["records"]) == 2 and result["records"][0]["code"] == "R"
    assert result["records"][1]["status"] == "unknown" and result["records"][1]["raw_system"] == "BBFC"
    assert xmltv_age_evidence([(None, "PG")])["status"] == "unknown"
    assert xmltv_age_evidence([("MPAA", "PG")] * 17)["reason"] == "truncated_evidence"
    unknown = parse_bytes(
        data.replace(b'<rating system="MPAA"><value>R</value></rating>', b"").replace(
            b'<rating system="BBFC"><value>15</value></rating>', b""
        )
    )[0]
    assert unknown.age_evidence["status"] == "unknown" and not unknown.age_evidence["records"]


async def test_tmdb_details_append_persist_and_mark_fetch_failure(api):
    client = api.c.catalog.meta
    client._get = AsyncMock(return_value=movie("PG-13"))
    result = await client.details("movie", 11)
    assert result.age_evidence["status"] == "known"
    assert "release_dates" in client._get.call_args.kwargs["append_to_response"]
    client._get.return_value = {
        "id": 11,
        "name": "Series",
        "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-MA"}]},
    }
    await client.details("tv", 11)
    assert "content_ratings" in client._get.call_args.kwargs["append_to_response"]
    async with api.c.db.session() as session:
        rows = list((await session.execute(select(AgeClassification))).scalars())
    assert {r.subject_key for r in rows} == {"tmdb:movie:11", "tmdb:tv:11"}
    assert all(r.evidence["status"] == "known" for r in rows)
    client._get.return_value = None
    await client.details("movie", 11)
    async with api.c.db.session() as session:
        failed = await session.get(AgeClassification, ("tmdb:movie:11", "tmdb"))
        assert failed.evidence["status"] == "unknown" and failed.evidence["reason"] == "fetch_unavailable"
    client._get.return_value = movie("R")
    await client.details("movie", 999)
    async with api.c.db.session() as session:
        assert await session.get(AgeClassification, ("tmdb:movie:999", "tmdb")) is None


async def test_xmltv_evidence_survives_source_scoped_sync(api, monkeypatch):
    source = await add_source(api)
    now = datetime.now(UTC)
    evidence = xmltv_age_evidence([("TVPG", "TV-14")])

    class Provider:
        async def epg_sources(self):
            return [EpgSourceSpec(url="https://fixture.invalid/epg")]

    class Epg:
        async def iter_programmes(self, spec, wanted):
            yield [
                ProgrammeInfo(
                    channel="bbc1.uk", start=now, stop=now + timedelta(hours=1), title="Fixture", age_evidence=evidence
                )
            ]

    monkeypatch.setattr(api.c.sources, "provider", lambda row: Provider())
    monkeypatch.setattr(api.c.sync, "_epg", Epg())
    assert await api.c.sync.refresh_epg(source["id"]) == 1
    async with api.c.db.session() as session:
        row = (await session.execute(select(Programme).where(Programme.feed == source["id"]))).scalar_one()
        assert row.age_evidence["records"][0]["code"] == "TV-14"
        assert row.age_evidence["subject"]["source_id"] == source["id"]


def test_additive_migration_preserves_existing_data(tmp_path):
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).parents[1]
    path = tmp_path / "legacy.db"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(cfg, "0001")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO programme(epg_channel,feed,start,stop,title) VALUES ('channel','source','2026-11-01','2026-11-02','Existing guide')"
        )
        conn.execute(
            "INSERT INTO profile(id,name,is_admin,theme_id,languages,kids,pin_hash,password_hash,token_version,prefs,created_at) VALUES ('existing','Existing',1,'midnight','[\"eng\"]',0,'retained-pin-hash','retained-password-hash',7,'{}','2026-11-01')"
        )
        before = conn.execute("SELECT * FROM profile").fetchall()
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as conn:
        assert [row[: len(before[0])] for row in conn.execute("SELECT * FROM profile").fetchall()] == before
        assert conn.execute("SELECT content_policy FROM profile").fetchone() == ("{}",)
        assert conn.execute("SELECT title,age_evidence FROM programme").fetchone() == ("Existing guide", "{}")
        assert conn.execute("SELECT count(*) FROM age_classification").fetchone() == (0,)


async def test_unchanged_cached_evidence_is_not_rewritten(api):
    from app.services.classification import ClassificationStore

    store = ClassificationStore(api.c.db)
    evidence = tmdb_age_evidence(movie("PG"), "movie")
    await store.record("tmdb:movie:11", evidence)
    async with api.c.db.session() as session:
        before = (await session.get(AgeClassification, ("tmdb:movie:11", "tmdb"))).observed_at
    await store.record("tmdb:movie:11", evidence)
    async with api.c.db.session() as session:
        assert (await session.get(AgeClassification, ("tmdb:movie:11", "tmdb"))).observed_at == before
    client = api.c.catalog.meta
    client._get = AsyncMock(return_value=movie("PG"))
    client._evidence_writer = AsyncMock(side_effect=RuntimeError("fixture storage failure"))
    assert (await client.details("movie", 11)).title == "Fixture"
