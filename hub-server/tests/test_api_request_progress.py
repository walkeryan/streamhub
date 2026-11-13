"""Request progress end to end: the request manager is respx, the clock is the test's."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.infra.models import MediaRequest, Notice
from app.services import request_progress as rp
from app.services import requests as requests_module
from app.services.sync import SyncScheduler
from tests.conftest import Api
from tests.test_api_requests import MANAGER, MANAGER_KEY, MOVIE, SHOW, TMDB, add_library_movie, configure, viewer

START = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
REQUEST_URL = re.compile(rf"{re.escape(MANAGER)}/api/v1/request/(\d+)")


class Clock:
    def __init__(self) -> None:
        self.t = START

    def __call__(self) -> datetime:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class Manager:
    """The request manager as respx: one settable answer per request id, every call counted."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.answers: dict[int, Any] = {}
        self.asked: list[int] = []
        self.active = self.peak = 0
        self.delay = 0.0
        self.next_id = 40
        router.get(f"{TMDB}/movie/603").mock(return_value=httpx.Response(200, json=MOVIE))
        router.get(f"{TMDB}/tv/1399").mock(return_value=httpx.Response(200, json=SHOW))
        router.get(f"{TMDB}/movie/603/watch/providers").mock(return_value=httpx.Response(200, json={"results": {}}))
        router.get(f"{TMDB}/tv/1399/watch/providers").mock(return_value=httpx.Response(200, json={"results": {}}))
        router.get(f"{TMDB}/search/multi").mock(
            return_value=httpx.Response(200, json={"results": [{**MOVIE, "media_type": "movie"}]})
        )
        self.create = router.post(f"{MANAGER}/api/v1/request").mock(side_effect=self._create)
        self.status = router.get(REQUEST_URL).mock(side_effect=self._status)

    def _create(self, request: httpx.Request) -> httpx.Response:
        self.next_id += 1
        return httpx.Response(201, json={"id": self.next_id, "status": 2})

    async def _status(self, request: httpx.Request) -> httpx.Response:
        request_id = int(request.url.path.rsplit("/", 1)[1])
        self.asked.append(request_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            answer = self.answers.get(request_id, body())
            if isinstance(answer, Exception):
                raise answer
            return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)
        finally:
            self.active -= 1


def body(request: int = 2, media: int | None = 3, queue: Any = None, **more: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"status": media, **more}
    if queue is not None:
        info["downloadStatus"] = queue
    return {"status": request, "media": info}


def item(size: Any = 1000, left: Any = 500, status: Any = "downloading", **more: Any) -> dict[str, Any]:
    return {"title": "fixture queue item", "size": size, "sizeLeft": left, "status": status, **more}


@pytest.fixture
def manager() -> Iterator[Manager]:
    with respx.mock(assert_all_called=False) as router:
        yield Manager(router)


@pytest.fixture
def clock(api: Api) -> Clock:
    clock = Clock()
    api.c.requests.now = clock
    return clock


async def sent_movie(api: Api) -> dict[str, Any]:
    await configure(api)
    r = await api.post("/requests", json={"media_type": "movie", "tmdb_id": 603})
    assert r.status_code == 201 and r.json()["state"] == "sent", r.text
    return r.json()


