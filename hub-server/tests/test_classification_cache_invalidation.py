from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.infra.models import AgeClassification
from app.services.classification import ClassificationStore


def evidence(at, code="PG"):
    return {"provider": "tmdb", "retrieved_at": at, "status": "known", "code": code}


async def test_classification_commit_invalidates_all_profiles_without_read_churn(api):
    db = api.c.db
    store = ClassificationStore(db)
    before = [db.catalog_revisions.version(p) for p in (api.profile_id, "other")]
    stamp = datetime.now(UTC) - timedelta(hours=1)
    data = evidence(stamp.isoformat())
    await store.record("tmdb:movie:11", data)
    changed = [db.catalog_revisions.version(p) for p in (api.profile_id, "other")]
    assert all(a != b for a, b in zip(before, changed, strict=True))
    await store.record("tmdb:movie:11", data)
    assert [db.catalog_revisions.version(p) for p in (api.profile_id, "other")] == changed
    async with db.session() as session:
        row = await session.get(AgeClassification, ("tmdb:movie:11", "tmdb"))
        assert row.observed_at == stamp
        row.evidence = evidence(stamp.isoformat(), "R")
        await session.flush()
        await session.rollback()
    assert db.catalog_revisions.version(api.profile_id) == changed[0]
    async with db.session() as session:
        await session.execute(delete(AgeClassification).where(AgeClassification.subject_key == "tmdb:movie:11"))
        await session.commit()
    assert db.catalog_revisions.version(api.profile_id) != changed[0]


async def test_fresh_evidence_refreshes_but_late_cached_evidence_cannot_replace_it(api):
    db = api.c.db
    store = ClassificationStore(db)
    old = datetime.now(UTC) - timedelta(days=10)
    fresh = datetime.now(UTC) - timedelta(minutes=1)
    await store.record("tmdb:movie:11", evidence(old.isoformat()))
    before = db.catalog_revisions.version(api.profile_id)
    await store.record("tmdb:movie:11", evidence(fresh.isoformat(), "R"))
    current = db.catalog_revisions.version(api.profile_id)
    assert current != before
    await store.record("tmdb:movie:11", evidence(old.isoformat()))
    assert db.catalog_revisions.version(api.profile_id) == current
    async with db.session() as session:
        row = await session.get(AgeClassification, ("tmdb:movie:11", "tmdb"))
        assert row.observed_at == fresh
        assert row.evidence["code"] == "R"


@pytest.mark.parametrize("stamp", [None, "invalid", "2026-01-01T12:00:00", "2999-01-01T00:00:00+00:00"])
async def test_unverifiable_observation_is_not_freshened_on_read(api, stamp):
    await ClassificationStore(api.c.db).record("tmdb:movie:11", evidence(stamp))
    async with api.c.db.session() as session:
        row = await session.get(AgeClassification, ("tmdb:movie:11", "tmdb"))
        assert row.observed_at == datetime.fromtimestamp(0, UTC)
