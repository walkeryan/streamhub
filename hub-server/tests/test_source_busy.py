"""A refused stream says who is using the source: ``source_busy`` carries the active streams."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.domain.entities import StreamTarget
from app.infra.models import Source, Title
from app.proxy import stream_proxy
from app.proxy.stream_proxy import StreamProxy, UpstreamRefused, Viewer
from app.services.errors import Forbidden
from app.services.playback import StreamGrant
from tests.conftest import Api
from tests.test_epg_integrity import add_source

LIVE = "http://streams.example.test/live"
FILM = "http://vod.example.test/film.mkv"


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


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[Process]:
    """Stub ffmpeg: a session that starts, unless a test queues another outcome in ``refuse``."""
    started: list[Process] = []

    async def spawn(*args: Any, **kwargs: Any) -> Process:
        Path(args[-1]).write_text("#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nseg.ts\n")
        started.append(Process())
        return started[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    # Do not wait out the admission window: the other viewer is polling, it will not free up.
    monkeypatch.setattr(stream_proxy, "HLS_ADMISSION_WAIT_S", 0.0)
    return started


async def limited_source(api: Api, limit: int) -> str:
    source = await add_source(api)
    async with api.c.db.session() as session:
        row = await session.get(Source, source["id"])
        assert row is not None
        row.hints = {**row.hints, "max_connections": limit}
        await session.commit()
    return str(source["id"])


def grant(api: Api, source_id: str, url: str, *, profile_id: str | None = None, **fields: Any) -> str:
    live = fields.pop("live", True)
    mode = fields.pop("mode", "hls")
    viewer = {**api.identity(profile_id), **fields}
    return api.c.tokens.issue(
        StreamGrant(profile_id or api.profile_id, source_id, url, {}, mode, live, None, False, **viewer)
    )


async def viewer_profile(api: Api, name: str = "Guest") -> str:
    created = await api.post("/profiles", json={"name": name})
    assert created.status_code == 201, created.text
    await api.session_for(created.json()["id"])
    return str(created.json()["id"])


# ---------------------------------------------------------------- the proxy knows its viewers
async def test_proxy_lists_who_holds_a_source(tmp_path: Path, ffmpeg: list[Process]) -> None:
    proxy = StreamProxy(lambda **kwargs: None, tmp_path)
    news = Viewer("p1", "Evening News", "channel", "ch:s:1")
    film = Viewer("p2", "The Long Film", "movie", "lib:s:7")
    await proxy.hls_session("news", f"{LIVE}/1", {}, live=True, transcode="none", source_id="s", viewer=news)
    # A second profile on the same channel shares the connection and is listed too; a repeat is not.
    await proxy.hls_session("news", f"{LIVE}/1", {}, live=True, transcode="none", source_id="s", viewer=news)
    await proxy.hls_session("news", f"{LIVE}/1", {}, live=True, transcode="none", source_id="s", viewer=film)
    await proxy.hls_session("other", f"{LIVE}/2", {}, live=True, transcode="none", source_id="elsewhere", viewer=film)
    assert [(v.profile_id, v.title, v.kind) for v in proxy.active_viewers("s")] == [
        ("p1", "Evening News", "channel"),
        ("p2", "The Long Film", "movie"),
    ]
    assert proxy.active_viewers("unused") == []
    await proxy.shutdown()
    assert proxy.active_viewers("s") == []


# ---------------------------------------------------------------- 409 bodies
async def test_busy_hls_answer_names_the_other_viewer(api: Api, ffmpeg: list[Process]) -> None:
    source = await limited_source(api, 1)
    guest = await viewer_profile(api)
    first = grant(api, source, f"{LIVE}/news.ts", profile_id=guest, title="Evening News", kind="channel")
    assert (await api.client.get(f"/api/v1/stream/{first}/index.m3u8")).status_code == 200
    second = grant(api, source, f"{LIVE}/sport.ts", title="Sport Live", kind="channel")
    response = await api.client.get(f"/api/v1/stream/{second}/index.m3u8")
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "source_busy" and body["limit"] == 1
    [active] = body["active"]
    assert (active["profile_name"], active["title"], active["kind"]) == ("Guest", "Evening News", "channel")
    assert datetime.fromisoformat(active["since"]).tzinfo is not None
    assert len(ffmpeg) == 1 and "example.test" not in response.text
    await api.c.proxy.shutdown()


async def test_busy_passthrough_answer_names_the_viewer_and_hides_private_titles(
    api: Api, ffmpeg: list[Process], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await limited_source(api, 1)
    guest = await viewer_profile(api)
    watching = grant(api, source, f"{LIVE}/news.ts", profile_id=guest, title="Evening News", content_ref="ch:x:1")
    assert (await api.client.get(f"/api/v1/stream/{watching}/index.m3u8")).status_code == 200
    film = grant(api, source, FILM, live=False, mode="proxy", title="The Long Film", kind="movie")
    response = await api.client.get(f"/api/v1/stream/{film}")
    assert response.status_code == 409 and response.json()["code"] == "source_busy"
    assert [(a["profile_name"], a["title"]) for a in response.json()["active"]] == [("Guest", "Evening News")]

    # What the refused profile may not open is not named to it either.
    async def deny(profile_id: str, ref: str, at: Any = None) -> None:
        raise Forbidden("blocked", code="content_restricted")

    monkeypatch.setattr(api.c.content_policy, "require", deny)
    hidden = (await api.client.get(f"/api/v1/stream/{film}")).json()
    assert [(a["profile_name"], a["title"], a["kind"]) for a in hidden["active"]] == [("Guest", None, None)]
    await api.c.proxy.shutdown()


async def test_resolve_puts_a_short_title_and_kind_in_the_grant(api: Api) -> None:
    source = await add_source(api)
    channels = (await api.get("/channels", params={"language": "all"})).json()["channels"]
    channel = next(c for c in channels if c["name"] == "MBC 1")  # raw MPEG-TS: remuxed for every client
    d = (await api.post("/playback/resolve", json={"id": channel["id"], "client": "android_tv"})).json()
    g = api.c.tokens.verify(d["url"].split("/stream/", 1)[1].split("/", 1)[0])
    assert g is not None and (g.title, g.kind) == (channel["name"], "channel")
    # An adult title is never named to another profile.
    async with api.c.db.session() as session:
        session.add(Title(source_id=source["id"], ext_id="a", kind="movie", title="After Dark", adult=True))
        session.add(Title(source_id=source["id"], ext_id="b", kind="movie", title="The Long Film " + "x" * 200))
        await session.commit()
    resolver = api.c.playback
    src = await api.c.sources.get(source["id"])
    for ext, expected in (("a", None), ("b", "The Long Film " + "x" * 66)):
        row = await api.c.catalog.title_row(source["id"], ext)
        desc = resolver._descriptor(
            api.profile_id,
            src,
            f"lib:{source['id']}:{ext}",
            StreamTarget(url=FILM, mime="video/mp4"),
            "web",
            True,  # an http file on an https site goes through the proxy, so there is a grant to read
            channel_key=None,
            title=row.title,
            subtitle=None,
            poster=None,
            kind=row.kind,
            private=bool(row.adult),
        )
        g = api.c.tokens.verify(desc["url"].split("/stream/", 1)[1])
        assert g is not None and (g.title, g.kind) == (expected, "movie")


# ---------------------------------------------------------------- the manager refuses the open
@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_refused_open_at_the_limit_is_source_busy(
    api: Api, ffmpeg: list[Process], respx_mock: respx.MockRouter
) -> None:
    respx_mock.route(host="hub.test").pass_through()
    source = await limited_source(api, 2)
    guest = await viewer_profile(api)
    film = grant(api, source, FILM, live=False, mode="proxy", title="The Long Film", kind="movie")
    upstream = respx_mock.get(FILM).mock(return_value=httpx.Response(403))

    # Nobody else is on the source: a refusal is an upstream failure, as before.
    alone = await api.client.get(f"/api/v1/stream/{film}")
    assert alone.status_code == 502 and alone.json()["code"] == "upstream_error"

    watching = grant(api, source, f"{LIVE}/news.ts", profile_id=guest, title="Evening News", kind="channel")
    assert (await api.client.get(f"/api/v1/stream/{watching}/index.m3u8")).status_code == 200
    # One of two connections is ours, the open is refused: something outside holds the other.
    for refusal in (httpx.Response(403), httpx.Response(429), httpx.Response(503)):
        upstream.mock(return_value=refusal)
        busy = await api.client.get(f"/api/v1/stream/{film}")
        assert busy.status_code == 409 and busy.json()["code"] == "source_busy", busy.text
        assert [(a["profile_name"], a["title"]) for a in busy.json()["active"]] == [("Guest", "Evening News")]
    upstream.mock(side_effect=httpx.RemoteProtocolError("Server disconnected without sending a response."))
    assert (await api.client.get(f"/api/v1/stream/{film}")).status_code == 409
    # A missing file is still a missing file.
    upstream.mock(return_value=httpx.Response(404))
    assert (await api.client.get(f"/api/v1/stream/{film}")).status_code == 502
    assert api.c.proxy._source_use(source) == 1  # no refused attempt left a connection counted
    await api.c.proxy.shutdown()


async def test_ffmpeg_reports_a_refused_open_without_keeping_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def spawn(*args: Any, **kwargs: Any) -> Process:
        text = f"Error opening input file {FILM}?token=s3cret\nError opening input files: Server returned 5XX Server Error reply\n"
        return Process(text.encode(), code=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    proxy = StreamProxy(lambda **kwargs: None, tmp_path)
    with pytest.raises(UpstreamRefused) as refused:
        await proxy.hls_session("film", FILM, {}, live=False, transcode="none", source_id="s")
    assert refused.value.status == "5XX" and "s3cret" not in str(refused.value)
    assert isinstance(refused.value, ConnectionError)  # still an upstream failure to callers that do not care
    await proxy.shutdown()


async def test_refused_hls_open_at_the_limit_is_source_busy(
    api: Api, ffmpeg: list[Process], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await limited_source(api, 2)
    guest = await viewer_profile(api)
    watching = grant(api, source, f"{LIVE}/news.ts", profile_id=guest, title="Evening News", kind="channel")
    assert (await api.client.get(f"/api/v1/stream/{watching}/index.m3u8")).status_code == 200

    async def refused(*args: Any, **kwargs: Any) -> Process:
        return Process(b"Error opening input files: Server returned 403 Forbidden (access denied)\n", code=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", refused)
    sport = grant(api, source, f"{LIVE}/sport.ts", title="Sport Live", kind="channel")
    response = await api.client.get(f"/api/v1/stream/{sport}/index.m3u8")
    assert response.status_code == 409 and response.json()["code"] == "source_busy", response.text
    assert [a["title"] for a in response.json()["active"]] == ["Evening News"]
    await api.c.proxy.shutdown()