async def read(api: Api, request_id: str) -> dict[str, Any]:
    r = await api.get(f"/requests/{request_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def add_sent_rows(api: Api, count: int) -> list[str]:
    async with api.c.db.session() as s:
        rows = [
            MediaRequest(
                id=f"{n:016x}",
                profile_id=api.profile_id,
                media_type="movie",
                tmdb_id=9000 + n,
                title=f"Fixture film {n}",
                state="sent",
                manager_request_id=500 + n,
            )
            for n in range(count)
        ]
        s.add_all(rows)
        await s.commit()
    return [row.id for row in rows]


async def test_a_new_request_never_says_downloading_first(api: Api, manager: Manager, clock: Clock) -> None:
    await configure(api)
    headers = await viewer(api)
    r = await api.client.post("/api/v1/requests", headers=headers, json={"media_type": "tv", "tmdb_id": 1399})
    waiting = r.json()
    assert waiting["state"] == "pending_approval"
    assert waiting["progress"] == {
        "phase": "requested",
        "last_phase": None,
        "percent": None,
        "eta": None,
        "detail": None,
        "checked_at": waiting["updated_at"],
    }
    # The owner's own request is approved and sent in one call; all that is known is that the manager took it.
    created = (await api.post("/requests", json={"media_type": "movie", "tmdb_id": 603})).json()
    assert created["state"] == "sent"
    assert (created["progress"]["phase"], created["progress"]["percent"]) == ("searching", None)
    assert manager.asked == []
    # The first real answer: accepted, nothing in the download queue.
    first = await read(api, created["id"])
    assert (first["progress"]["phase"], first["progress"]["percent"]) == ("searching", None)
    assert first["progress"]["checked_at"].startswith("2030-01-01T12:00:00") and manager.asked == [41]
    # Only a queue item makes it a download.
    manager.answers[41] = body(queue=[item(4000, 3000)])
    clock.tick(rp.SLOW_S)
    assert (await read(api, created["id"]))["progress"]["phase"] == "downloading"
    # Neither request, at any point, told the tiles more than it knew.
    assert [
        x["progress"]["phase"] for x in (await api.get("/requests", params={"scope": "all"})).json()["requests"]
    ] == [
        "downloading",
        "requested",
    ]


async def test_an_approved_request_the_manager_has_not_taken_is_approved(
    api: Api, manager: Manager, clock: Clock
) -> None:
    await configure(api)
    manager.create.mock(return_value=httpx.Response(500, json={"message": "see http://media.example.test:7878/log"}))
    created = (await api.post("/requests", json={"media_type": "movie", "tmdb_id": 603})).json()
    assert created["state"] == "approved"
    assert created["progress"]["phase"] == "approved" and manager.asked == []
    # The manager's own words are passed on unless they name an address.
    assert created["progress"]["detail"] == "Not sent to the request manager yet."
    manager.create.mock(return_value=httpx.Response(500, json={"message": "Database is busy"}))
    # "Check again" on an unsent request is the retry.
    retried = await api.post(f"/requests/{created['id']}/refresh")
    assert retried.json()["progress"]["detail"] == "The request manager answered 500: Database is busy"
    manager.create.mock(side_effect=manager._create)
    assert (await api.post(f"/requests/{created['id']}/refresh")).json()["state"] == "approved"
    clock.tick(requests_module.MANUAL_REFRESH_MIN_S)
    sent = (await api.post(f"/requests/{created['id']}/refresh")).json()
    assert (sent["state"], sent["progress"]["phase"], sent["note"]) == ("sent", "searching", None)


async def test_walk_from_searching_to_available(api: Api, manager: Manager, clock: Clock) -> None:
    created = await sent_movie(api)
    steps: list[tuple[Any, str, int | None]] = [
        (body(), "searching", None),
        (body(queue=[]), "searching", None),
        (body(queue=[item(4000, 4000, "queued")]), "downloading", 0),
        (body(queue=[item(4000, 3000)]), "downloading", 25),
        (body(queue=[item(4000, 400)]), "downloading", 90),
        (body(queue=[item(4000, 0, "completed")]), "importing", None),
    ]
    for answer, phase, percent in steps:
        manager.answers[41] = answer
        clock.tick(rp.SLOW_S)
        got = await read(api, created["id"])
        assert (got["state"], got["progress"]["phase"], got["progress"]["percent"]) == ("sent", phase, percent)
        assert got["progress"]["last_phase"] is None and got["updated_at"] == created["updated_at"]
    assert "fixture queue item" not in (await api.get("/requests")).text
    manager.answers[41] = body(media=5)
    clock.tick(rp.FAST_S)
    done = await read(api, created["id"])
    assert (done["state"], done["progress"]["phase"], done["progress"]["percent"]) == ("available", "available", None)
    # Told once, on the transition.
    clock.tick(rp.SLOW_S)
    await read(api, created["id"])
    await api.c.requests.poll_due()
    async with api.c.db.session() as s:
        notices = list((await s.execute(select(Notice))).scalars())
    assert [(n.kind, n.title) for n in notices] == [("request_available", "The Matrix is now available")]
    assert manager.asked.count(41) == len(steps) + 1


async def test_eta_and_series_detail_reach_the_api(api: Api, manager: Manager, clock: Clock) -> None:
    await configure(api)
    created = (await api.post("/requests", json={"media_type": "tv", "tmdb_id": 1399, "seasons": [1]})).json()
    due = (START + timedelta(minutes=12)).isoformat().replace("+00:00", "Z")
    manager.answers[41] = body(
        queue=[
            item(1000, 0, "completed", episode={"seasonNumber": 1, "episodeNumber": 1}),
            item(1000, 500, estimatedCompletionTime=due),
            item(2000, 2000, "queued", timeLeft="00:10:00"),
        ]
    )
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["percent"], got["detail"]) == ("downloading", 37, "1 of 3 episodes")
    assert datetime.fromisoformat(got["eta"]) == START + timedelta(minutes=12)


