from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.adapters.deeplink.registry import DeeplinkRegistry, load_seed, parse_deeplinks_md
from app.adapters.tmdb.client import TmdbClient
from app.domain.ports import AdapterContext
from app.infra.cache import MemoryCache
from app.infra.models import Source, Title
from app.services.ids import normalise_title
from tests.conftest import Api

TMDB = "https://api.themoviedb.org/3"

MATRIX = {
    "id": 603,
    "title": "The Matrix",
    "release_date": "1999-03-30",
    "overview": "Neo.",
    "poster_path": "/p.jpg",
    "backdrop_path": "/b.jpg",
    "vote_average": 8.2,
    "genres": [{"id": 1, "name": "Action"}],
    "runtime": 136,
    "external_ids": {"imdb_id": "tt0133093"},
    "credits": {"cast": [{"name": "Keanu Reeves", "character": "Neo", "profile_path": "/k.jpg"}]},
    "images": {"logos": [{"file_path": "/logo.png", "iso_639_1": "en"}]},
}
PROVIDERS = {
    "results": {
        "US": {
            "flatrate": [
                {"provider_id": 8, "provider_name": "Netflix", "logo_path": "/n.png"},
                {"provider_id": 1899, "provider_name": "Max", "logo_path": "/m.png"},
            ],
            "rent": [{"provider_id": 2, "provider_name": "Apple TV", "logo_path": "/a.png"}],
        },
        "GB": {"flatrate": [{"provider_id": 337, "provider_name": "Disney Plus"}]},
    }
}


def mock_tmdb(router: respx.MockRouter) -> None:
    router.get(f"{TMDB}/movie/603").mock(return_value=httpx.Response(200, json=MATRIX))
    router.get(f"{TMDB}/movie/603/watch/providers").mock(return_value=httpx.Response(200, json=PROVIDERS))
    router.get(f"{TMDB}/search/multi").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{**MATRIX, "media_type": "movie"}, {"id": 1, "name": "Person", "media_type": "person"}]},
        )
    )


async def seed_library(api: Api) -> dict[str, str]:
    """A Plex-like library item matched by tmdb id and an Xtream VOD matched by title+year."""
    async with api.c.db.session() as s:
        s.add_all(
            [
                Source(
                    id="plex1",
                    type="plex",
                    name="Home Plex",
                    priority=10,
                    region="US",
                    config_enc=api.c.crypto.encrypt_json({"server_url": "http://plex.local:32400", "token": "t"}),
                    hints={},
                    counts={},
                ),
                Source(
                    id="xt1",
                    type="xtream",
                    name="Panel",
                    priority=20,
                    region="US",
                    config_enc=api.c.crypto.encrypt_json({"server_url": "http://p", "username": "u", "password": "p"}),
                    hints={},
                    counts={},
                ),
                Source(
                    id="xt2",
                    type="xtream",
                    name="Panel 2",
                    priority=30,
                    region="US",
                    config_enc=api.c.crypto.encrypt_json({"server_url": "http://p2", "username": "u", "password": "p"}),
                    hints={},
                    counts={},
                ),
            ]
        )
        await s.flush()
        s.add_all(
            [
                Title(
                    source_id="plex1",
                    ext_id="111",
                    kind="movie",
                    title="The Matrix",
                    norm_title="matrix",
                    year=1999,
                    tmdb_id=603,
                    extra={"part_key": "/library/parts/9/file.mkv", "container": "mkv"},
                ),
                Title(
                    source_id="xt1",
                    ext_id="7",
                    kind="vod",
                    title="EN | The Matrix (1999) [4K]",
                    norm_title=normalise_title("EN | The Matrix (1999) [4K]"),
                    year=1999,
                    extra={"ext": "mp4"},
                ),
                # same title, wrong year → no match
                Title(source_id="xt2", ext_id="8", kind="vod", title="The Matrix", norm_title="matrix", year=2021),
            ]
        )
        await s.commit()
    nf = await api.post(
        "/sources", json={"type": "streaming_service", "name": "Netflix", "service_key": "netflix", "config": {}}
    )
    dp = await api.post(
        "/sources", json={"type": "streaming_service", "name": "Disney+", "service_key": "disney", "config": {}}
    )
    return {"netflix": nf.json()["id"], "disney": dp.json()["id"]}


