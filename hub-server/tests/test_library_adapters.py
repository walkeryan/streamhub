from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import respx

from app.adapters.jellyfin.provider import JellyfinSource
from app.adapters.plex.provider import PlexLinkFlow, PlexSource, parse_guids, pick_connection
from app.domain.entities import CatalogItem, MediaKind, SourceRef, SourceStatus, SourceType
from app.domain.ports import AdapterContext
from app.infra.cache import MemoryCache
from app.infra.models import utcnow
from tests.conftest import Api

PLEX = "http://192.168.1.5:32400"
JF = "http://jelly.local:8096"


def ctx(tmp_path: Path) -> AdapterContext:
    return AdapterContext(
        http_factory=lambda **kw: httpx.AsyncClient(headers=kw.get("headers")),
        cache=MemoryCache(),
        data_dir=tmp_path,
        client_id="cid-1",
        product="StreamHub",
    )


class Sink:
    def __init__(self) -> None:
        self.items: list[CatalogItem] = []

    async def add_channels(self, channels: Any) -> None: ...

    async def add_items(self, items: Any) -> None:
        self.items.extend(items)

    async def set_config_hint(self, key: str, value: Any) -> None: ...


def test_parse_guids_and_connections() -> None:
    assert parse_guids({"Guid": [{"id": "imdb://tt0133093"}, {"id": "tmdb://603"}]}) == (603, "tt0133093")
    assert parse_guids({"guid": "com.plexapp.agents.themoviedb://603?lang=en"}) == (603, None)
    res = {
        "connections": [
            {"uri": "https://relay.plex.direct", "relay": True, "local": False},
            {"uri": "https://1-2-3-4.abc.plex.direct:32400", "local": False, "protocol": "https"},
            {"uri": "http://192.168.1.5:32400", "local": True, "protocol": "http"},
        ]
    }
    assert pick_connection(res) == "http://192.168.1.5:32400"


@respx.mock
async def test_plex_pin_flow(tmp_path: Path) -> None:
    start = respx.post("https://clients.plex.tv/api/v2/pins").mock(
        return_value=httpx.Response(201, json={"id": 4242, "code": "abcd"})
    )
    flow = PlexLinkFlow(ctx(tmp_path))
    pin = await flow.start()
    assert pin["pin_id"] == 4242 and pin["code"] == "abcd" and "code=abcd" in pin["link_url"]
    req = start.calls[0].request
    assert req.url.params["strong"] == "true"
    assert req.headers["X-Plex-Client-Identifier"] == "cid-1" and req.headers["X-Plex-Product"] == "StreamHub"

    poll = respx.get("https://clients.plex.tv/api/v2/pins/4242").mock(
        side_effect=[httpx.Response(200, json={"authToken": None}), httpx.Response(200, json={"authToken": "acct"})]
    )
    respx.get("https://clients.plex.tv/api/v2/resources").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "Home",
                    "provides": "server",
                    "accessToken": "srvtok",
                    "clientIdentifier": "m1",
                    "connections": [{"uri": PLEX, "local": True}],
                },
                {"name": "Phone", "provides": "player", "connections": [{"uri": "http://x"}]},
            ],
        )
    )
    assert await flow.poll(4242) is None
    drafts = await flow.poll(4242)
    assert drafts is not None and len(drafts) == 1
    assert drafts[0].config["server_url"] == PLEX and drafts[0].config["token"] == "srvtok"
    assert poll.call_count == 2


@respx.mock
async def test_plex_sync_stream_progress_and_continue(tmp_path: Path) -> None:
    src = PlexSource(SourceRef("p1", SourceType.PLEX, "Home"), {"server_url": PLEX, "token": "tok"}, ctx(tmp_path))
    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Directory": [{"key": "1", "type": "movie", "title": "Movies"}, {"key": "3", "type": "artist"}]
                }
            },
        )
    )
    movie = {
        "ratingKey": "111",
        "type": "movie",
        "title": "The Matrix",
        "year": 1999,
        "addedAt": 1700000000,
        "thumb": "/library/metadata/111/thumb/1",
        "Guid": [{"id": "tmdb://603"}],
        "duration": 8160000,
        "Media": [{"Part": [{"key": "/library/parts/9/file.mkv", "container": "mkv"}]}],
    }
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"totalSize": 1, "Metadata": [movie]}})
    )
    sink = Sink()
    await src.sync(sink)
    it = sink.items[0]
    assert it.kind == MediaKind.MOVIE and it.tmdb_id == 603 and it.group == "Movies"
    assert it.poster == f"{PLEX}/library/metadata/111/thumb/1"  # no token in persisted URL
    assert src.image_headers(it.poster or "") == {"X-Plex-Token": "tok", "Accept": "image/*"}
    assert src.image_headers("http://elsewhere/x.jpg") is None
    t = await src.stream_for_item(it)
    assert t.url == f"{PLEX}/library/parts/9/file.mkv?X-Plex-Token=tok" and t.mime == "video/x-matroska"

    tl = respx.get(f"{PLEX}/:/timeline").mock(return_value=httpx.Response(200))
    await src.report_progress(it, 60000, 8160000, "paused")
    p = tl.calls[0].request.url.params
    assert (p["ratingKey"], p["state"], p["time"], p["duration"]) == ("111", "paused", "60000", "8160000")

    respx.get(f"{PLEX}/hubs/continueWatching").mock(return_value=httpx.Response(404))
    respx.get(f"{PLEX}/library/onDeck").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": [{**movie, "viewOffset": 1234}]}})
    )
    cw = await src.continue_watching()
    assert cw[0][0].ext_id == "111" and cw[0][1] == 1234