async def test_tiles_and_the_title_page_carry_phase_and_percent(api: Api, manager: Manager, clock: Clock) -> None:
    await configure(api)
    detail = (await api.get("/titles/tmdb:movie:603")).json()
    assert detail["request_state"] is None and detail["request_progress"] is None
    created = await sent_movie(api)
    manager.answers[41] = body(queue=[item(1000, 400)])
    await read(api, created["id"])
    detail = (await api.get("/titles/tmdb:movie:603")).json()
    assert detail["request_state"] == "sent" and detail["request_progress"] == {"phase": "downloading", "percent": 60}
    tile = (await api.get("/search", params={"q": "matrix"})).json()["results"][0]
    assert tile["request_progress"] == {"phase": "downloading", "percent": 60}
    # Tiles serve what is stored: showing a page never reaches the manager.
    asked = len(manager.asked)
    clock.tick(rp.SLOW_S * 4)
    await api.get("/titles/tmdb:movie:603")
    await api.get("/search", params={"q": "matrix"})
    assert len(manager.asked) == asked


@pytest.mark.parametrize(
    ("answer", "phase"),
    [
        ({"status": 2}, "searching"),
        ({"status": 2, "media": None}, "searching"),
        ({"status": 2, "media": {"status": 3, "downloadStatus": None}}, "searching"),
        ({"status": 2, "media": {"status": 3, "downloadStatus": "busy"}}, "searching"),
        ({"status": 2, "media": {"status": 3, "downloadStatus": ["x", 4, None]}}, "searching"),
        ({"status": 2, "media": {"status": 3, "downloadStatus": [{}]}}, "downloading"),
        ({"status": 2, "media": {"status": 3, "downloadStatus": [{"size": "big", "sizeLeft": []}]}}, "downloading"),
        ({}, "unknown"),
        ({"status": "approved", "media": {"status": "processing"}}, "unknown"),
        ({"media": {"downloadStatus": []}}, "unknown"),
    ],
)
async def test_missing_and_malformed_answers_are_never_optimistic(
    api: Api, manager: Manager, clock: Clock, answer: dict[str, Any], phase: str
) -> None:
    created = await sent_movie(api)
    manager.answers[41] = answer
    got = await read(api, created["id"])
    assert (got["state"], got["progress"]["phase"], got["progress"]["percent"]) == ("sent", phase, None)
    assert got["progress"]["eta"] is None
    if phase == "unknown":
        assert got["progress"]["last_phase"] == "searching"
        assert got["progress"]["detail"] == "The request manager's answer could not be read."


async def test_a_request_deleted_in_the_manager_fails(api: Api, manager: Manager, clock: Clock) -> None:
    created = await sent_movie(api)
    manager.answers[41] = httpx.Response(404, json={"message": "Request not found."})
    got = await read(api, created["id"])
    assert (got["state"], got["progress"]["phase"]) == ("failed", "failed")
    assert got["progress"]["detail"] == got["note"] == "The request manager no longer has this request."
    assert (await api.get("/titles/tmdb:movie:603")).json()["request_progress"] == {"phase": "failed", "percent": None}