def test_normalise_title() -> None:
    assert normalise_title("EN | The Matrix (1999) [4K]") == "matrix"
    assert normalise_title("Amélie") == "amelie"
    assert normalise_title("Fast & Furious") == "fast and furious"


async def test_availability_merge_and_ordering(api: Api) -> None:
    with respx.mock(assert_all_called=False) as router:
        mock_tmdb(router)
        router.get(url__startswith="https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(200, json={"results": {"bindings": [{"v": {"value": "20557937"}}]}})
        )
        svc = await seed_library(api)
        await api.put("/settings/tmdb_key", json={"api_key": "k" * 32})

        d = (await api.get("/titles/tmdb:movie:603")).json()
        assert d["title"] == "The Matrix" and d["year"] == 1999 and d["runtime_min"] == 136
        assert d["poster"] == "https://image.tmdb.org/t/p/w342/p.jpg"
        assert d["cast"][0]["name"] == "Keanu Reeves" and d["logo"].endswith("/logo.png")
        assert d["attribution"] == "Availability data by JustWatch"
        srcs = [(b["source_id"], b["playable_in_app"], b["kind"]) for b in d["sources"]]
        # in-app first by priority (plex tmdb match, xtream title+year match), then hand-off;
        # disney (GB-only offer) and xt2 (wrong year) excluded
        assert srcs == [("plex1", True, "library"), ("xt1", True, "vod"), (svc["netflix"], False, "flatrate")]

        # play: default picks first in-app source (Plex direct-play URL with token)
        p = (await api.post("/playback/resolve", json={"id": "tmdb:movie:603", "client": "android_tv"})).json()
        assert p["mode"] == "direct"
        assert p["url"] == "http://plex.local:32400/library/parts/9/file.mkv?X-Plex-Token=t"
        assert p["mime"] == "video/x-matroska"

        # explicit hand-off: Netflix deep link resolved through Wikidata
        p = (await api.post("/playback/resolve", json={"id": "tmdb:movie:603", "source_id": svc["netflix"]})).json()
        assert p["mode"] == "handoff" and p["url"] is None
        h = p["handoff"]
        assert h["android_package"] == "com.netflix.ninja"
        assert h["android_uri"] == "https://www.netflix.com/title/20557937?source=30"
        assert h["web_url"] == "https://www.netflix.com/title/20557937" and h["supports_title_link"] is True

        # search groups: in-app playable → play_here
        res = (await api.get("/search", params={"q": "matrix"})).json()
        play_ids = [t["id"] for t in res["play_here"]]
        assert "tmdb:movie:603" in play_ids
        merged = next(t for t in res["play_here"] if t["id"] == "tmdb:movie:603")
        assert {b["source_id"] for b in merged["sources"]} >= {"plex1"}

        # region change on the service source switches offers (GB has Disney only)
        await api.patch(f"/sources/{svc['disney']}", json={"region": "GB"})
        d = (await api.get("/titles/tmdb:movie:603")).json()
        assert svc["disney"] in [b["source_id"] for b in d["sources"]]


async def test_handoff_only_title_goes_to_open_in_app(api: Api) -> None:
    with respx.mock(assert_all_called=False) as router:
        mock_tmdb(router)
        router.get(url__startswith="https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(200, json={"results": {"bindings": []}})
        )
        nf = await api.post(
            "/sources", json={"type": "streaming_service", "name": "Netflix", "service_key": "netflix", "config": {}}
        )
        await api.put("/settings/tmdb_key", json={"api_key": "k" * 32})
        res = (await api.get("/search", params={"q": "matrix"})).json()
        assert [t["id"] for t in res["open_in_app"]] == ["tmdb:movie:603"]
        assert res["play_here"] == []
        # no netflix id known → falls back to the service's search URL
        p = (await api.post("/playback/resolve", json={"id": "tmdb:movie:603"})).json()
        assert p["mode"] == "handoff" and p["handoff"]["android_uri"] is None
        assert p["handoff"]["web_url"] == "https://www.netflix.com/search?q=The+Matrix"
        assert p["handoff"]["supports_title_link"] is False
        assert nf.status_code == 201


