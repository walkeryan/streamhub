from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.domain.entities import PlaybackMode
from app.infra.models import Channel, Programme, Title
from app.services.playback import decide_mode
from tests.conftest import FIXTURES, Api, add_m3u

BASE = dict(
    client="web",
    url="https://cdn/x.m3u8",
    mime="application/x-mpegURL",
    live=True,
    headers={},
    source_policy="auto",
    global_mode="auto",
    https_site=False,
    max_connections=None,
    active_on_source=0,
)


def mode(**kw: Any) -> PlaybackMode:
    return decide_mode(**{**BASE, **kw})  # type: ignore[arg-type]


def test_decide_mode_matrix() -> None:
    assert mode() == PlaybackMode.DIRECT
    # mixed content: http stream on https site → proxy (web only)
    assert mode(url="http://cdn/x.ts", https_site=True) == PlaybackMode.PROXY
    assert mode(url="http://cdn/x.ts", https_site=True, client="android_tv") == PlaybackMode.DIRECT
    # headers browsers cannot send
    assert mode(headers={"User-Agent": "X"}) == PlaybackMode.PROXY
    assert mode(headers={"User-Agent": "X"}, client="android_tv") == PlaybackMode.DIRECT
    # CORS-blocked: raw TS live → HLS remux; other → passthrough
    assert mode(mime="video/mp2t", cors_ok=False) == PlaybackMode.HLS
    assert mode(cors_ok=False) == PlaybackMode.PROXY
    # policies
    assert mode(source_policy="always") == PlaybackMode.PROXY
    assert mode(source_policy="always", client="android_tv") == PlaybackMode.PROXY
    assert mode(global_mode="always") == PlaybackMode.PROXY
    assert mode(source_policy="hls") == PlaybackMode.HLS
    assert mode(global_mode="never", url="http://x", https_site=True) == PlaybackMode.DIRECT
    # single-connection source already in use → shared proxy, any client
    assert mode(client="android_tv", max_connections=1, active_on_source=1) == PlaybackMode.PROXY
    assert mode(client="android_tv", max_connections=1, active_on_source=0) == PlaybackMode.DIRECT


async def channels(api: Api) -> list[dict[str, Any]]:
    r = await api.get("/channels", params={"language": "all"})
    assert r.status_code == 200
    out: list[dict[str, Any]] = r.json()["channels"]
    return out


async def test_channels_languages_groups(api: Api) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u")
    chs = await channels(api)
    by_name = {c["name"]: c for c in chs}
    assert by_name["BBC One HD"]["language"] == "eng" and by_name["BBC One HD"]["language_source"] == "tag"
    assert by_name["TF1"]["language"] == "fra" and by_name["TF1"]["language_source"] == "country"
    assert by_name["MBC 1"]["language"] == "ara" and by_name["MBC 1"]["language_source"] == "group"
    assert by_name["Radio España 🇪🇸"]["language_source"] == "flag"
    assert [c["number"] for c in chs] == sorted(c["number"] for c in chs)
    langs = {x["code"]: x for x in (await api.get("/channels/languages")).json()}
    assert langs["eng"]["count"] == 1 and langs["eng"]["name"] == "English"

    # language filter + profile default languages
    r = await api.get("/channels", params={"language": "fra,ara"})
    assert {c["name"] for c in r.json()["channels"]} == {"TF1", "MBC 1", "ARABIC NEWS 24"}
    await api.patch(f"/profiles/{api.profile_id}", json={"languages": ["fr"]})
    r = await api.get("/channels")
    assert [c["name"] for c in r.json()["channels"]] == ["TF1"]
    await api.patch(f"/profiles/{api.profile_id}", json={"languages": []})

    # per-group language override + hide
    groups = {g["name"]: g for g in (await api.get("/channels/groups")).json()}
    misc = groups["Misc"]
    r = await api.patch(f"/channels/groups/{misc['id']}", json={"language_override": "tr"})
    assert r.json()["language_override"] == "tur"
    by_name = {c["name"]: c for c in await channels(api)}
    assert by_name["ARABIC NEWS 24"]["language"] == "tur" and by_name["ARABIC NEWS 24"]["language_source"] == "user"
    await api.patch(f"/channels/groups/{misc['id']}", json={"language_override": None, "hidden": True})
    names = {c["name"] for c in await channels(api)}
    assert "ARABIC NEWS 24" not in names
    # explicit group query still shows hidden group
    r = await api.get("/channels", params={"group": misc["id"], "language": "all"})
    assert [c["name"] for c in r.json()["channels"]] == ["ARABIC NEWS 24"]
    assert r.json()["channels"][0]["language"] == "ara"


