"""End-to-end proxy playback through the *real* outbound client factory.

The shared ``http_factory`` fixture drops ``timeout``, which is how UAT P0-1 (an ``httpx.Timeout``
re-wrapped by ``make_client`` -> AssertionError -> HTTP 500 on every proxied segment) slipped through.
These tests build the container with ``make_client`` so the timeout handling is exercised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.container import Container, build_container
from app.infra.cache import MemoryCache
from app.infra.http import make_client
from app.infra.settings import AppSettings
from app.main import create_app
from tests.conftest import FIXTURES, Api, _TestTransport, add_m3u

TS = b"\x47" + b"\x00" * 187  # one MPEG-TS packet


def _real_factory(**kw: Any) -> httpx.AsyncClient:
    # make_client with its own timeout handling; only the transport is swapped so respx can hook in
    return make_client(transport=_TestTransport(), **kw)


@pytest.fixture
def real_container(settings: AppSettings) -> Container:
    return build_container(settings, http_factory=_real_factory, cache=MemoryCache())


@pytest.fixture
async def real_api(settings: AppSettings, real_container: Container) -> AsyncIterator[Api]:
    app = create_app(settings, container=real_container, start_background=False)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://hub.test") as client,
    ):
        a = Api(client, real_container)
        await a.login_admin()
        yield a


def test_make_client_accepts_number_none_and_timeout() -> None:
    assert make_client(5.0).timeout == httpx.Timeout(5.0, connect=10.0)
    assert make_client(None).timeout == httpx.Timeout(None, connect=10.0)
    t = httpx.Timeout(30.0, connect=10.0, read=None)
    assert make_client(t).timeout == t


async def _channel(api: Api, name: str) -> dict[str, Any]:
    r = await api.get("/channels", params={"limit": 500})
    chs: list[dict[str, Any]] = r.json()["channels"]
    return next(c for c in chs if c["name"] == name)


async def _resolve(api: Api, ch_id: str) -> str:
    d = (await api.post("/playback/resolve", json={"id": ch_id, "client": "web"})).json()
    assert d["mode"] == "proxy", d
    url: str = d["url"]
    assert "/api/v1/stream/" in url
    return url.split("hub.test", 1)[1] if "hub.test" in url else url


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_proxy_always_hls_playlist_and_segments(real_api: Api, respx_mock: respx.MockRouter) -> None:
    api = real_api
    respx_mock.route(host="hub.test").pass_through()
    await add_m3u(api, FIXTURES / "sample.m3u")
    await api.patch("/settings", json={"proxy_mode": "always"})
    bbc = await _channel(api, "BBC One HD")

    respx_mock.get("http://streams.example/live/bbc1.m3u8").mock(
        return_value=httpx.Response(
            200, text="#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nseg1.ts\n#EXTINF:2,\nseg2.ts\n"
        )
    )
    seen: list[httpx.Request] = []

    def seg(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "range" in request.headers:
            return httpx.Response(
                206, content=TS[:10], headers={"content-type": "video/mp2t", "content-range": "bytes 0-9/188"}
            )
        return httpx.Response(200, content=TS * 4, headers={"content-type": "video/mp2t"})

    respx_mock.get(url__regex=r"http://streams\.example/live/seg\d\.ts").mock(side_effect=seg)

    r = await api.client.get(await _resolve(api, bbc["id"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    segs = [ln for ln in r.text.splitlines() if ln and not ln.startswith("#")]
    assert len(segs) == 2 and all("/seg/" in s and "streams.example" not in s for s in segs)

    for s in segs:
        r = await api.client.get(s)
        assert r.status_code == 200, r.text
        assert r.content == TS * 4 and r.headers["content-type"] == "video/mp2t"

    r = await api.client.get(segs[0], headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == TS[:10]
    assert r.headers["content-range"] == "bytes 0-9/188"
    assert seen[-1].headers["range"] == "bytes=0-9"


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_proxy_raw_ts_passthrough_with_channel_headers(real_api: Api, respx_mock: respx.MockRouter) -> None:
    """TF1 carries #EXTVLCOPT UA/Referer, so web resolves it to the proxy; headers must reach upstream."""
    api = real_api
    respx_mock.route(host="hub.test").pass_through()
    await add_m3u(api, FIXTURES / "sample.m3u")
    await api.patch("/settings", json={"proxy_mode": "always"})
    tf1 = await _channel(api, "TF1")
    calls: list[httpx.Request] = []

    def live(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # first connection delivers data; the reconnect gets 404 so the shared upstream ends
        # (ASGITransport buffers the whole body, so an endless live stream can't be read here)
        if len(calls) == 1:
            return httpx.Response(200, content=TS * 50, headers={"content-type": "video/mp2t"})
        return httpx.Response(404)

    respx_mock.get("http://streams.example/live/tf1.ts").mock(side_effect=live)
    r = await api.client.get(await _resolve(api, tf1["id"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "video/mp2t"
    assert r.content == TS * 50
    assert calls[0].headers["user-agent"] == "SuperPlayer/2.0"
    assert calls[0].headers["referer"] == "http://ref.example/"


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_proxied_segment_upstream_errors_are_502(real_api: Api, respx_mock: respx.MockRouter) -> None:
    api = real_api
    respx_mock.route(host="hub.test").pass_through()
    await add_m3u(api, FIXTURES / "sample.m3u")
    await api.patch("/settings", json={"proxy_mode": "always"})
    bbc = await _channel(api, "BBC One HD")
    respx_mock.get("http://streams.example/live/bbc1.m3u8").mock(
        return_value=httpx.Response(200, text="#EXTM3U\n#EXTINF:2,\nboom.ts\n#EXTINF:2,\ngone.ts\n")
    )
    respx_mock.get("http://streams.example/live/boom.ts").mock(side_effect=RuntimeError("unexpected"))
    respx_mock.get("http://streams.example/live/gone.ts").mock(return_value=httpx.Response(404))
    r = await api.client.get(await _resolve(api, bbc["id"]))
    segs = [ln for ln in r.text.splitlines() if ln and not ln.startswith("#")]
    assert (await api.client.get(segs[0])).status_code == 502
    assert (await api.client.get(segs[1])).status_code == 502