async def test_declined_and_failed_in_the_manager(api: Api, manager: Manager, clock: Clock) -> None:
    created = await sent_movie(api)
    manager.answers[41] = body(request=4, queue=[item()])
    got = await read(api, created["id"])
    assert (got["state"], got["progress"]["phase"], got["progress"]["percent"]) == ("failed", "failed", None)
    assert got["progress"]["detail"] == "The request manager could not complete this request."
    again = (await api.post("/requests", json={"media_type": "movie", "tmdb_id": 603})).json()
    manager.answers[42] = body(request=3)
    got = await read(api, again["id"])
    assert (got["state"], got["progress"]["phase"]) == ("declined", "declined")
    assert got["progress"]["detail"] == "Declined in the request manager."


async def test_errors_are_unknown_and_back_off(api: Api, manager: Manager, clock: Clock) -> None:
    created = await sent_movie(api)
    manager.answers[41] = body(queue=[item(1000, 400)])
    assert (await read(api, created["id"]))["progress"]["phase"] == "downloading"
    answered_at = (await read(api, created["id"]))["progress"]["checked_at"]

    manager.answers[41] = httpx.Response(503, json={"message": "starting"})
    clock.tick(rp.FAST_S)
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["last_phase"], got["percent"], got["eta"]) == ("unknown", "downloading", None, None)
    assert got["detail"] == "The request manager did not answer." and got["checked_at"] == answered_at
    asked = len(manager.asked)
    # 30 s, then 60, 120, 240 and 300 from there on: nothing is asked in between.
    for wait in (30, 60, 120, 240, 300, 300):
        clock.tick(wait - 1)
        await read(api, created["id"])
        await api.c.requests.poll_due()
        assert len(manager.asked) == asked, wait
        clock.tick(1)
        await api.c.requests.poll_due()
        asked += 1
        assert len(manager.asked) == asked, wait
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["last_phase"]) == ("unknown", "downloading")

    # The first answer ends it, and the download is polled at its own pace again.
    manager.answers[41] = body(queue=[item(1000, 300)])
    clock.tick(300)
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["last_phase"], got["percent"]) == ("downloading", None, 70)
    clock.tick(rp.FAST_S)
    await api.c.requests.poll_due()
    assert len(manager.asked) == asked + 2


async def test_an_unreachable_manager_is_asked_once_for_everything(api: Api, manager: Manager, clock: Clock) -> None:
    await configure(api)
    ids = await add_sent_rows(api, 6)
    assert await api.c.requests.poll_due() == 0 and len(manager.asked) == 6
    for request_id in range(500, 506):
        manager.answers[request_id] = httpx.ReadTimeout("slow")
    clock.tick(rp.SLOW_S)
    await api.c.requests.poll_due()
    # One timeout, not six; every request says it does not know.
    assert len(manager.asked) == 7
    listed = (await api.get("/requests")).json()["requests"]
    assert {x["id"] for x in listed} == set(ids)
    assert {(x["progress"]["phase"], x["progress"]["last_phase"]) for x in listed} == {("unknown", "searching")}
    # While it is known to be down neither the job nor a reader tries again.
    clock.tick(rp.BACKOFF_FIRST_S - 1)
    await api.c.requests.poll_due()
    await api.get("/requests")
    assert len(manager.asked) == 7
    clock.tick(1)
    await api.c.requests.poll_due()
    assert len(manager.asked) == 8
    manager.answers.clear()
    clock.tick(rp.BACKOFF_MAX_S)
    await api.c.requests.poll_due()
    assert {x["progress"]["phase"] for x in (await api.get("/requests")).json()["requests"]} == {"searching"}


