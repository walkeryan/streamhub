from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.infra.models import Setting, Source, Title
from app.services.sync import SyncScheduler
from tests.conftest import FIXTURES, Api, add_m3u

TMDB = "https://api.themoviedb.org/3"
MANAGER = "http://requests.example.test:5055"
MANAGER_KEY = "fixture-manager-key-0123456789"
MOVIE = {"id": 603, "title": "The Matrix", "release_date": "1999-03-30", "poster_path": "/p.jpg"}
SHOW = {
    "id": 1399,
    "name": "Game of Thrones",
    "first_air_date": "2011-04-17",
    "poster_path": "/g.jpg",
    "seasons": [{"season_number": 0}, {"season_number": 1}, {"season_number": 2}],
}


@pytest.fixture
def upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{TMDB}/movie/603").mock(return_value=httpx.Response(200, json=MOVIE))
        router.get(f"{TMDB}/tv/1399").mock(return_value=httpx.Response(200, json=SHOW))
        router.get(f"{TMDB}/movie/603/watch/providers").mock(return_value=httpx.Response(200, json={"results": {}}))
        router.get(f"{TMDB}/tv/1399/watch/providers").mock(return_value=httpx.Response(200, json={"results": {}}))
        router.get(f"{TMDB}/search/multi").mock(
            return_value=httpx.Response(200, json={"results": [{**MOVIE, "media_type": "movie"}]})
        )
        router.get(f"{MANAGER}/api/v1/status").mock(return_value=httpx.Response(200, json={"version": "2.7.3"}))
        router.get(f"{MANAGER}/api/v1/auth/me").mock(return_value=httpx.Response(200, json={"id": 1}))
        router.post(f"{MANAGER}/api/v1/request").mock(return_value=httpx.Response(201, json={"id": 41, "status": 2}))
        router.get(f"{MANAGER}/api/v1/request/41").mock(
            return_value=httpx.Response(200, json={"id": 41, "status": 2, "media": {"status": 3}})
        )
        yield router


async def configure(api: Api) -> None:
    await api.put("/settings/tmdb_key", json={"api_key": "k" * 32})
    r = await api.put("/settings/request_manager", json={"url": MANAGER, "api_key": MANAGER_KEY})
    assert r.status_code == 200, r.text


