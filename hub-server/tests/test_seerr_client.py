from __future__ import annotations

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


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"status": 1, "media": {"status": 2}}, "pending"),
        ({"status": 2, "media": {"status": 3}}, "approved"),
        ({"status": 2, "media": {"status": 4}}, "approved"),
        ({"status": 2, "media": {"status": 5}}, "available"),
        ({"status": 5, "media": {"status": 3}}, "available"),
        ({"status": 3, "media": {"status": 5}}, "declined"),
        ({"status": 4, "media": {"status": 3}}, "failed"),
        ({"status": 9}, "unknown"),
        ({"status": "2", "media": None}, "unknown"),
    ],
)
@respx.mock
async def test_request_status_mapping(client: SeerrClient, body: dict, expected: str) -> None:
    respx.get(f"{BASE}/api/v1/request/41").mock(return_value=httpx.Response(200, json=body))
    assert await client.request_status(41) == expected


@respx.mock
async def test_request_status_errors(client: SeerrClient) -> None:
    route = respx.get(f"{BASE}/api/v1/request/41")
    route.mock(return_value=httpx.Response(404, json={"message": "Request not found."}))
    with pytest.raises(SeerrError) as err:
        await client.request_status(41)
    assert err.value.status == 404
    route.mock(return_value=httpx.Response(200, text="{broken"))
    with pytest.raises(SeerrError):
        await client.request_status(41)


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
            await SeerrClient(http, BASE, KEY).request_status(41)
    assert err.value.status == 302 and not elsewhere.called