async def test_a_download_that_stands_still_is_stalled(api: Api, manager: Manager, clock: Clock) -> None:
    created = await sent_movie(api)
    manager.answers[41] = body(queue=[item(1000, 400)])
    assert (await read(api, created["id"]))["progress"]["phase"] == "downloading"
    clock.tick(rp.STALL_AFTER_S - 60)
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["percent"]) == ("downloading", 60)
    clock.tick(60)
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["percent"], got["detail"]) == ("stalled", None, "No progress for 30 minutes.")
    assert (await api.get("/titles/tmdb:movie:603")).json()["request_progress"] == {"phase": "stalled", "percent": None}
    # A byte moves: it is a download again, and the clock starts over.
    manager.answers[41] = body(queue=[item(1000, 399)])
    clock.tick(rp.SLOW_S)
    assert (await read(api, created["id"]))["progress"]["phase"] == "downloading"
    clock.tick(rp.STALL_AFTER_S - 60)
    assert (await read(api, created["id"]))["progress"]["phase"] == "downloading"

    # Waiting on purpose is not standing still, however long it lasts.
    manager.answers[41] = body(queue=[item(1000, 399, "paused")])
    for _ in range(4):
        clock.tick(rp.STALL_AFTER_S)
        got = (await read(api, created["id"]))["progress"]
        assert (got["phase"], got["detail"]) == ("downloading", "Paused")
    # Neither is a search.
    manager.answers[41] = body()
    for _ in range(4):
        clock.tick(rp.STALL_AFTER_S)
        assert (await read(api, created["id"]))["progress"]["phase"] == "searching"
    # A finished download that is never imported is.
    manager.answers[41] = body(queue=[item(1000, 0, "completed")])
    clock.tick(rp.SLOW_S)
    assert (await read(api, created["id"]))["progress"]["phase"] == "importing"
    clock.tick(rp.STALL_AFTER_S)
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["detail"]) == ("stalled", "Downloaded, but not imported for 30 minutes.")
    # What the download client itself reports needs no waiting.
    manager.answers[41] = body(queue=[item(1000, 400, "warning")])
    clock.tick(rp.SLOW_S)
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["detail"]) == ("stalled", "The download client reported a warning.")


async def test_series_becomes_partially_available(api: Api, manager: Manager, clock: Clock) -> None:
    await configure(api)
    created = (await api.post("/requests", json={"media_type": "tv", "tmdb_id": 1399, "seasons": [1, 2]})).json()
    seasons = [{"seasonNumber": 1, "status": 5}, {"seasonNumber": 2, "status": 3}]
    manager.answers[41] = body(media=4, seasons=seasons)
    got = await read(api, created["id"])
    assert (got["state"], got["progress"]["phase"]) == ("sent", "partially_available")
    assert got["progress"]["detail"] == "1 of 2 seasons available"
    assert (await api.get("/titles/tmdb:tv:1399")).json()["request_progress"] == {
        "phase": "partially_available",
        "percent": None,
    }
    manager.answers[41] = body(media=4, seasons=[{"seasonNumber": 1, "status": 5}, {"seasonNumber": 2, "status": 5}])
    clock.tick(rp.SLOW_S)
    got = await read(api, created["id"])
    assert (got["state"], got["progress"]["phase"]) == ("available", "available")


async def test_listing_fifty_requests_asks_the_manager_a_bounded_number_of_times(
    api: Api, manager: Manager, clock: Clock
) -> None:
    await configure(api)
    await add_sent_rows(api, 50)
    manager.delay = 0.01
    listed = (await api.get("/requests")).json()["requests"]
    assert len(listed) == 50
    assert len(manager.asked) == requests_module.READ_REFRESH_MAX == 8
    assert manager.peak <= requests_module.POLL_CONCURRENCY == 4
    assert len(set(manager.asked)) == 8
    # The same list again asks about the next few, never about one twice within its interval.
    for _ in range(10):
        await api.get("/requests")
    assert len(manager.asked) == len(set(manager.asked)) == 50
    assert manager.peak <= requests_module.POLL_CONCURRENCY
    # All fresh: reading costs nothing until an interval has passed.
    await api.get("/requests")
    clock.tick(rp.SLOW_S - 1)
    await api.get("/requests")
    assert len(manager.asked) == 50
    clock.tick(1)
    await api.get("/requests")
    assert len(manager.asked) == 58


