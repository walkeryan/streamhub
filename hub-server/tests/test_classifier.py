"""The background job that keeps catalog titles classified ahead of anyone browsing them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.domain.entities import MetaTitle
from app.infra.models import AgeClassification, Source, Title
from app.services import classifier as classifier_module
from app.services.classification import classification_record, summarize
from app.services.classifier import CLASSIFY_BATCH, CURSOR_KEY, ClassificationJob
from app.services.sync import SyncScheduler
from tests.conftest import Api
from tests.people import member
from tests.test_adult_surfaces import shown

NOW = datetime.now(UTC)


def evidence(key: str, code: str, at: datetime) -> dict[str, Any]:
    movie = key.startswith("tmdb:movie:")
    record = classification_record("US", "US-MPA" if movie else "US-TVPG", code)
    return {
        **summarize("tmdb", "movie" if movie else "series", [record]),
        "subject_key": key,
        "retrieved_at": at.isoformat(),
    }


class FakeTmdb:
    """Stands in for the TMDB client: records evidence the way ``details`` does, or fails."""

    def __init__(self, api: Api) -> None:
        self.enabled = True
        self.calls: list[tuple[str, int]] = []
        self.down: set[tuple[str, int]] = set()
        self._record = api.c.tmdb._evidence_writer

    async def details(self, media_type: str, tmdb_id: int) -> MetaTitle | None:
        self.calls.append((media_type, tmdb_id))
        if (media_type, tmdb_id) in self.down:
            return None
        key = f"tmdb:{media_type}:{tmdb_id}"
        assert self._record is not None
        await self._record(key, evidence(key, "PG" if media_type == "movie" else "TV-PG", datetime.now(UTC)))
        return MetaTitle(tmdb_id, media_type, "Title", 2020)


@pytest.fixture
async def job(api: Api, monkeypatch: pytest.MonkeyPatch) -> ClassificationJob:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(classifier_module.asyncio, "sleep", no_sleep)
    async with api.c.db.session() as s:
        s.add(Source(id="catalog", name="Catalog", type="xtream", config_enc="unused"))
        await s.flush()
        for n in range(1, 8):
            s.add(Title(source_id="catalog", ext_id=f"m{n}", kind="vod", title=f"Film {n}", tmdb_id=n))
        # Two rows for one film count once; a show shares a number with a film and is its own key.
        s.add(Title(source_id="catalog", ext_id="m1-again", kind="movie", title="Film 1", tmdb_id=1))
        s.add(Title(source_id="catalog", ext_id="s1", kind="series", title="Show 1", tmdb_id=1))
        # Nothing to look up: no id, or a row that is judged by its show.
        s.add(Title(source_id="catalog", ext_id="noid", kind="vod", title="No Id"))
        s.add(Title(source_id="catalog", ext_id="e1", kind="episode", title="Episode", tmdb_id=77, parent_ext_id="s1"))
        s.add(
            AgeClassification(
                subject_key="tmdb:movie:2",
                provider="tmdb",
                evidence=evidence("tmdb:movie:2", "R", NOW - timedelta(days=1)),
                observed_at=NOW - timedelta(days=1),
            )
        )
        s.add(
            AgeClassification(
                subject_key="tmdb:movie:3",
                provider="tmdb",
                evidence=evidence("tmdb:movie:3", "R", NOW - timedelta(days=5)),
                observed_at=NOW - timedelta(days=5),
            )
        )
        await s.commit()
    job = ClassificationJob(api.c.db, FakeTmdb(api), api.c.app_settings, batch=3)
    return job


async def keys(api: Api) -> dict[str, AgeClassification]:
    async with api.c.db.session() as s:
        return {r.subject_key: r for r in (await s.execute(select(AgeClassification))).scalars()}


async def test_without_a_tmdb_key_the_job_does_nothing(api: Api, job: ClassificationJob) -> None:
    job._meta.enabled = False
    assert await job.run_batch() == {"classified": 0, "failed": 0, "remaining": 0}
    assert job._meta.calls == [] and set(await keys(api)) == {"tmdb:movie:2", "tmdb:movie:3"}
    # The real container is wired the same way and has no key in tests.
    assert await api.c.classifier.run_batch() == {"classified": 0, "failed": 0, "remaining": 0}


async def test_batches_walk_the_catalog_and_resume_where_they_stopped(api: Api, job: ClassificationJob) -> None:
    assert await job.remaining() == 7  # movies 1,3..7 and show 1; movie 2 is fresh
    first = await job.run_batch()
    assert first == {"classified": 3, "failed": 0, "remaining": 4}
    assert job._meta.calls == [("movie", 1), ("movie", 3), ("movie", 4)]
    assert api.c.app_settings.get(CURSOR_KEY) == "movie:4"
    # A new process picks the walk up from the stored cursor.
    again = ClassificationJob(api.c.db, job._meta, api.c.app_settings, batch=3)
    assert await again.run_batch() == {"classified": 3, "failed": 0, "remaining": 1}
    assert job._meta.calls[3:] == [("movie", 5), ("movie", 6), ("movie", 7)]
    assert await again.run_batch() == {"classified": 1, "failed": 0, "remaining": 0}
    assert job._meta.calls[6:] == [("tv", 1)]
    assert api.c.app_settings.get(CURSOR_KEY) == ""  # the lap is over
    # Everything is fresh now: nothing to do, nothing fetched.
    assert await again.run_batch() == {"classified": 0, "failed": 0, "remaining": 0}
    assert len(job._meta.calls) == 7
    rows = await keys(api)
    assert set(rows) == {f"tmdb:movie:{n}" for n in range(1, 8)} | {"tmdb:tv:1"}
    assert rows["tmdb:movie:2"].evidence["records"][0]["code"] == "R"  # fresh evidence was left alone
    assert rows["tmdb:movie:3"].evidence["records"][0]["code"] == "PG"  # ageing evidence was renewed
    assert all(r.observed_at > NOW - timedelta(days=2) for r in rows.values())


async def test_a_title_that_cannot_be_fetched_does_not_hold_the_walk_up(api: Api, job: ClassificationJob) -> None:
    job._meta.down = {("movie", 1), ("movie", 4)}
    assert await job.run_batch() == {"classified": 1, "failed": 2, "remaining": 6}
    assert await job.run_batch() == {"classified": 3, "failed": 0, "remaining": 3}
    assert job._meta.calls[3:] == [("movie", 5), ("movie", 6), ("movie", 7)]
    assert await job.run_batch() == {"classified": 1, "failed": 0, "remaining": 2}
    # The next lap comes back to the two that failed.
    job._meta.down = set()
    assert await job.run_batch() == {"classified": 2, "failed": 0, "remaining": 0}
    assert job._meta.calls[-2:] == [("movie", 1), ("movie", 4)]


async def test_an_outage_ends_the_batch_early(api: Api, job: ClassificationJob) -> None:
    job._batch = 50
    job._meta.down = {("movie", n) for n in range(1, 8)} | {("tv", 1)}
    out = await job.run_batch()
    assert out["classified"] == 0 and out["failed"] == classifier_module.FAILURE_STREAK
    assert len(job._meta.calls) == classifier_module.FAILURE_STREAK


async def test_a_corrupt_cursor_restarts_the_lap(api: Api, job: ClassificationJob) -> None:
    await api.c.app_settings._put(CURSOR_KEY, "nonsense")
    assert (await job.run_batch())["classified"] == 3
    assert job._meta.calls[0] == ("movie", 1)


async def test_the_scheduler_runs_the_job(api: Api) -> None:
    ran: list[int] = []

    async def classify() -> None:
        ran.append(1)

    scheduler = SyncScheduler(api.c.sync, classify=classify)
    scheduler.start()
    try:
        scheduled = scheduler._sched.get_job("classification")
        assert scheduled is not None and scheduled.trigger.interval == timedelta(minutes=2)
        await scheduled.func()
    finally:
        scheduler.shutdown()
    assert ran == [1] and CLASSIFY_BATCH == 60


async def test_run_endpoint_needs_system_manage(api: Api) -> None:
    r = await api.post("/settings/classification/run")
    assert r.status_code == 200 and r.json() == {"classified": 0, "failed": 0, "remaining": 0}
    parent = await member(api, "family", "Parent")
    refused = await parent.post("/settings/classification/run")
    assert refused.status_code == 403 and refused.json()["missing"] == ["system.manage"]


async def test_a_classified_title_reaches_a_restricted_user_without_a_first_view(
    api: Api, job: ClassificationJob
) -> None:
    kid = await member(api, "kid", "Kid")

    async def seen() -> set[str]:
        return shown((await kid.get("/browse/movies", params={"language": "all"})).json())[1]

    assert await seen() == set()
    while (await job.run_batch())["remaining"]:
        pass
    assert await seen() == {f"Film {n}" for n in (1, 3, 4, 5, 6, 7)}  # all rated PG by the job; film 2 stays R
