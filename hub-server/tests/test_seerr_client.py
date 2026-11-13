from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.adapters.seerr import SeerrClient, SeerrError

BASE = "http://requests.example.test:5055"
KEY = "fixture-manager-key"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=5.0) as http:
        yield SeerrClient(http, BASE + "/", KEY)


@respx.mock
async def test_test_reports_version_and_checks_the_key(client: SeerrClient) -> None:
    status = respx.get(f"{BASE}/api/v1/status").mock(return_value=httpx.Response(200, json={"version": "2.7.3"}))
    me = respx.get(f"{BASE}/api/v1/auth/me").mock(return_value=httpx.Response(200, json={"id": 1}))
    assert await client.test() == (True, "Connected to request manager 2.7.3.")
    assert status.calls.last.request.headers["X-Api-Key"] == KEY
    assert me.calls.last.request.headers["X-Api-Key"] == KEY


@respx.mock
async def test_test_rejected_key_unreachable_and_malformed(client: SeerrClient) -> None:
    respx.get(f"{BASE}/api/v1/status").mock(return_value=httpx.Response(200, json={"version": "2.7.3"}))
    respx.get(f"{BASE}/api/v1/auth/me").mock(return_value=httpx.Response(401, json={"message": "nope"}))
    ok, message = await client.test()
    assert not ok and "API key" in message and KEY not in message

    respx.get(f"{BASE}/api/v1/status").mock(side_effect=httpx.ConnectError("refused"))
    ok, message = await client.test()
    assert not ok and "reach" in message

    respx.get(f"{BASE}/api/v1/status").mock(return_value=httpx.Response(200, text="<html>login</html>"))
    ok, message = await client.test()
    assert not ok and "request manager" in message


@respx.mock
async def test_create_movie_and_tv_requests(client: SeerrClient) -> None:
    route = respx.post(f"{BASE}/api/v1/request").mock(return_value=httpx.Response(201, json={"id": 41, "status": 2}))
    assert await client.create_request("movie", 603) == 41
    sent = route.calls.last.request
    assert sent.headers["X-Api-Key"] == KEY
    assert httpx.Response(200, content=sent.content).json() == {"mediaType": "movie", "mediaId": 603}

    assert await client.create_request("tv", 1399, [2, 1]) == 41
    assert httpx.Response(200, content=route.calls.last.request.content).json() == {
        "mediaType": "tv",
        "mediaId": 1399,
        "seasons": [2, 1],
    }
    # A series request without a season pick asks for the whole show.
    await client.create_request("tv", 1399)
    assert httpx.Response(200, content=route.calls.last.request.content).json()["seasons"] == "all"


@respx.mock
async def test_create_unauthorized_raises_without_leaking_the_key(client: SeerrClient) -> None:
    respx.post(f"{BASE}/api/v1/request").mock(return_value=httpx.Response(401, json={"message": "Unauthorized"}))
    with pytest.raises(SeerrError) as err:
        await client.create_request("movie", 603)
    assert err.value.status == 401 and KEY not in str(err.value)


@respx.mock
async def test_create_conflict_returns_existing_id_when_present(client: SeerrClient) -> None:
    route = respx.post(f"{BASE}/api/v1/request")
    route.mock(return_value=httpx.Response(409, json={"message": "Request already exists.", "id": 17}))
    assert await client.create_request("movie", 603) == 17
    route.mock(return_value=httpx.Response(409, json={"message": "exists", "request": {"id": 18}}))
    assert await client.create_request("movie", 603) == 18
    route.mock(return_value=httpx.Response(409, json={"message": "Request for this media already exists."}))
    with pytest.raises(SeerrError) as err:
        await client.create_request("movie", 603)
    assert err.value.status == 409 and "already exists" in err.value.message