async def viewer(api: Api, name: str = "Viewer") -> dict[str, str]:
    # A Friend may request but not auto-approve or approve, which is what these tests mean by a viewer.
    user = (await api.post("/users", json={"name": name, "role_id": "friend", "pin": "1234"})).json()
    r = await api.client.post("/api/v1/auth/login", json={"profile_id": user["profile_id"], "pin": "1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


async def add_library_movie(api: Api) -> None:
    async with api.c.db.session() as s:
        s.add(Source(id="lib1", type="plex", name="Library", config_enc=api.c.crypto.encrypt_json({})))
        await s.flush()
        s.add(Title(source_id="lib1", ext_id="1", kind="movie", title="The Matrix", norm_title="matrix", tmdb_id=603))
        await s.commit()


async def test_request_manager_settings_are_admin_only_and_never_return_the_key(
    api: Api, upstream: respx.MockRouter
) -> None:
    assert (await api.get("/settings")).json()["request_manager_configured"] is False
    assert (await api.get("/settings/request_manager")).json() == {"url": None, "api_key_set": False}
    r = await api.post("/settings/request_manager/test")
    assert r.json()["ok"] is False and "not configured" in r.json()["message"]

    assert (await api.put("/settings/request_manager", json={"url": "ftp://example.test"})).status_code == 400
    assert (
        await api.put("/settings/request_manager", json={"url": "http://user:pw@requests.example.test"})
    ).status_code == 400
    r = await api.put("/settings/request_manager", json={"url": MANAGER + "/api/v1/", "api_key": MANAGER_KEY})
    assert r.json() == {"url": MANAGER, "api_key_set": True}
    assert MANAGER_KEY not in r.text
    for path in ("/settings", "/settings/request_manager"):
        assert MANAGER_KEY not in (await api.get(path)).text
    assert (await api.get("/settings")).json()["request_manager_configured"] is True
    async with api.c.db.session() as s:
        stored = {row.key: row.value for row in (await s.execute(select(Setting))).scalars()}
    assert MANAGER_KEY not in repr(stored) and stored["request_manager_url"] == MANAGER

    tested = await api.post("/settings/request_manager/test")
    assert tested.json() == {"ok": True, "message": "Connected to request manager 2.7.3."}
    assert upstream.calls.last.request.headers["X-Api-Key"] == MANAGER_KEY

    # Omitting the key keeps it for the same server, but it never follows the URL to another one.
    r = await api.put("/settings/request_manager", json={"url": MANAGER + "/"})
    assert r.json() == {"url": MANAGER, "api_key_set": True}
    r = await api.put("/settings/request_manager", json={"url": "http://other.example.test:5055"})
    assert r.json() == {"url": "http://other.example.test:5055", "api_key_set": False}
    assert (await api.get("/settings")).json()["request_manager_configured"] is False
    await configure(api)
    r = await api.put("/settings/request_manager", json={"url": None})
    assert r.json() == {"url": None, "api_key_set": False}

    headers = await viewer(api)
    for method, path in (
        ("GET", "/settings/request_manager"),
        ("PUT", "/settings/request_manager"),
        ("POST", "/settings/request_manager/test"),
    ):
        r = await api.client.request(method, "/api/v1" + path, headers=headers, json={"url": MANAGER})
        assert r.status_code == 403, (method, path)


async def test_saved_configuration_survives_a_restart(api: Api, upstream: respx.MockRouter) -> None:
    await configure(api)
    await api.c.app_settings.load()
    assert api.c.app_settings.request_manager() == (MANAGER, MANAGER_KEY)


async def test_request_flow_approval_and_notice(api: Api, upstream: respx.MockRouter) -> None:
    await configure(api)
    headers = await viewer(api)
    detail = (await api.client.get("/api/v1/titles/tmdb:movie:603", headers=headers)).json()
    assert detail["requestable"] is True and detail["request_state"] is None

    r = await api.client.post("/api/v1/requests", headers=headers, json={"media_type": "movie", "tmdb_id": 603})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["state"] == "pending_approval" and created["title"] == "The Matrix" and created["year"] == 1999
    assert created["title_id"] == "tmdb:movie:603" and created["profile_name"] == "Viewer"
    assert "manager_request_id" not in created
    again = await api.client.post("/api/v1/requests", headers=headers, json={"media_type": "movie", "tmdb_id": 603})
    assert again.status_code == 200 and again.json()["id"] == created["id"]

    detail = (await api.client.get("/api/v1/titles/tmdb:movie:603", headers=headers)).json()
    assert detail["request_state"] == "pending_approval"
    mine = (await api.client.get("/api/v1/requests", headers=headers)).json()
    assert [x["id"] for x in mine["requests"]] == [created["id"]] and mine["can_approve"] is False
    assert (await api.get("/requests")).json() == {"requests": [], "can_approve": True}
    assert (await api.client.get("/api/v1/requests?scope=all", headers=headers)).status_code == 403
    assert (
        await api.client.post(f"/api/v1/requests/{created['id']}/approve", headers=headers, json={})
    ).status_code == 403
    pending = (await api.get("/requests", params={"scope": "all", "state": "pending_approval"})).json()["requests"]
    assert [x["id"] for x in pending] == [created["id"]]
    assert (await api.get("/requests", params={"state": "bogus"})).status_code == 400

    approved = await api.post(f"/requests/{created['id']}/approve")
    assert approved.status_code == 200 and approved.json()["state"] == "sent"
    sent = upstream.calls.last.request
    assert sent.headers["X-Api-Key"] == MANAGER_KEY and b'"mediaId":603' in sent.content.replace(b" ", b"")
    assert (await api.post(f"/requests/{created['id']}/decline", json={"note": "late"})).status_code == 409
    assert (await api.post("/requests/missing/approve")).status_code == 404

    assert await api.c.requests.reconcile() == 0
    assert (await api.client.get("/api/v1/notices", headers=headers)).json() == {"notices": []}
    await add_library_movie(api)
    assert await api.c.requests.reconcile() == 1
    notices = (await api.client.get("/api/v1/notices", headers=headers)).json()["notices"]
    assert [(n["kind"], n["ref_id"], n["read_at"]) for n in notices] == [("request_available", "tmdb:movie:603", None)]
    assert notices[0]["title"] == "The Matrix is now available"
    assert (await api.get("/notices")).json() == {"notices": []}
    assert (await api.post(f"/notices/{notices[0]['id']}/read")).status_code == 404
    read = await api.client.post(f"/api/v1/notices/{notices[0]['id']}/read", headers=headers)
    assert read.status_code == 200 and read.json()["read_at"]
    detail = (await api.client.get("/api/v1/titles/tmdb:movie:603", headers=headers)).json()
    assert detail["request_state"] == "available" and detail["requestable"] is False
    r = await api.client.post("/api/v1/requests", headers=headers, json={"media_type": "movie", "tmdb_id": 603})
    assert r.status_code == 409 and r.json()["code"] == "already_available"


async def test_decline_and_unread_first_ordering(api: Api, upstream: respx.MockRouter) -> None:
    await configure(api)
    headers = await viewer(api)
    first = (
        await api.client.post("/api/v1/requests", headers=headers, json={"media_type": "movie", "tmdb_id": 603})
    ).json()
    declined = await api.post(f"/requests/{first['id']}/decline", json={"note": "Already on a service"})
    assert declined.json()["state"] == "declined" and declined.json()["note"] == "Already on a service"
    second = (
        await api.client.post(
            "/api/v1/requests", headers=headers, json={"media_type": "tv", "tmdb_id": 1399, "seasons": [2]}
        )
    ).json()
    assert second["seasons"] == [2]
    await api.post(f"/requests/{second['id']}/decline")
    notices = (await api.client.get("/api/v1/notices", headers=headers)).json()["notices"]
    assert [n["kind"] for n in notices] == ["request_declined", "request_declined"]
    older = next(n for n in notices if "Matrix" in n["title"])
    assert older["body"] == "Already on a service"
    newer = next(n for n in notices if n["id"] != older["id"])
    await api.client.post(f"/api/v1/notices/{newer['id']}/read", headers=headers)
    notices = (await api.client.get("/api/v1/notices", headers=headers)).json()["notices"]
    assert [n["id"] for n in notices] == [older["id"], newer["id"]]
    detail = (await api.client.get("/api/v1/titles/tmdb:movie:603", headers=headers)).json()
    assert detail["request_state"] == "declined" and detail["requestable"] is True


async def test_admin_request_is_sent_at_once_and_series_expose_seasons(api: Api, upstream: respx.MockRouter) -> None:
    await configure(api)
    detail = (await api.get("/titles/tmdb:tv:1399")).json()
    assert detail["requestable"] is True and detail["request_seasons"] == [1, 2]
    r = await api.post("/requests", json={"media_type": "tv", "tmdb_id": 1399, "seasons": [1]})
    assert r.status_code == 201 and r.json()["state"] == "sent" and r.json()["seasons"] == [1]
    body = httpx.Response(200, content=upstream.calls.last.request.content).json()
    assert body == {"mediaType": "tv", "mediaId": 1399, "seasons": [1]}
    assert (await api.post("/requests", json={"media_type": "tv", "tmdb_id": 1399, "seasons": [-3]})).status_code == 400
    assert (await api.post("/requests", json={"media_type": "song", "tmdb_id": 1})).status_code == 422


async def test_manager_failure_is_reported_without_the_key(api: Api, upstream: respx.MockRouter) -> None:
    await configure(api)
    upstream.post(f"{MANAGER}/api/v1/request").mock(return_value=httpx.Response(401, json={"message": MANAGER_KEY}))
    r = await api.post("/requests", json={"media_type": "movie", "tmdb_id": 603})
    assert r.status_code == 201 and r.json()["state"] == "approved"
    assert "API key" in r.json()["note"] and MANAGER_KEY not in r.text


async def test_tiles_are_requestable_only_when_it_can_work(api: Api, upstream: respx.MockRouter) -> None:
    await api.put("/settings/tmdb_key", json={"api_key": "k" * 32})
    result = (await api.get("/search", params={"q": "matrix"})).json()["results"]
    assert [(t["id"], t["requestable"], t["request_state"]) for t in result] == [("tmdb:movie:603", False, None)]
    r = await api.post("/requests", json={"media_type": "movie", "tmdb_id": 603})
    assert r.status_code == 409 and r.json()["code"] == "request_manager_unconfigured"

    await configure(api)
    result = (await api.get("/search", params={"q": "matrix"})).json()["results"]
    assert [(t["id"], t["requestable"]) for t in result] == [("tmdb:movie:603", True)]
    await add_library_movie(api)
    result = (await api.get("/search", params={"q": "matrix"})).json()["results"]
    assert [(t["id"], t["requestable"]) for t in result] == [("tmdb:movie:603", False)]


async def test_requests_need_a_session(api: Api) -> None:
    for method, path in (("GET", "/requests"), ("POST", "/requests"), ("GET", "/notices")):
        assert (await api.client.request(method, "/api/v1" + path)).status_code == 401


async def test_reconcile_runs_after_a_source_sync_and_on_the_scheduler(api: Api) -> None:
    calls: list[int] = []

    async def reconcile() -> int:
        calls.append(1)
        return 0

    assert api.c.sync.after_catalog_sync == api.c.requests.reconcile
    api.c.sync.after_catalog_sync = reconcile
    await add_m3u(api, FIXTURES / "sample.m3u")
    assert calls == [1]

    async def broken() -> int:
        raise RuntimeError("boom")

    api.c.sync.after_catalog_sync = broken
    source: dict[str, Any] = (await api.get("/sources")).json()[0]
    assert await api.c.sync.sync_source(source["id"]) is True

    scheduler = SyncScheduler(api.c.sync, reconcile)
    scheduler.start()
    try:
        job = scheduler._sched.get_job("requests")
        assert job is not None and job.trigger.interval.total_seconds() == 300
    finally:
        scheduler.shutdown()
