from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.infra.models import CacheEntry, CatalogRelease, Source, Title
from tests.test_users_migration import config


def test_existing_cache_and_ambiguous_source_years(tmp_path: Path):
    file = tmp_path / "catalog.db"
    cfg = config(file)
    command.upgrade(cfg, "0018")
    db = create_engine(f"sqlite:///{file}")
    with Session(db) as s:
        s.add(Source(id="x", type="xtream", name="Test", config_enc="fixture"))
        s.flush()
        inputs = [
            ("a", "Death Race 2050", 2050, 9),
            ("b", "Journey to 2099", 2099, None),
            ("c", "Future without digits", 2080, None),
            ("d", "2050 Film (2050)", 2050, None),
        ]
        for ext, title, year, tmdb in inputs:
            s.add(
                Title(
                    source_id="x",
                    kind="vod",
                    ext_id=ext,
                    title=title,
                    tmdb_id=tmdb,
                    year=year,
                    extra={"raw_title": title},
                )
            )
        for resource, tid, field, day in [
            ("movie/9", 9, "release_date", "2017-01-17"),
            ("tv/9", 9, "first_air_date", "2027-09-12"),
            ("movie/10", 11, "release_date", "2000-01-01"),
            ("movie/12/recommendations", 12, "release_date", "2025-02-02"),
            ("movie/13", 13, "release_date", "2026-02-30"),
        ]:
            s.add(
                CacheEntry(
                    key="tmdb:/" + resource + "?append_to_response=synthetic",
                    value={"id": tid, field: day},
                    expires_at=datetime.now(UTC),
                )
            )
        s.commit()
    command.upgrade(cfg, "0018a")
    with Session(db) as s:
        assert dict(s.execute(select(Title.ext_id, Title.year)).all()) == {"a": 2017, "b": None, "c": 2080, "d": 2050}
        assert sorted(
            s.execute(select(CatalogRelease.media_type, CatalogRelease.tmdb_id, CatalogRelease.release_date)).all()
        ) == [("movie", 9, "2017-01-17"), ("tv", 9, "2027-09-12")]
