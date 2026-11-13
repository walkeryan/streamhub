"""One upstream session per viewer for a VOD file.

The upstream here behaves like panels that hand out a per-viewer session on redirect: the first
request for a file is redirected to the same path plus ``?session_id=…``. Requests carrying that id
are one viewer; a request without it is another viewer, refused (503) while the first is counted.
ffprobe and ffmpeg are stubbed; what matters is the URL they are handed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.infra.http import pin_upstream_session
from app.proxy import stream_proxy
from app.proxy.stream_proxy import StreamProxy
from app.services.errors import Conflict
from app.services.playback import StreamGrant
from tests.conftest import Api
from tests.test_web_vod import FILM_URL, FakeProbe, add_film, probe

ORIGIN = "http://manager.example.test"
LIVE_URL = f"{ORIGIN}/live/alice/s3cret/3.ts"


class Body(httpx.AsyncByteStream):
    """A file transfer the panel counts until it is closed."""

    def __init__(self, panel: Panel) -> None:
        self.panel, self.closed = panel, False
        panel.open += 1

    async def __aiter__(self) -> Any:
        yield b"x" * 8

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            self.panel.open -= 1


class Panel:
    def __init__(self, router: respx.MockRouter, url: str = FILM_URL) -> None:
        parts = httpx.URL(url)
        router.route(method="GET", host=parts.host, path=parts.path).mock(side_effect=self.answer)
        self.session: str | None = None
        self.issued = 0
        self.open = 0
        # (session id, Range, transfers still open when the request arrived)
        self.seen: list[tuple[str | None, str | None, int]] = []

    def expire(self) -> None:
        self.session = None

    @property
    def unpinned(self) -> int:
        return sum(sid is None for sid, _, _ in self.seen)

    def answer(self, request: httpx.Request) -> httpx.Response:
        sid = request.url.params.get("session_id")
        self.seen.append((sid, request.headers.get("range"), self.open))
        if sid is None:
            if self.session is not None:
                return httpx.Response(503)
            self.issued += 1
            self.session = f"s{self.issued}"
            return httpx.Response(302, headers={"Location": f"{request.url.path}?session_id={self.session}"})
        if sid != self.session:
            return httpx.Response(404)
        return httpx.Response(206, stream=Body(self), headers={"content-type": "video/x-matroska"})


def pinned(session: str) -> str:
    return f"{FILM_URL}?session_id={session}"


def new_proxy(tmp_path: Path, **kw: Any) -> StreamProxy:
    return StreamProxy(lambda **client: httpx.AsyncClient(**client), tmp_path, **kw)


class Process:
    def __init__(self, stderr: bytes = b"", code: int | None = None) -> None:
        self.returncode = code
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        if code is not None:
            self.stderr.feed_eof()

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15
            self.stderr.feed_eof()

    kill = terminate

    async def wait(self) -> int | None:
        return self.returncode


class Ffmpeg:
    """Stub ffmpeg: a session that starts, unless a test queues its last words in ``refusals``."""

    def __init__(self) -> None:
        self.runs: list[list[str]] = []
        self.refusals: list[bytes] = []

    @property
    def inputs(self) -> list[str]:
        return [args[args.index("-i") + 1] for args in self.runs]

    async def spawn(self, *args: Any, **kwargs: Any) -> Process:
        self.runs.append(list(args))
        if self.refusals:
            return Process(self.refusals.pop(0), code=1)
        Path(args[-1]).write_text("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.000000,\nseg_00000.ts\n")
        return Process()


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> Ffmpeg:
    stub = Ffmpeg()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", stub.spawn)
    return stub


# ---------------------------------------------------------------- the helper
@respx.mock
async def test_pin_follows_one_same_origin_redirect_without_reading_the_file(respx_mock: respx.MockRouter) -> None:
    seen: list[httpx.Request] = []
    answer: dict[str, Any] = {"status": 302, "location": "/movie/alice/s3cret/7.mkv?session_id=abc"}

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("session_id"):
            return httpx.Response(206, content=b"x")
        return httpx.Response(answer["status"], headers={"Location": answer["location"]})

    respx_mock.route(host="manager.example.test").mock(side_effect=redirect)
    async with httpx.AsyncClient(follow_redirects=True) as http:
        assert await pin_upstream_session(http, FILM_URL, {"User-Agent": "Player/1"}) == pinned("abc")
        # One hop only: the target itself is not requested, whatever the client's own redirect policy.
        assert len(seen) == 1 and seen[0].headers["range"] == "bytes=0-0"
        assert seen[0].headers["user-agent"] == "Player/1"
        answer["location"] = pinned("abc")  # an absolute Location on the same origin
        for answer["status"] in (301, 303, 307, 308):
            assert await pin_upstream_session(http, FILM_URL, {}) == pinned("abc")
        answer["location"] = "8.mkv?session_id=abc"  # relative to the file's directory
        assert await pin_upstream_session(http, FILM_URL, {}) == f"{ORIGIN}/movie/alice/s3cret/8.mkv?session_id=abc"
    assert len(seen) == 6


@respx.mock
async def test_pin_keeps_the_original_url_when_there_is_nothing_to_pin(
    respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.infra.http")
    upstream = respx_mock.get(FILM_URL)
    async with httpx.AsyncClient() as http:
        # Another origin is an ordinary redirect (a CDN): left to normal redirect handling.
        for elsewhere in ("http://cdn.example.test/7.mkv?session_id=abc", "https://manager.example.test/7.mkv"):
            upstream.mock(return_value=httpx.Response(302, headers={"Location": elsewhere}))
            assert await pin_upstream_session(http, FILM_URL, {}) == FILM_URL
        upstream.mock(return_value=httpx.Response(302))  # no Location
        assert await pin_upstream_session(http, FILM_URL, {}) == FILM_URL
        body = Body(Panel(respx_mock, f"{ORIGIN}/unused"))
        for answer in (httpx.Response(206, stream=body), httpx.Response(503), httpx.Response(404)):
            upstream.mock(return_value=answer)
            assert await pin_upstream_session(http, FILM_URL, {}) == FILM_URL
        assert body.closed  # a server that answers directly is hung up on, not read
        upstream.mock(side_effect=httpx.ConnectError("no route to host"))
        assert await pin_upstream_session(http, FILM_URL, {}) == FILM_URL
    # The one thing logged is the failure, with the credentials in the path redacted.
    assert [record.url for record in caplog.records] == [f"{ORIGIN}/movie/***/***/7.mkv"]
    assert "s3cret" not in caplog.text and "alice" not in caplog.text


# ---------------------------------------------------------------- the proxy's pins
@respx.mock
async def test_a_pin_is_resolved_once_per_playback_and_bounded(
    tmp_path: Path, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = Panel(respx_mock)
    now = [0.0]
    proxy = new_proxy(tmp_path, clock=lambda: now[0])
    first, second = await asyncio.gather(proxy.pin("viewer", FILM_URL, {}), proxy.pin("viewer", FILM_URL, {}))
    assert first == second == pinned("s1") and panel.unpinned == 1
    now[0] += stream_proxy.PIN_TTL_S - 1
    assert await proxy.pin("viewer", FILM_URL, {}) == pinned("s1")  # each use keeps it another two hours
    now[0] += stream_proxy.PIN_TTL_S - 1
    assert await proxy.pin("viewer", FILM_URL, {}) == pinned("s1") and panel.unpinned == 1
    now[0] += stream_proxy.PIN_TTL_S + 1
    panel.expire()
    assert await proxy.pin("viewer", FILM_URL, {}) == pinned("s2") and panel.unpinned == 2
    # An upstream that is busy or down is asked again soon; it is not remembered as "no session".
    assert await proxy.pin("other", FILM_URL, {}) == FILM_URL and panel.unpinned == 3
    now[0] += stream_proxy.PIN_MISS_TTL_S + 1
    panel.expire()
    assert await proxy.pin("other", FILM_URL, {}) == pinned("s3")
    monkeypatch.setattr(stream_proxy, "MAX_PINS", 2)
    await proxy.pin("third", FILM_URL, {})
    assert len(proxy._pins) == 2 and "third" in proxy._pins
    await proxy.stop_owned()
    assert proxy._pins == {}


@respx.mock
async def test_passthrough_ranges_use_one_session_and_never_overlap(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    panel = Panel(respx_mock)
    proxy = new_proxy(tmp_path)
    status, _, first = await proxy.passthrough(FILM_URL, {}, "s", "bytes=0-", max_connections=1, pin_key="viewer")
    assert status == 206 and await anext(first) == b"x" * 8
    # The player seeks: its next Range request arrives while the previous response is still open.
    status, _, second = await proxy.passthrough(FILM_URL, {}, "s", "bytes=4096-", max_connections=1, pin_key="viewer")
    assert status == 206
    assert panel.seen == [(None, "bytes=0-0", 0), ("s1", "bytes=0-", 0), ("s1", "bytes=4096-", 0)]
    assert panel.open == 1 and proxy._source_use("s") == 1
    assert [chunk async for chunk in first] == []  # the replaced response simply ends
    # Another viewer is still held to the source's limit.
    with pytest.raises(Conflict):
        await proxy.passthrough(FILM_URL, {}, "s", "bytes=0-", max_connections=1, pin_key="someone else")
    await second.aclose()
    assert panel.open == 0 and proxy._source_use("s") == 0 and panel.unpinned == 1


@respx.mock
async def test_a_server_that_answers_directly_is_asked_once(tmp_path: Path, respx_mock: respx.MockRouter) -> None:
    seen: list[str | None] = []

    def direct(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("range"))
        return httpx.Response(206, content=b"x")

    respx_mock.get(FILM_URL).mock(side_effect=direct)
    proxy = new_proxy(tmp_path)
    for wanted in ("bytes=0-", "bytes=4096-"):
        _, _, stream = await proxy.passthrough(FILM_URL, {}, "s", wanted, pin_key="viewer")
        await stream.aclose()
    assert seen == ["bytes=0-0", "bytes=0-", "bytes=4096-"]


@respx.mock
async def test_passthrough_repins_once_when_the_session_expired(tmp_path: Path, respx_mock: respx.MockRouter) -> None:
    panel = Panel(respx_mock)
    proxy = new_proxy(tmp_path)
    _, _, stream = await proxy.passthrough(FILM_URL, {}, "s", "bytes=0-", pin_key="viewer")
    await stream.aclose()
    panel.expire()
    status, _, stream = await proxy.passthrough(FILM_URL, {}, "s", "bytes=4096-", pin_key="viewer")
    assert status == 206 and proxy._pins["viewer"][0] == pinned("s2")
    assert [(sid, wanted) for sid, wanted, _ in panel.seen[2:]] == [
        ("s1", "bytes=4096-"),
        (None, "bytes=0-0"),
        ("s2", "bytes=4096-"),
    ]
    await stream.aclose()


@respx.mock
async def test_a_missing_file_costs_one_repin_and_is_still_refused(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    seen: list[str | None] = []

    def gone(request: httpx.Request) -> httpx.Response:
        sid = request.url.params.get("session_id")
        seen.append(sid)
        if sid is None:
            return httpx.Response(302, headers={"Location": f"{request.url.path}?session_id=s{len(seen)}"})
        return httpx.Response(404)

    respx_mock.route(host="manager.example.test").mock(side_effect=gone)
    proxy = new_proxy(tmp_path)
    with pytest.raises(stream_proxy.UpstreamRefused) as refused:
        await proxy.passthrough(FILM_URL, {}, "s", "bytes=0-", pin_key="viewer")
    assert refused.value.status == "404" and seen == [None, "s1", None, "s3"]
    assert proxy._source_use("s") == 0


@respx.mock
async def test_hls_restart_repins_once_when_the_session_expired(
    tmp_path: Path, respx_mock: respx.MockRouter, ffmpeg: Ffmpeg
) -> None:
    panel = Panel(respx_mock)
    proxy = new_proxy(tmp_path)
    assert await proxy.pin("viewer", FILM_URL, {}) == pinned("s1")
    panel.expire()
    ffmpeg.refusals.append(b"Error opening input files: Server returned 404 Not Found\n")
    plan = stream_proxy.VodPlan(video="copy", family="viewer")
    sess = await proxy.hls_session(
        "viewer:vod", FILM_URL, {}, live=False, transcode="none", source_id="s", vod=plan, pin_key="viewer"
    )
    assert ffmpeg.inputs == [pinned("s1"), pinned("s2")] and panel.unpinned == 2
    assert proxy._hls["viewer:vod"] is sess and sess.proc.returncode is None
    # Still refused after a fresh session: an upstream failure, reported as before.
    await proxy.shutdown()
    panel.expire()
    ffmpeg.refusals += [b"Server returned 404 Not Found\n"] * 2
    with pytest.raises(stream_proxy.UpstreamRefused):
        await proxy.hls_session(
            "viewer:vod", FILM_URL, {}, live=False, transcode="none", source_id="s", vod=plan, pin_key="viewer"
        )
    assert len(ffmpeg.runs) == 4 and proxy._hls == {}


@respx.mock
async def test_a_reaped_session_takes_its_pin_with_it(
    tmp_path: Path, respx_mock: respx.MockRouter, ffmpeg: Ffmpeg, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = Panel(respx_mock)
    now = [0.0]
    proxy = new_proxy(tmp_path / "transcode", clock=lambda: now[0])

    async def play(start_s: int) -> None:
        vod = stream_proxy.VodPlan(video="copy", family="viewer", start_s=start_s)
        await proxy.hls_session(
            f"viewer:{start_s}", FILM_URL, {}, live=False, transcode="none", source_id="s", vod=vod, pin_key="viewer"
        )

    await play(0)
    # A film outlasts the TTL: the session that is still running keeps its pin for the next seek.
    now[0] += stream_proxy.PIN_TTL_S + 1
    await play(1800)
    assert ffmpeg.inputs == [pinned("s1")] * 2 and panel.unpinned == 1 and len(proxy._hls) == 1

    pause = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _delay: pause(0))  # run the reaper without its ten-second wait
    now[0] += stream_proxy.HLS_IDLE_S + 1
    proxy.start()
    for _ in range(100):
        await pause(0.01)
        if not proxy._hls:
            break
    assert proxy._hls == {} and proxy._pins == {}
    await proxy.shutdown()


# ---------------------------------------------------------------- one viewer, end to end
def grant_token(api: Api, url: str, *, mode: str, live: bool) -> str:
    return api.c.tokens.issue(StreamGrant(api.profile_id, "iptv", url, {}, mode, live, None, False, **api.identity()))


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_probe_play_and_seek_are_one_upstream_session(
    api: Api, ffmpeg: Ffmpeg, respx_mock: respx.MockRouter
) -> None:
    respx_mock.route(host="hub.test").pass_through()
    panel = Panel(respx_mock)
    await add_film(api)
    fake = api.c.playback.probe = FakeProbe(probe("h264", "yuv420p"))
    assert (await api.get("/playback/options", params={"id": "lib:iptv:7"})).status_code == 200
    assert fake.calls == [(pinned("s1"), {})]

    async def play(**body: Any) -> None:
        d = (await api.post("/playback/resolve", json={"id": "lib:iptv:7", **body})).json()
        assert d["mode"] == "hls", d
        playlist = await api.client.get(d["url"].split("hub.test", 1)[1])
        assert playlist.status_code == 200, playlist.text

    await play()
    await play(start_position_ms=1_800_000)
    assert ffmpeg.inputs == [pinned("s1"), pinned("s1")]
    assert "-ss" not in ffmpeg.runs[0] and ffmpeg.runs[1][ffmpeg.runs[1].index("-ss") + 1] == "1800"
    # The panel saw this viewer arrive once; every later open carried the session it was given.
    assert panel.unpinned == 1 and panel.seen == [(None, "bytes=0-0", 0)]
    await api.c.proxy.shutdown()


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_tv_range_requests_of_one_grant_share_the_session(api: Api, respx_mock: respx.MockRouter) -> None:
    respx_mock.route(host="hub.test").pass_through()
    panel = Panel(respx_mock)
    await add_film(api)
    token = grant_token(api, FILM_URL, mode="proxy", live=False)
    for wanted in ("bytes=0-", "bytes=4096-"):
        response = await api.client.get(f"/api/v1/stream/{token}", headers={"Range": wanted})
        assert response.status_code == 206, response.text
    assert [(sid, wanted) for sid, wanted, _ in panel.seen] == [
        (None, "bytes=0-0"),
        ("s1", "bytes=0-"),
        ("s1", "bytes=4096-"),
    ]


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_live_channels_are_not_pinned(
    api: Api, ffmpeg: Ffmpeg, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx_mock.route(host="hub.test").pass_through()
    await add_film(api)
    seen: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(403)

    respx_mock.route(host="manager.example.test").mock(side_effect=refuse)
    pins: list[str] = []
    resolve = api.c.proxy.pin

    async def spy(key: str, *args: Any, **kwargs: Any) -> str:
        pins.append(key)
        return str(await resolve(key, *args, **kwargs))

    monkeypatch.setattr(api.c.proxy, "pin", spy)
    hls = await api.client.get(f"/api/v1/stream/{grant_token(api, LIVE_URL, mode='hls', live=True)}/index.m3u8")
    assert hls.status_code == 200 and ffmpeg.inputs == [LIVE_URL] and seen == []
    await api.c.proxy.shutdown()
    # A live passthrough opens its upstream once, as before (refused here so the test ends).
    raw = await api.client.get(f"/api/v1/stream/{grant_token(api, LIVE_URL, mode='proxy', live=True)}")
    assert raw.status_code == 502
    assert len(seen) == 1 and "range" not in seen[0].headers and pins == []