async def test_the_job_asks_only_about_what_is_due(api: Api, manager: Manager, clock: Clock) -> None:
    await configure(api)
    ids = await add_sent_rows(api, 30)
    manager.delay = 0.01
    # One pass takes a bounded batch, a few at a time.
    assert await api.c.requests.poll_due() == 0
    assert len(manager.asked) == requests_module.POLL_BATCH_MAX == 20 and manager.peak <= 4
    await api.c.requests.poll_due()
    assert len(manager.asked) == 30
    await api.c.requests.poll_due()
    assert len(manager.asked) == 30
    # A download is asked about every FAST_S, a search every SLOW_S.
    manager.answers[500] = body(queue=[item()])
    clock.tick(rp.SLOW_S)
    await api.c.requests.poll_due()
    await api.c.requests.poll_due()
    assert len(manager.asked) == 60
    for _ in range(3):
        clock.tick(rp.FAST_S)
        await api.c.requests.poll_due()
    assert manager.asked[60:] == [500, 500, 500]
    # Requests waiting for approval, unsent or closed are never asked about.
    async with api.c.db.session() as s:
        for n, state in enumerate(("pending_approval", "approved", "available", "declined", "failed")):
            (await s.get(MediaRequest, ids[n + 1])).state = state  # type: ignore[union-attr]
        await s.commit()
    clock.tick(rp.SLOW_S)
    await api.c.requests.poll_due()
    await api.c.requests.poll_due()
    assert len(manager.asked) == 63 + 25 and not {501, 502, 503, 504, 505} & set(manager.asked[63:])


async def test_a_request_just_sent_is_asked_about_sooner(api: Api, manager: Manager, clock: Clock) -> None:
    created = await sent_movie(api)
    await api.c.requests.poll_due()
    assert manager.asked == [41]
    # Searching, but new: every FRESH_S for the first FRESH_FOR_S, so a download is noticed as it starts.
    clock.tick(rp.FRESH_S - 1)
    await api.c.requests.poll_due()
    assert manager.asked == [41]
    clock.tick(1)
    manager.answers[41] = body(queue=[item(1000, 990)])
    await api.c.requests.poll_due()
    assert manager.asked == [41, 41]
    assert (await read(api, created["id"]))["progress"]["percent"] == 1
    # Still nothing after ten minutes: from then on it is asked about at the slow pace.
    manager.answers[41] = body()
    clock.tick(rp.FRESH_FOR_S)
    await api.c.requests.poll_due()
    asked = len(manager.asked)
    clock.tick(rp.SLOW_S - 1)
    await api.c.requests.poll_due()
    assert len(manager.asked) == asked
    clock.tick(1)
    await api.c.requests.poll_due()
    assert len(manager.asked) == asked + 1


async def test_reading_and_refreshing_one_request(api: Api, manager: Manager, clock: Clock) -> None:
    await configure(api)
    owner = await viewer(api, "Requester")
    other = await viewer(api, "Somebody else")
    created = (
        await api.client.post("/api/v1/requests", headers=owner, json={"media_type": "movie", "tmdb_id": 603})
    ).json()
    path = f"/api/v1/requests/{created['id']}"
    assert (await api.client.get(path)).status_code == 401
    assert (await api.client.get(path, headers=other)).status_code == 403
    assert (await api.client.post(path + "/refresh", headers=other)).status_code == 403
    assert (await api.client.get(path, headers=owner)).json()["progress"]["phase"] == "requested"
    assert (await api.get("/requests/missing")).status_code == 404
    assert (await api.post("/requests/missing/refresh")).status_code == 404
    # Nothing to ask the manager about yet.
    assert (await api.client.post(path + "/refresh", headers=owner)).json()["progress"]["phase"] == "requested"
    assert manager.asked == []

    assert (await api.post(f"/requests/{created['id']}/approve")).json()["progress"]["phase"] == "searching"
    manager.answers[41] = body(queue=[item(1000, 900)])
    assert (await api.client.get(path, headers=owner)).json()["progress"]["percent"] == 10
    # Within the interval a read serves what is stored; "Check again" asks now.
    manager.answers[41] = body(queue=[item(1000, 500)])
    clock.tick(6)
    assert (await api.client.get(path, headers=owner)).json()["progress"]["percent"] == 10
    refreshed = await api.client.post(path + "/refresh", headers=owner)
    assert refreshed.status_code == 200 and refreshed.json()["progress"]["percent"] == 50
    # ...but not more often than every few seconds, whoever presses it.
    manager.answers[41] = body(queue=[item(1000, 100)])
    asked = len(manager.asked)
    clock.tick(requests_module.MANUAL_REFRESH_MIN_S - 1)
    assert (await api.post(f"/requests/{created['id']}/refresh")).json()["progress"]["percent"] == 50
    assert len(manager.asked) == asked
    clock.tick(1)
    assert (await api.post(f"/requests/{created['id']}/refresh")).json()["progress"]["percent"] == 90
    assert MANAGER_KEY not in refreshed.text and MANAGER not in refreshed.text