async def test_channel_overrides_survive_resync(api: Api) -> None:
    src = await add_m3u(api, FIXTURES / "sample.m3u")
    tf1 = next(c for c in await channels(api) if c["name"] == "TF1")
    r = await api.patch(f"/channels/{tf1['id']}", json={"number": 42, "language": "de", "epg_id": "custom.epg"})
    assert r.status_code == 200, r.text
    assert (r.json()["number"], r.json()["language"], r.json()["epg_id"]) == (42, "deu", "custom.epg")
    await api.c.sync.sync_source(src["id"])
    tf1 = next(c for c in await channels(api) if c["name"] == "TF1")
    assert (tf1["number"], tf1["language"], tf1["language_source"], tf1["epg_id"]) == (42, "deu", "user", "custom.epg")
    r = await api.patch(f"/channels/{tf1['id']}", json={"language": None})
    assert r.json()["language"] == "fra"


async def test_channel_numbering_offset_per_source(api: Api) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u", name="A")
    await add_m3u(api, FIXTURES / "sample.m3u", name="B")
    chs = await channels(api)
    nums = sorted(c["number"] for c in chs)
    assert nums[:7] == list(range(1, 8)) and nums[7:] == list(range(1001, 1008))


async def test_list_id_returns_same_order(api: Api) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u")
    r = await api.get("/channels", params={"language": "eng,fra,ara"})
    body = r.json()
    again = await api.get("/channels", params={"list_id": body["list_id"]})
    assert [c["id"] for c in again.json()["channels"]] == [c["id"] for c in body["channels"]]
    assert (await api.get("/channels", params={"list_id": "nope"})).status_code == 404


async def test_playback_direct_proxy_hls_and_channel_context(api: Api) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u")
    chs = await channels(api)
    bbc = next(c for c in chs if c["name"] == "BBC One HD")
    tf1 = next(c for c in chs if c["name"] == "TF1")

    # direct on http site with no headers
    d = (await api.post("/playback/resolve", json={"id": bbc["id"], "client": "web"})).json()
    assert d["mode"] == "direct" and d["url"] == "http://streams.example/live/bbc1.m3u8"
    assert d["mime"] == "application/x-mpegURL" and d["live"] is True
    ctx = d["channel_context"]
    assert ctx["count"] == len(chs) and 0 <= ctx["index"] < ctx["count"]
    listed = (await api.get("/channels", params={"list_id": ctx["list_id"]})).json()["channels"]
    assert listed[ctx["index"]]["id"] == bbc["id"]

    # https deployment → proxy via signed token
    d = (await api.post("/playback/resolve", json={"id": bbc["id"]}, headers={"X-Forwarded-Proto": "https"})).json()
    assert d["mode"] == "proxy" and d["url"].startswith("https://hub.test/api/v1/stream/") and d["headers"] == {}

    # EXTVLCOPT headers: web → proxy, android_tv → direct with headers
    d = (await api.post("/playback/resolve", json={"id": tf1["id"]})).json()
    assert d["mode"] == "proxy"
    d = (await api.post("/playback/resolve", json={"id": tf1["id"], "client": "android_tv"})).json()
    assert d["mode"] == "direct" and d["headers"]["User-Agent"] == "SuperPlayer/2.0"

    # global proxy_mode=always / source hls policy
    await api.patch("/settings", json={"proxy_mode": "always"})
    d = (await api.post("/playback/resolve", json={"id": bbc["id"], "client": "android_tv"})).json()
    assert d["mode"] == "proxy"
    await api.patch("/settings", json={"proxy_mode": "auto"})
    await api.patch(f"/sources/{bbc['source_id']}", json={"config": {"proxy": "hls"}})
    d = (await api.post("/playback/resolve", json={"id": bbc["id"]})).json()
    assert d["mode"] == "hls" and d["url"].endswith("/index.m3u8") and d["mime"] == "application/x-mpegURL"


async def test_stream_token_validation(api: Api) -> None:
    r = await api.client.get("/api/v1/stream/not-a-token")
    assert r.status_code == 401