@respx.mock
async def test_create_malformed_json_and_missing_id(client: SeerrClient) -> None:
    route = respx.post(f"{BASE}/api/v1/request")
    route.mock(return_value=httpx.Response(201, text="not json"))
    with pytest.raises(SeerrError) as err:
        await client.create_request("movie", 603)
    assert err.value.status == 201
    route.mock(return_value=httpx.Response(201, json={"status": 2}))
    with pytest.raises(SeerrError):
        await client.create_request("movie", 603)
    route.mock(return_value=httpx.Response(201, json=[1, 2]))
    with pytest.raises(SeerrError):
        await client.create_request("movie", 603)


@respx.mock
async def test_transport_errors_become_seerr_errors(client: SeerrClient) -> None:
    respx.post(f"{BASE}/api/v1/request").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(SeerrError) as err:
        await client.create_request("movie", 603)
    assert err.value.status is None


@respx.mock
async def test_snapshot_reads_request_media_and_queue(client: SeerrClient) -> None:
    body = {
        "id": 41,
        "status": 2,
        "is4k": False,
        "seasons": [{"seasonNumber": 2, "status": 2}, {"seasonNumber": 1, "status": 2}],
        "media": {
            "status": 3,
            "status4k": 1,
            "seasons": [{"seasonNumber": 1, "status": 5}, {"seasonNumber": 2, "status": 3}],
            "downloadStatus": [
                {
                    "title": "fixture item one",
                    "size": 4000,
                    "sizeLeft": 1000,
                    "status": "Downloading",
                    "timeLeft": "00:12:30",
                    "estimatedCompletionTime": "2030-01-02T03:04:05.000Z",
                    "episode": {"seasonNumber": 2, "episodeNumber": 3},
                },
                {"size": 2000.0, "sizeLeft": 2000.0, "status": "queued", "timeLeft": "1.02:00:00"},
            ],
            "downloadStatus4k": [{"size": 9, "sizeLeft": 9, "status": "paused"}],
        },
    }
    respx.get(f"{BASE}/api/v1/request/41").mock(return_value=httpx.Response(200, json=body))
    snap = await client.request_snapshot(41)
    assert (snap.request_status, snap.media_status, snap.tracked) == (2, 3, True)
    assert snap.seasons_requested == (1, 2) and snap.season_status == {1: 5, 2: 3}
    first, second = snap.queue
    assert (first.status, first.size, first.size_left, first.time_left_s) == ("downloading", 4000, 1000, 750)
    assert first.eta == datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC) and (first.season, first.episode) == (2, 3)
    assert (second.status, second.size, second.size_left, second.eta) == ("queued", 2000, 2000, None)
    assert second.time_left_s == 26 * 3600 and (second.season, second.episode) == (None, None)
    # Nothing of the queue item's name is kept: it can carry a release name.
    assert "fixture item one" not in repr(snap)


@respx.mock
async def test_snapshot_of_a_4k_request_reads_the_4k_fields(client: SeerrClient) -> None:
    body = {
        "status": 2,
        "is4k": True,
        "media": {
            "status": 1,
            "status4k": 3,
            "seasons": [{"seasonNumber": 1, "status": 1, "status4k": 5}],
            "downloadStatus": [],
            "downloadStatus4k": [{"size": 10, "sizeLeft": 5, "status": "downloading"}],
        },
    }
    respx.get(f"{BASE}/api/v1/request/41").mock(return_value=httpx.Response(200, json=body))
    snap = await client.request_snapshot(41)
    assert snap.media_status == 3 and len(snap.queue) == 1 and snap.season_status == {1: 5}


@pytest.mark.parametrize(
    "body",
    [
        {"status": "2", "media": None},
        {"status": True, "media": {"status": "5", "downloadStatus": "none"}},
        {"media": {"status": 3.5, "seasons": {"1": 5}, "downloadStatus": {"size": 1}}},
        {"status": None, "seasons": "all", "media": []},
    ],
)
@respx.mock
async def test_snapshot_drops_malformed_pieces(client: SeerrClient, body: dict) -> None:
    respx.get(f"{BASE}/api/v1/request/41").mock(return_value=httpx.Response(200, json=body))
    snap = await client.request_snapshot(41)
    assert (snap.request_status, snap.media_status, snap.queue) == (None, None, ())
    assert snap.seasons_requested == () and snap.season_status == {}