async def test_trending_hero_when_no_sources(api: Api) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{TMDB}/trending/all/week").mock(
            return_value=httpx.Response(200, json={"results": [{**MATRIX, "media_type": "movie"}]})
        )
        router.get(f"{TMDB}/movie/603/watch/providers").mock(return_value=httpx.Response(200, json=PROVIDERS))
        await api.put("/settings/tmdb_key", json={"api_key": "k" * 32})
        home = (await api.get("/home")).json()
        assert [t["id"] for t in home["hero"]] == ["tmdb:movie:603"]
        assert any(r["id"] == "trending" for r in home["rows"])


async def test_tmdb_429_backoff_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    ctx = AdapterContext(
        http_factory=lambda **kw: httpx.AsyncClient(headers=kw.get("headers")), cache=MemoryCache(), data_dir=tmp_path
    )
    tmdb = TmdbClient(ctx, lambda: "key123")
    with respx.mock() as router:
        route = router.get(f"{TMDB}/movie/603").mock(
            side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json=MATRIX)]
        )
        m = await tmdb.details("movie", 603)
        assert m is not None and m.imdb_id == "tt0133093"
        assert sleeps == [2.0]
        assert route.calls[0].request.url.params["api_key"] == "key123"
        await tmdb.details("movie", 603)
        assert route.call_count == 2  # second call served from cache
    assert TmdbClient(ctx, lambda: None).enabled is False


def test_deeplinks_seed_parse() -> None:
    rows = {r.key: r for r in load_seed()}
    assert set(rows) >= {"netflix", "hulu", "prime", "disney", "max", "youtube"}
    assert rows["prime"].tmdb_provider_ids == [9, 119]
    assert rows["hulu"].android_component == ".WKFactivity"
    assert rows["hulu"].android_title_uri == "https://www.hulu.com/watch/{hulu_id}"
    assert rows["peacock"].android_title_uri is None and rows["netflix"].supports_title_link == "true"
    assert parse_deeplinks_md("no table here") == []


async def test_disney_wikidata_lookup_cached(tmp_path: Path) -> None:
    rows = {r.key: r for r in load_seed()}

    async def loader(key: str) -> Any:
        return rows.get(key)

    ctx = AdapterContext(http_factory=lambda **kw: httpx.AsyncClient(), cache=MemoryCache(), data_dir=tmp_path)
    reg = DeeplinkRegistry(ctx, loader)
    with respx.mock() as router:
        route = router.get(url__startswith="https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(200, json={"results": {"bindings": [{"v": {"value": "abc-123"}}]}})
        )
        link = await reg.link_for("disney", tmdb_type="tv", tmdb_id=1399, title="X")
        assert link is not None
        assert link.android_uri == "https://www.disneyplus.com/browse/entity-abc-123"
        q = route.calls[0].request.url.params["query"]
        assert "P4983" in q and "P13902" in q and '"1399"' in q
        await reg.link_for("disney", tmdb_type="tv", tmdb_id=1399, title="X")
        assert route.call_count == 1
        prime = await reg.link_for("prime", tmdb_type="movie", tmdb_id=1, title="Some Film")
        assert prime is not None and prime.android_uri == "https://app.primevideo.com"
        assert "phrase=Some+Film" in prime.web_url
    assert await reg.link_for("unknown", tmdb_type=None, tmdb_id=None, title="x") is None


async def test_services_registry_endpoint(api: Api) -> None:
    rows = (await api.get("/services/registry")).json()
    nf = next(r for r in rows if r["service_key"] == "netflix")
    assert nf == {
        "service_key": "netflix",
        "name": "Netflix",
        "android_package": "com.netflix.ninja",
        "web_url": "https://www.netflix.com",
        "supports_title_link": True,
        "tmdb_provider_ids": [8],
    }
