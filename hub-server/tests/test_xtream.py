from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.adapters.xtream import fields as F
from app.adapters.xtream.provider import (
    XtreamClient,
    XtreamSource,
    map_episodes,
    map_live,
    map_series,
    map_vod,
    normalise_server,
)
from app.domain.entities import CatalogItem, ChannelInfo, MediaKind, ProgrammeInfo, SourceRef, SourceType
from app.domain.ports import AdapterContext
from app.infra.cache import MemoryCache

SERVER = "http://panel.example:8080"


def ctx(tmp_path: Path) -> AdapterContext:
    return AdapterContext(
        http_factory=lambda **kw: httpx.AsyncClient(headers=kw.get("headers")), cache=MemoryCache(), data_dir=tmp_path
    )


@pytest.fixture
def client(tmp_path: Path) -> XtreamClient:
    return XtreamClient(ctx(tmp_path), SERVER + "/player_api.php", "alice", "s3cret", None)


def test_normalise_server() -> None:
    assert normalise_server("panel.example:8080/") == "http://panel.example:8080"
    assert normalise_server("https://p.example/get.php?username=a") == "https://p.example"


def test_field_helpers() -> None:
    assert F.as_int("12abc") == 12 and F.as_int(None) is None and F.as_int(3.9) == 3
    assert F.as_bool("1") and F.as_bool(1) and F.as_bool("true") and not F.as_bool("0") and not F.as_bool(None)
    assert F.as_float("7,5") == 7.5 and F.as_float("") is None
    assert F.year_from("2021-05-02") == 2021 and F.year_from(None, "Movie (1999)") == 1999
    assert F.maybe_b64(base64.b64encode(b"Hello World").decode()) == "Hello World"
    assert F.maybe_b64("Plain title") == "Plain title"
    assert F.tmdb_from({"tmdb_id": "603"}) == 603 and F.tmdb_from({"tmdb": ""}) is None
    assert F.imdb_from({"imdb": "tt0133093"}) == "tt0133093" and F.imdb_from({"imdb": "bogus"}) is None


@pytest.mark.parametrize(
    "row",
    [
        # classic XC
        {
            "num": 1,
            "name": "CNN",
            "stream_id": 101,
            "stream_icon": "http://i/cnn.png",
            "epg_channel_id": "cnn.us",
            "category_id": "5",
            "tv_archive": 1,
            "tv_archive_duration": "3",
        },
        # XUI-ish: string ids, different names
        {
            "number": "1",
            "title": "CNN",
            "id": "101",
            "icon": "http://i/cnn.png",
            "epg_id": "cnn.us",
            "categoryId": 5,
            "tvArchive": "1",
            "archive_duration": 3,
        },
        # minimal / odd panel with category name inline
        {
            "stream_id": "101",
            "stream_name": "CNN",
            "logo": "http://i/cnn.png",
            "tvg_id": "cnn.us",
            "category_id": "5",
            "tv_archive": True,
            "tv_archive_duration": "3 days",
        },
    ],
)
def test_map_live_varied_fields(row: dict[str, Any], client: XtreamClient) -> None:
    ch = map_live(row, {"5": "News"}, "ts", client)
    assert ch is not None
    assert ch.ext_id == "101" and ch.name == "CNN"
    assert ch.logo == "http://i/cnn.png" and ch.tvg_id == "cnn.us"
    assert ch.group == "News"
    assert ch.catchup_days == 3 and ch.catchup_type == "xtream"
    assert ch.stream_url is None  # credentials are never persisted in catalog rows


def test_map_live_without_archive_and_bad_row(client: XtreamClient) -> None:
    ch = map_live({"stream_id": 5, "name": "X", "tv_archive": "0", "tv_archive_duration": "7"}, {}, "ts", client)
    assert ch is not None and ch.catchup_days == 0 and ch.group == "Uncategorized"
    assert map_live({"name": "no id"}, {}, "ts", client) is None


@pytest.mark.parametrize(
    "row",
    [
        {
            "stream_id": 7,
            "name": "The Matrix",
            "year": "1999",
            "stream_icon": "http://p/m.jpg",
            "rating": "8.7",
            "tmdb": "603",
            "container_extension": "mkv",
            "category_id": "2",
            "added": "1700000000",
        },
        {
            "id": "7",
            "title": "The Matrix (1999)",
            "cover": "http://p/m.jpg",
            "rating_5based": 4.3,
            "tmdb_id": 603,
            "extension": "mkv",
            "categoryId": "2",
            "added": 1700000000,
        },
    ],
)
def test_map_vod_varied_fields(row: dict[str, Any], client: XtreamClient) -> None:
    it = map_vod(row, {"2": "Action"}, client)
    assert it is not None
    assert it.kind == MediaKind.VOD and it.ext_id == "7"
    assert it.year == 1999 and it.tmdb_id == 603 and it.poster == "http://p/m.jpg"
    assert it.group == "Action" and it.extra["ext"] == "mkv"
    assert it.added_at == datetime.fromtimestamp(1700000000, UTC)