@respx.mock
async def test_jellyfin_password_and_quick_connect(tmp_path: Path) -> None:
    auth = respx.post(f"{JF}/Users/AuthenticateByName").mock(
        return_value=httpx.Response(200, json={"AccessToken": "jtok", "User": {"Id": "u1"}})
    )
    src = JellyfinSource(
        SourceRef("j1", SourceType.JELLYFIN, "JF"),
        {"server_url": JF, "username": "bob", "password": "pw"},
        ctx(tmp_path),
    )
    done, upd = await src.complete_auth()
    assert done and upd["access_token"] == "jtok" and upd["password"] is None
    assert 'DeviceId="cid-1"' in auth.calls[0].request.headers["Authorization"]

    respx.get(f"{JF}/Users/u1/Items").mock(
        return_value=httpx.Response(
            200,
            json={
                "TotalRecordCount": 1,
                "Items": [
                    {
                        "Id": "abc",
                        "Type": "Movie",
                        "Name": "Dune",
                        "ProductionYear": 2021,
                        "ProviderIds": {"Tmdb": "438631", "Imdb": "tt1160419"},
                        "ImageTags": {"Primary": "t1"},
                        "RunTimeTicks": 93_000_000_000,
                        "DateCreated": "2024-01-02T03:04:05.1234567Z",
                        "MediaSources": [{"Container": "mkv"}],
                    }
                ],
            },
        )
    )
    sink = Sink()
    await src.sync(sink)
    it = sink.items[0]
    assert it.tmdb_id == 438631 and it.imdb_id == "tt1160419" and it.duration_ms == 9_300_000
    assert it.added_at is not None and it.added_at.year == 2024
    assert it.poster == f"{JF}/Items/abc/Images/Primary?tag=t1"
    respx.post(f"{JF}/Items/abc/PlaybackInfo").mock(
        return_value=httpx.Response(200, json={"MediaSources": [{"Id": "ms1", "Container": "mkv"}]})
    )
    t = await src.stream_for_item(it)
    assert t.url == f"{JF}/Videos/abc/stream?static=true&api_key=jtok&MediaSourceId=ms1"
    prog = respx.post(f"{JF}/Sessions/Playing/Progress").mock(return_value=httpx.Response(204))
    await src.report_progress(it, 1000, None, "playing")
    import json

    assert json.loads(prog.calls[0].request.content)["PositionTicks"] == 10_000_000

    # Quick Connect: initiate → pending → authenticated
    qc = JellyfinSource(
        SourceRef("j2", SourceType.JELLYFIN, "JF"), {"server_url": JF, "quick_connect": True}, ctx(tmp_path)
    )
    respx.post(f"{JF}/QuickConnect/Initiate").mock(
        return_value=httpx.Response(200, json={"Secret": "s", "Code": "123456"})
    )
    done, upd = await qc.complete_auth()
    assert not done and upd["quick_connect_code"] == "123456"
    assert qc.config_summary()["quick_connect_code"] == "123456"
    respx.get(f"{JF}/QuickConnect/Connect").mock(
        side_effect=[
            httpx.Response(200, json={"Authenticated": False}),
            httpx.Response(200, json={"Authenticated": True}),
        ]
    )
    respx.post(f"{JF}/Users/AuthenticateWithQuickConnect").mock(
        return_value=httpx.Response(200, json={"AccessToken": "qtok", "User": {"Id": "u2"}})
    )
    assert (await qc.complete_auth())[0] is False
    done, upd = await qc.complete_auth()
    assert done and upd["access_token"] == "qtok" and upd["quick_connect_secret"] is None


async def test_scheduler_due_sources_and_epg_cleanup(api: Api) -> None:
    from app.infra.models import Programme

    await api.accept_disclaimer()
    r = await api.post(
        "/sources",
        json={
            "type": "m3u",
            "name": "x",
            "config": {"file": "#EXTM3U\n#EXTINF:-1,A\nhttp://x/a.ts\n", "refresh_hours": 6},
        },
    )
    sid = r.json()["id"]
    await api.c.sync.wait_idle()
    assert sid not in await api.c.sync.due_sources()
    await api.c.sources.set_status(sid, SourceStatus.OK, last_sync=utcnow() - timedelta(hours=7))
    assert sid in await api.c.sync.due_sources()
    async with api.c.db.session() as s:
        s.add(
            Programme(
                epg_channel="a",
                feed=sid,
                start=utcnow() - timedelta(days=4),
                stop=utcnow() - timedelta(days=3),
                title="old",
            )
        )
        s.add(Programme(epg_channel="a", feed=sid, start=utcnow(), stop=utcnow() + timedelta(hours=1), title="new"))
        await s.commit()
    assert await api.c.sync.cleanup_epg() == 1
    from sqlalchemy import select

    from app.infra.models import SyncRun

    async with api.c.db.session() as s:
        runs = list((await s.execute(select(SyncRun).where(SyncRun.source_id == sid))).scalars())
    assert runs and runs[0].status == "ok" and runs[0].job == "catalog"


async def test_sync_marks_error_status(api: Api) -> None:
    await api.accept_disclaimer()
    r = await api.post(
        "/sources", json={"type": "m3u", "name": "bad", "config": {"url": "http://nowhere.invalid/x.m3u"}}
    )
    await api.c.sync.wait_idle()
    got = (await api.get(f"/sources/{r.json()['id']}")).json()
    assert got["status"] == "error"
    t = (await api.post(f"/sources/{r.json()['id']}/test")).json()
    assert t["ok"] is False