async def test_shared_upstream_fanout_and_linger(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    """One upstream connection serves N clients; it closes LINGER_S after the last one leaves.

    Tested on StreamProxy directly: httpx's ASGITransport buffers whole responses, so an endless
    live body cannot be consumed through the test client.
    """
    import asyncio

    from app.proxy import stream_proxy

    monkeypatch.setattr(stream_proxy, "LINGER_S", 0.05)
    payload = b"\x47" * 188 * 50
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("http://up.example/live.ts").mock(
            return_value=httpx.Response(200, content=payload, headers={"content-type": "video/mp2t"})
        )
        proxy = api.c.proxy
        ctype, g1 = await proxy.live_shared("src:ch1", "http://up.example/live.ts", {"User-Agent": "UA"}, "src")
        _, g2 = await proxy.live_shared("src:ch1", "http://up.example/live.ts", {"User-Agent": "UA"}, "src")
        assert ctype == "video/mp2t"
        c1 = await g1.__anext__()
        c2 = await g2.__anext__()
        assert c1[:1] == c2[:1] == b"\x47"
        assert proxy.stats["shared_upstreams"] == {"src:ch1": 2}
        assert route.calls[0].request.headers["user-agent"] == "UA"
        first_calls = route.call_count
        assert api.c.active.count("src") == 2
        await g1.aclose()  # type: ignore[attr-defined]
        await g2.aclose()  # type: ignore[attr-defined]
        assert api.c.active.count("src") == 0
        await asyncio.sleep(0.3)
        assert "src:ch1" not in proxy.stats["shared_upstreams"]
        assert first_calls >= 1


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_proxy_vod_passthrough_and_hls_rewrite(api: Api, respx_mock: respx.MockRouter) -> None:
    from app.services.playback import StreamGrant

    respx_mock.route(host="hub.test").pass_through()
    await add_m3u(api, FIXTURES / "sample.m3u")
    bbc = next(c for c in await channels(api) if c["name"] == "BBC One HD")
    seen: dict[str, str] = {}

    def mp4(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(
            206, content=b"0123456789", headers={"content-type": "video/mp4", "content-range": "bytes 0-9/100"}
        )

    respx_mock.get("http://vod.example/film.mp4").mock(side_effect=mp4)
    token = api.c.tokens.issue(
        StreamGrant(
            profile_id=api.profile_id,
            source_id=bbc["source_id"],
            url="http://vod.example/film.mp4",
            headers={"Referer": "http://r/"},
            mode="proxy",
            live=False,
            channel_key=None,
            transcode=False,
        )
    )
    r = await api.client.get(f"/api/v1/stream/{token}", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == b"0123456789"
    assert r.headers["content-range"] == "bytes 0-9/100"
    assert seen["referer"] == "http://r/" and seen["range"] == "bytes=0-9"

    respx_mock.get("http://streams.example/live/bbc1.m3u8").mock(
        return_value=httpx.Response(200, text="#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nseg1.ts\n")
    )
    respx_mock.get("http://streams.example/live/seg1.ts").mock(
        return_value=httpx.Response(200, content=b"\x47" * 188, headers={"content-type": "video/mp2t"})
    )
    d = (await api.post("/playback/resolve", json={"id": bbc["id"]}, headers={"X-Forwarded-Proto": "https"})).json()
    r = await api.client.get(d["url"].split("hub.test", 1)[1])
    assert r.status_code == 200
    seg = next(ln for ln in r.text.splitlines() if ln and not ln.startswith("#"))
    assert seg.startswith("/api/v1/stream/") and "/seg/" in seg and "streams.example" not in seg
    r = await api.client.get(seg)
    assert r.status_code == 200 and r.content == b"\x47" * 188


async def test_catchup_playback_and_epg_grid(api: Api) -> None:
    src = await add_m3u(api, FIXTURES / "sample.m3u")
    tf1 = next(c for c in await channels(api) if c["name"] == "TF1")
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    async with api.c.db.session() as s:
        s.add_all(
            [
                Programme(
                    epg_channel="tf1.fr",
                    feed=src["id"],
                    start=now - timedelta(hours=2),
                    stop=now - timedelta(hours=1),
                    title="Past Show",
                ),
                Programme(
                    epg_channel="tf1.fr",
                    feed=src["id"],
                    start=now - timedelta(minutes=30),
                    stop=now + timedelta(minutes=30),
                    title="Now Show",
                ),
                Programme(
                    epg_channel="tf1.fr",
                    feed=src["id"],
                    start=now + timedelta(minutes=30),
                    stop=now + timedelta(minutes=90),
                    title="Next Show",
                ),
            ]
        )
        await s.commit()
    grid = (
        await api.get(
            "/epg",
            params={
                "channel_ids": tf1["id"],
                "from": (now - timedelta(hours=3)).isoformat(),
                "to": (now + timedelta(hours=2)).isoformat(),
            },
        )
    ).json()
    progs = grid[tf1["id"]]
    assert [p["title"] for p in progs] == ["Past Show", "Now Show", "Next Show"]
    assert progs[0]["catchup_available"] and not progs[1]["catchup_available"]
    ch = next(c for c in await channels(api) if c["name"] == "TF1")
    assert ch["now"]["title"] == "Now Show" and ch["next"]["title"] == "Next Show"

    d = (
        await api.post(
            "/playback/resolve", json={"id": tf1["id"], "client": "android_tv", "catchup_programme_id": progs[0]["id"]}
        )
    ).json()
    start = int((now - timedelta(hours=2)).timestamp())
    assert d["url"] == f"http://streams.example/tf1/{start}-3600.ts" and d["live"] is False
    assert d["subtitle"] == "Past Show" and d["duration_ms"] == 3_600_000


async def test_playback_progress_resume_and_watched(api: Api) -> None:
    src = await add_m3u(api, FIXTURES / "sample.m3u")
    async with api.c.db.session() as s:
        s.add(
            Title(
                source_id=src["id"],
                ext_id="vod1",
                kind="vod",
                title="Film",
                norm_title="film",
                duration_ms=6_000_000,
                extra={"stream_url": "http://streams.example/vod/1.mp4"},
            )
        )
        await s.commit()
    lib = f"lib:{src['id']}:vod1"
    # m3u adapter cannot play titles; register a fake playback provider via the registry contract
    from app.domain.entities import StreamTarget

    class FakePlayable:
        async def stream_for_item(self, item: Any) -> StreamTarget:
            return StreamTarget(url=item.stream_url, mime="video/mp4", duration_ms=item.duration_ms)

    real = api.c.sources.provider

    def provider(row: Any) -> Any:
        p = real(row)
        if not hasattr(p, "stream_for_item"):
            p.stream_for_item = FakePlayable().stream_for_item  # type: ignore[attr-defined]
        return p

    api.c.sources.provider = provider  # type: ignore[method-assign]
    assert (
        await api.post(
            "/playback/progress", json={"id": lib, "position_ms": 120_000, "duration_ms": 6_000_000, "state": "paused"}
        )
    ).status_code == 204
    d = (await api.post("/playback/resolve", json={"id": lib})).json()
    assert d["mode"] == "direct" and d["resume_position_ms"] == 120_000 and d["mime"] == "video/mp4"
    hist = (await api.get("/history")).json()
    assert hist[0]["id"] == lib and hist[0]["progress_pct"] == 2.0
    # stopped at end → watched
    await api.post(
        "/playback/progress", json={"id": lib, "position_ms": 6_000_000, "duration_ms": 6_000_000, "state": "stopped"}
    )
    d = (await api.post("/playback/resolve", json={"id": lib})).json()
    assert d["resume_position_ms"] == 0
    assert (await api.get("/history")).json()[0]["progress_pct"] == 100.0


async def test_favorites_any_kind_and_reorder(api: Api) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u")
    chs = await channels(api)
    a, b = chs[0]["id"], chs[1]["id"]
    assert (await api.put("/favorites", json={"kind": "channel", "id": a})).status_code == 204
    assert (await api.put("/favorites", json={"kind": "channel", "id": b})).status_code == 204
    assert (await api.put("/favorites", json={"kind": "movie", "id": "tmdb:movie:603"})).status_code == 204
    assert (await api.put("/favorites", json={"kind": "bogus", "id": a})).status_code == 422
    assert (await api.put("/favorites", json={"kind": "movie", "id": "garbage"})).status_code == 400
    favs = (await api.get("/favorites")).json()
    # tmdb title without TMDB key or library match cannot be resolved → omitted
    assert [f["id"] for f in favs] == [a, b] and all(f["is_favorite"] for f in favs)
    await api.post(
        "/favorites/reorder", json=[{"kind": "channel", "id": b, "sort": 0}, {"kind": "channel", "id": a, "sort": 1}]
    )
    assert [f["id"] for f in (await api.get("/favorites")).json()] == [b, a]
    r = await api.get("/channels", params={"favorites": "true", "language": "all"})
    assert {c["id"] for c in r.json()["channels"]} == {a, b}
    assert (await api.delete("/favorites", params={"kind": "channel", "id": a})).status_code == 204
    assert [f["id"] for f in (await api.get("/favorites")).json()] == [b]
    home = (await api.get("/home")).json()
    on_now = next(r for r in home["rows"] if r["id"] == "on_now")
    assert [t["id"] for t in on_now["tiles"]] == [b]


async def test_watchlist_and_home_layout(api: Api) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u")
    ch = (await channels(api))[0]
    await api.put("/watchlist", json={"id": ch["id"]})
    wl = (await api.get("/watchlist")).json()
    assert [t["id"] for t in wl] == [ch["id"]]
    layout = (await api.get(f"/profiles/{api.profile_id}/home_layout")).json()["rows"]
    ids = [r["id"] for r in layout]
    assert ids[:2] == ["continue_watching", "on_now"]
    new = [{"id": "watchlist", "sort": 1}, {"id": "on_now", "hidden": True, "sort": 2}]
    r = await api.put(f"/profiles/{api.profile_id}/home_layout", json={"rows": new})
    rows = r.json()["rows"]
    assert rows[0]["id"] == "watchlist" and next(x for x in rows if x["id"] == "on_now")["hidden"]
    home = (await api.get("/home")).json()
    row_ids = [r["id"] for r in home["rows"]]
    assert row_ids[0] == "watchlist" and "on_now" not in row_ids
    await api.delete("/watchlist", params={"id": ch["id"]})
    assert (await api.get("/watchlist")).json() == []


async def test_search_channels_group(api: Api) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u")
    res = (await api.get("/search", params={"q": "bbc"})).json()
    assert [c["name"] for c in res["channels"]] == ["BBC One HD"]
    assert res["attribution"] == "Availability data by JustWatch"
    assert res["play_here"] == [] and res["open_in_app"] == []


async def test_themes_builtin_and_custom(api: Api) -> None:
    themes = (await api.client.get("/api/v1/themes")).json()
    ids = [t["id"] for t in themes]
    assert ids[:5] == ["midnight", "oled", "light", "contrast", "adaptive"]
    adaptive = themes[4]
    assert adaptive["tokens"]["bg"] == "#0B0F17" and adaptive["tokens"]["accent"] == "dynamic:poster-dominant"
    r = await api.post("/themes", json={"name": "Mine", "tokens": {"extends": "oled", "accent": "#FF00AA"}})
    assert r.status_code == 201 and r.json()["tokens"]["bg"] == "#000000"
    assert (await api.post("/themes", json={"name": "Bad", "tokens": {"accent": "javascript:x"}})).status_code == 400
    assert (await api.post("/themes", json={"id": "midnight", "name": "X", "tokens": {}})).status_code == 409
    assert (await api.patch(f"/profiles/{api.profile_id}", json={"theme_id": r.json()["id"]})).status_code == 200


async def test_settings(api: Api) -> None:
    s = (await api.get("/settings")).json()
    assert s == {
        "tmdb_api_key_set": False,
        "region": "US",
        "sleep_timer_min": None,
        "epg_refresh_hours": 12,
        "transcode_hw": "none",
        "proxy_mode": "auto",
    }
    s = (await api.patch("/settings", json={"region": "gb", "sleep_timer_min": 60, "transcode_hw": "vaapi"})).json()
    assert s["region"] == "GB" and s["sleep_timer_min"] == 60 and s["transcode_hw"] == "vaapi"
    assert (await api.patch("/settings", json={"proxy_mode": "sometimes"})).status_code == 422
    assert (await api.put("/settings/tmdb_key", json={"api_key": "abc123secret"})).status_code == 204
    assert (await api.get("/settings")).json()["tmdb_api_key_set"] is True
    from app.infra.models import Setting

    async with api.c.db.session() as sess:
        row = (await sess.execute(select(Setting).where(Setting.key == "tmdb_api_key_enc"))).scalar_one()
    assert "abc123secret" not in str(row.value)


async def test_resync_removes_vanished_channels(api: Api, tmp_path: Any) -> None:
    src = await add_m3u(api, FIXTURES / "sample.m3u")
    row = await api.c.sources.get(src["id"])
    cfg = api.c.sources.decrypt_config(row)
    path = api.c.settings.playlist_dir / cfg["file"]
    path.write_text('#EXTM3U\n#EXTINF:-1 group-title="Only",Solo\nhttp://x/solo.ts\n')
    await api.c.sync.sync_source(src["id"])
    async with api.c.db.session() as s:
        names = list((await s.execute(select(Channel.name))).scalars())
    assert names == ["Solo"]
    groups = (await api.get("/channels/groups")).json()
    assert [g["name"] for g in groups] == ["Only"]


@pytest.mark.parametrize("client", ["web", "android_tv"])
async def test_unknown_ids(api: Api, client: str) -> None:
    assert (await api.post("/playback/resolve", json={"id": "nope", "client": client})).status_code == 404
    assert (await api.post("/playback/resolve", json={"id": "ch:x:y", "client": client})).status_code == 404
    assert (await api.get("/titles/lib:x:y")).status_code == 404