@respx.mock
async def test_snapshot_keeps_the_readable_queue_items(client: SeerrClient) -> None:
    queue = [
        "broken",
        None,
        {"size": "4000", "sizeLeft": -1, "status": 7, "timeLeft": "soon", "estimatedCompletionTime": "tomorrow"},
        {
            "size": 100,
            "sizeLeft": 400,
            "status": "downloading",
            "episode": {"seasonNumber": "1", "episodeNumber": True},
        },
        {"size": float("1e12"), "sizeLeft": 0, "status": "completed", "estimatedCompletionTime": 12},
    ]
    body = {"status": 2, "media": {"status": 3, "downloadStatus": queue}}
    respx.get(f"{BASE}/api/v1/request/41").mock(return_value=httpx.Response(200, json=body))
    snap = await client.request_snapshot(41)
    # Every object counts as a queue item; what cannot be read about it is None.
    assert [(i.status, i.size, i.size_left) for i in snap.queue] == [
        ("", None, None),
        ("downloading", 100, None),
        ("completed", 10**12, 0),
    ]
    assert all(i.eta is None and i.time_left_s is None and i.season is None for i in snap.queue)


@respx.mock
async def test_title_snapshot_reads_media_info(client: SeerrClient) -> None:
    movie = respx.get(f"{BASE}/api/v1/movie/603")
    movie.mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 603,
                "mediaInfo": {"status": 3, "downloadStatus": [{"size": 8, "sizeLeft": 2, "status": "downloading"}]},
            },
        )
    )
    snap = await client.title_snapshot("movie", 603)
    assert (snap.request_status, snap.media_status, snap.tracked) == (None, 3, True)
    assert [(i.size, i.size_left) for i in snap.queue] == [(8, 2)]
    # A title the manager does not track has no mediaInfo at all.
    respx.get(f"{BASE}/api/v1/tv/1399").mock(return_value=httpx.Response(200, json={"id": 1399}))
    snap = await client.title_snapshot("tv", 1399)
    assert (snap.media_status, snap.queue, snap.tracked) == (None, (), False)
    # The better copy is the one that says where the title is.
    movie.mock(return_value=httpx.Response(200, json={"mediaInfo": {"status": 1, "status4k": 5}}))
    assert (await client.title_snapshot("movie", 603)).media_status == 5


@respx.mock
async def test_snapshot_errors(client: SeerrClient) -> None:
    route = respx.get(f"{BASE}/api/v1/request/41")
    route.mock(return_value=httpx.Response(404, json={"message": "Request not found."}))
    with pytest.raises(SeerrError) as err:
        await client.request_snapshot(41)
    assert err.value.status == 404
    route.mock(return_value=httpx.Response(200, text="{broken"))
    with pytest.raises(SeerrError):
        await client.request_snapshot(41)
    route.mock(return_value=httpx.Response(200, json=[1]))
    with pytest.raises(SeerrError):
        await client.request_snapshot(41)
    route.mock(return_value=httpx.Response(503, text="busy"))
    with pytest.raises(SeerrError) as err:
        await client.request_snapshot(41)
    assert err.value.status == 503
    route.mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(SeerrError) as err:
        await client.request_snapshot(41)
    assert err.value.status is None and KEY not in err.value.message and BASE not in err.value.message


@respx.mock
async def test_redirects_are_not_followed_with_the_key() -> None:
    elsewhere = respx.get("http://elsewhere.example.test/api/v1/request/41").mock(
        return_value=httpx.Response(200, json={"status": 2})
    )
    respx.get(f"{BASE}/api/v1/request/41").mock(
        return_value=httpx.Response(302, headers={"Location": "http://elsewhere.example.test/api/v1/request/41"})
    )
    async with httpx.AsyncClient(follow_redirects=True) as http:
        with pytest.raises(SeerrError) as err:
            await SeerrClient(http, BASE, KEY).request_snapshot(41)
    assert err.value.status == 302 and not elsewhere.called