async def test_a_request_the_manager_gave_no_id_for_is_followed_by_title(
    api: Api, manager: Manager, clock: Clock
) -> None:
    await configure(api)
    manager.create.mock(return_value=httpx.Response(409, json={"message": "Request for this media already exists."}))
    created = (await api.post("/requests", json={"media_type": "movie", "tmdb_id": 603})).json()
    assert (created["state"], created["progress"]["phase"]) == ("sent", "searching")
    title = manager.router.get(f"{MANAGER}/api/v1/movie/603")
    title.mock(return_value=httpx.Response(200, json={"id": 603}))
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["last_phase"]) == ("unknown", "searching")
    title.mock(return_value=httpx.Response(200, json={"mediaInfo": {"status": 3, "downloadStatus": [item(10, 5)]}}))
    clock.tick(rp.SLOW_S)
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["percent"]) == ("downloading", 50)
    title.mock(return_value=httpx.Response(200, json={"mediaInfo": {"status": 5}}))
    clock.tick(rp.FAST_S)
    assert (await read(api, created["id"]))["state"] == "available"
    assert manager.asked == []


async def test_removing_the_manager_leaves_open_requests_unknown(api: Api, manager: Manager, clock: Clock) -> None:
    created = await sent_movie(api)
    manager.answers[41] = body(queue=[item()])
    assert (await read(api, created["id"]))["progress"]["phase"] == "downloading"
    await api.put("/settings/request_manager", json={"url": None})
    clock.tick(rp.FAST_S)
    got = (await read(api, created["id"]))["progress"]
    assert (got["phase"], got["last_phase"]) == ("unknown", "downloading")
    assert len(manager.asked) == 1


async def test_the_sweep_closes_library_arrivals_and_follows_the_rest(api: Api, manager: Manager, clock: Clock) -> None:
    created = await sent_movie(api)
    show = (await api.post("/requests", json={"media_type": "tv", "tmdb_id": 1399})).json()
    await read(api, created["id"])
    await read(api, show["id"])
    assert manager.asked == [41, 42]
    # The sweep asks about every sent request, due or not.
    manager.answers[42] = body(queue=[item()])
    assert await api.c.requests.reconcile() == 0
    assert manager.asked == [41, 42, 41, 42]
    assert (await read(api, show["id"]))["progress"]["phase"] == "downloading"
    await add_library_movie(api)
    assert await api.c.requests.reconcile() == 1
    got = await read(api, created["id"])
    assert (got["state"], got["progress"]["phase"]) == ("available", "available")
    assert manager.asked.count(41) == 2


async def test_the_scheduler_polls_progress_at_the_fast_interval(api: Api) -> None:
    assert rp.FAST_S == 15

    async def nothing() -> int:
        return 0

    scheduler = SyncScheduler(api.c.sync, nothing, poll_requests=nothing)
    scheduler.start()
    try:
        job = scheduler._sched.get_job("request-progress")
        assert job is not None and job.trigger.interval.total_seconds() == rp.FAST_S
        assert scheduler._sched.get_job("requests").trigger.interval.total_seconds() == 300
    finally:
        scheduler.shutdown()
    assert api.c.scheduler._poll_requests == api.c.requests.poll_due