def test_map_series_and_episodes(client: XtreamClient) -> None:
    s = map_series(
        {
            "series_id": 9,
            "name": "Dark",
            "cover": "c.jpg",
            "plot": "Time travel",
            "releaseDate": "2017-12-01",
            "genre": "Drama / Sci-Fi",
            "backdrop_path": ["b.jpg"],
            "tmdb": "70523",
        },
        {},
    )
    assert s is not None and s.kind == MediaKind.SERIES and s.year == 2017
    assert s.genres == ["Drama", "Sci-Fi"] and s.backdrop == "b.jpg" and s.tmdb_id == 70523
    info = {
        "seasons": [{"season_number": 1, "name": "Season One", "cover": "s1.jpg"}],
        "episodes": {
            "1": [
                {
                    "id": "1001",
                    "episode_num": 1,
                    "title": "Secrets",
                    "container_extension": "mp4",
                    "info": {"duration_secs": 3000, "plot": "Ep1"},
                    "season": 1,
                },
                {"id": "1002", "episode_num": "2", "title": "Lies", "container_extension": "mkv", "info": []},
            ]
        },
    }
    items = map_episodes("9", info, client)
    season = items[0]
    assert season.kind == MediaKind.SEASON and season.title == "Season One" and season.ext_id == "9:s1"
    eps = items[1:]
    assert [e.ext_id for e in eps] == ["1001", "1002"]
    assert eps[0].duration_ms == 3_000_000 and eps[0].parent_ext_id == "9:s1" and eps[1].episode_number == 2


def test_map_episodes_list_shape(client: XtreamClient) -> None:
    info = {"episodes": [[{"id": "1", "episode_num": 1, "title": "A"}], [{"id": "2", "episode_num": 1, "title": "B"}]]}
    items = map_episodes("5", info, client)
    assert [(i.kind.value, i.season_number) for i in items] == [
        ("season", 1),
        ("episode", 1),
        ("season", 2),
        ("episode", 2),
    ]


def make_source(tmp_path: Path, **cfg: Any) -> XtreamSource:
    config = {"server_url": SERVER, "username": "alice", "password": "s3cret", "output": "ts", **cfg}
    return XtreamSource(SourceRef("src1", SourceType.XTREAM, "Panel"), config, ctx(tmp_path))


async def test_stream_and_catchup_urls(tmp_path: Path) -> None:
    src = make_source(tmp_path)
    ch = ChannelInfo(ext_id="101", name="CNN", catchup_days=3, extra={"ext": "ts"})
    t = await src.stream_for_channel(ch)
    assert t.url == f"{SERVER}/live/alice/s3cret/101.ts" and t.mime == "video/mp2t" and t.live
    vod = CatalogItem(ext_id="7", kind=MediaKind.VOD, title="M", extra={"ext": "mkv"})
    assert (await src.stream_for_item(vod)).url == f"{SERVER}/movie/alice/s3cret/7.mkv"
    ep = CatalogItem(ext_id="1001", kind=MediaKind.EPISODE, title="E", extra={"ext": "mp4"})
    assert (await src.stream_for_item(ep)).url == f"{SERVER}/series/alice/s3cret/1001.mp4"

    start = (datetime.now(UTC) - timedelta(hours=5)).replace(second=0, microsecond=0)
    prog = ProgrammeInfo("cnn.us", start, start + timedelta(minutes=45), "News")
    cu = await src.catchup_for_channel(ch, prog)
    assert cu is not None
    assert cu.url.startswith(f"{SERVER}/streaming/timeshift.php?")
    assert f"start={start.strftime('%Y-%m-%d:%H-%M')}" in cu.url and "duration=45" in cu.url and "stream=101" in cu.url
    old = ProgrammeInfo("cnn.us", start - timedelta(days=5), start - timedelta(days=5, minutes=-30), "Old")
    assert await src.catchup_for_channel(ch, old) is None


def test_config_summary_has_no_secrets(tmp_path: Path) -> None:
    summary = make_source(tmp_path).config_summary()
    flat = repr(summary)
    assert "s3cret" not in flat and "alice" not in flat
    assert summary["server_url"] == SERVER


class Sink:
    def __init__(self) -> None:
        self.channels: list[ChannelInfo] = []
        self.items: list[CatalogItem] = []
        self.hints: dict[str, Any] = {}

    async def add_channels(self, channels: Any) -> None:
        self.channels.extend(channels)

    async def add_items(self, items: Any) -> None:
        self.items.extend(items)

    async def set_config_hint(self, key: str, value: Any) -> None:
        self.hints[key] = value


@respx.mock
async def test_full_sync_and_short_epg(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        assert q["username"] == "alice" and q["password"] == "s3cret"
        action = q.get("action")
        data: Any = {
            None: {
                "user_info": {"auth": 1, "status": "Active", "max_connections": "1", "exp_date": "1900000000"},
                "server_info": {"url": "panel.example"},
            },
            "get_live_categories": [{"category_id": "1", "category_name": "UK | News"}],
            "get_live_streams": {
                "0": {"stream_id": 1, "name": "BBC News", "category_id": "1", "epg_channel_id": "bbcnews.uk"}
            },
            "get_vod_categories": [{"category_id": 3, "category_name": "Movies"}],
            "get_vod_streams": [{"stream_id": 7, "name": "Film (2020)", "category_id": 3}],
            "get_series_categories": [],
            "get_series": [{"series_id": 9, "name": "Show"}],
            "get_short_epg": {
                "epg_listings": [
                    {
                        "title": base64.b64encode(b"Morning").decode(),
                        "description": base64.b64encode(b"desc").decode(),
                        "start_timestamp": "1761900000",
                        "stop_timestamp": "1761903600",
                    }
                ]
            },
        }[action]
        return httpx.Response(200, json=data)

    respx.get(f"{SERVER}/player_api.php").mock(side_effect=handler)
    src = make_source(tmp_path)
    assert (await src.test()).ok
    sink = Sink()
    await src.sync(sink)
    assert [c.name for c in sink.channels] == ["BBC News"] and sink.channels[0].group == "UK | News"
    assert {i.kind for i in sink.items} == {MediaKind.VOD, MediaKind.SERIES}
    assert sink.hints["max_connections"] == 1
    epg = await src.short_epg(sink.channels[0])
    assert epg[0].title == "Morning" and epg[0].description == "desc" and epg[0].channel == "bbcnews.uk"
    specs = await src.epg_sources()
    assert specs[0].url.startswith(f"{SERVER}/xmltv.php?")


@respx.mock
async def test_auth_failure(tmp_path: Path) -> None:
    respx.get(f"{SERVER}/player_api.php").mock(return_value=httpx.Response(200, json={"user_info": {"auth": 0}}))
    res = await make_source(tmp_path).test()
    assert not res.ok and "s3cret" not in res.message


@pytest.mark.parametrize(
    "audio,expected",
    [
        ({"codec_type": "audio", "tags": {"language": "eng"}, "disposition": {"default": 1}}, ["eng"]),
        ([{"tags": {"language": "fra"}}, {"tags": {"language": "eng"}}], ["eng", "fra"]),
        ({"codec_type": "audio", "codec_name": "aac"}, []),
        ({"codec_type": "video", "tags": {"language": "eng"}}, []),
        ("English", []),
        (None, []),
    ],
)
async def test_vod_detail_preserves_only_explicit_audio_metadata(tmp_path, audio, expected):
    from unittest.mock import AsyncMock

    from app.services.audio_selection import audio_info

    src = make_source(tmp_path)
    src.client.api = AsyncMock(
        return_value={
            "info": {
                "audio": audio,
                "original_language": "en",
                "language": "en",
            }
        }
    )
    item = CatalogItem(ext_id="7", kind=MediaKind.VOD, title="EN - Film", extra={"ext": "mkv"})
    result = await src.item_detail(item)
    src.client.api.assert_awaited_once_with("get_vod_info", vod_id="7")
    assert result.extra["ext"] == "mkv"
    evidence = audio_info(result.extra)
    assert evidence["audio_languages"] == expected
    assert evidence["audio_verified"] is bool(expected)
    assert "original_language" not in result.extra


def test_vod_and_episode_mapping_retain_audio_tags(client):
    from app.services.audio_selection import audio_info

    item = map_vod({"stream_id": 7, "name": "FR Film", "audio": {"tags": {"language": "eng"}}}, {}, client)
    assert audio_info(item.extra)["audio_languages"] == ["eng"]
    episodes = map_episodes(
        "9",
        {
            "episodes": {
                "1": [
                    {
                        "id": "1001",
                        "title": "EN Episode",
                        "info": {"audio": {"tags": {"language": "fra"}}},
                    }
                ]
            }
        },
        client,
    )
    assert audio_info(episodes[1].extra)["audio_languages"] == ["fra"]


async def test_missing_audio_detail_does_not_fabricate_evidence(tmp_path):
    from unittest.mock import AsyncMock

    from app.services.audio_selection import audio_info

    src = make_source(tmp_path)
    src.client.api = AsyncMock(return_value={"info": {"original_language": "en", "language": "en"}})
    item = CatalogItem(ext_id="7", kind=MediaKind.VOD, title="EN - Film", extra={"ext": "mkv"})
    assert not audio_info((await src.item_detail(item)).extra)["audio_verified"]
