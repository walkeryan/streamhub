"""Artwork of sources that clients cannot reach (proxy: always) is served by the hub."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx
import respx

from app.infra.image_cache import ImageCache
from app.services.auth import Principal
from app.services.content_policy import active_principal
from app.services.image_grants import image_payload
from tests.conftest import Api
from tests.test_presentation_sync import MANAGER, add_xtream, panel

POSTER = f"{MANAGER}/images/poster-7.jpg"
BACKDROP = f"{MANAGER}/images/backdrop-7.jpg"
LOGO = f"{MANAGER}/images/logo-100.png"
PNG = b"\x89PNG\r\n\x1a\n-fixture-poster"


def catalog() -> Any:
    return panel(
        get_vod_categories=[{"category_id": "1", "category_name": "EN - NEW RELEASE"}],
        get_vod_streams=[
            {"stream_id": 7, "name": "EN - The Mill  (2023)", "category_id": "1", "stream_icon": POSTER},
        ],
        get_vod_info={"info": {"backdrop_path": [BACKDROP]}},
        get_live_categories=[{"category_id": "20", "category_name": "US| SPORT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ"}],
        get_live_streams=[{"stream_id": 100, "name": "Sport One", "category_id": "20", "stream_icon": LOGO}],
    )


def image_path(url: str) -> str:
    """Path of a hub image URL, asserting that it is one."""
    path = urlsplit(url).path
    assert path.startswith("/api/v1/images/"), url
    return path


@respx.mock
async def test_proxied_source_artwork_goes_through_the_hub(api: Api) -> None:
    respx.get(f"{MANAGER}/player_api.php").mock(side_effect=catalog())
    poster = respx.get(POSTER).mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    respx.get(BACKDROP).mock(
        return_value=httpx.Response(200, content=b"backdrop", headers={"content-type": "image/webp"})
    )
    respx.get(LOGO).mock(return_value=httpx.Response(200, content=b"logo", headers={"content-type": "image/png"}))
    sid = await add_xtream(api, proxy="always", user_agent="FixturePlayer/1.0")

    tile = (await api.get("/browse/movies", params={"language": "all"})).json()["tiles"][0]
    detail = (await api.get(f"/titles/lib:{sid}:7")).json()
    assert MANAGER not in repr(tile) and MANAGER not in repr(detail)
    assert image_path(detail["poster"]) == image_path(tile["poster"])

    # No bearer token: an <img> tag cannot send one, the signed URL is the grant.
    first = await api.client.get(image_path(tile["poster"]))
    assert first.status_code == 200 and first.content == PNG
    assert first.headers["content-type"] == "image/png"
    assert first.headers["cache-control"] == "public, max-age=86400"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert poster.calls[0].request.headers["user-agent"] == "FixturePlayer/1.0"
    again = await api.client.get(image_path(tile["poster"]))
    assert (again.content, again.headers["content-type"]) == (PNG, "image/png")
    assert poster.call_count == 1  # second response came from the disk cache
    cached = list((api.c.settings.cache_dir / "images").iterdir())
    assert len(cached) == 1 and "poster" not in cached[0].name

    backdrop = await api.client.get(image_path(detail["backdrop"]))
    assert backdrop.content == b"backdrop" and backdrop.headers["content-type"] == "image/webp"

    channel = (await api.get("/channels", params={"language": "all"})).json()["channels"][0]
    logo = await api.client.get(image_path(channel["logo"]))
    assert logo.content == b"logo"
    favorite = await api.put("/favorites", json={"kind": "channel", "id": channel["id"]})
    assert favorite.status_code == 204, favorite.text
    channel_tile = (await api.get("/favorites")).json()[0]
    assert image_path(channel_tile["logo"]) == image_path(channel["logo"])
    assert image_path(channel_tile["poster"]) == image_path(channel["logo"])

    # The player gets the same treatment: nothing in a playback descriptor points at the source.
    for ref in (channel["id"], f"lib:{sid}:7"):
        playing = await api.post("/playback/resolve", json={"id": ref, "allow_unverified_audio": True})
        assert playing.status_code == 200, playing.text
        assert MANAGER not in repr(playing.json())
        image_path(playing.json()["poster"])


@respx.mock
async def test_other_sources_keep_their_artwork_urls(api: Api) -> None:
    respx.get(f"{MANAGER}/player_api.php").mock(side_effect=catalog())
    sid = await add_xtream(api)
    tile = (await api.get("/browse/movies", params={"language": "all"})).json()["tiles"][0]
    detail = (await api.get(f"/titles/lib:{sid}:7")).json()
    channel = (await api.get("/channels", params={"language": "all"})).json()["channels"][0]
    assert (tile["poster"], detail["poster"], detail["backdrop"], channel["logo"]) == (POSTER, POSTER, BACKDROP, LOGO)

    # A grant minted while the source was proxied stops working once it no longer is.
    token = api.c.crypto.sign_json({"s": sid, "u": POSTER})
    assert (await api.client.get(f"/api/v1/images/{token}")).status_code == 404


@respx.mock
async def test_proxied_artwork_only_serves_images(api: Api) -> None:
    respx.get(f"{MANAGER}/player_api.php").mock(side_effect=catalog())
    page = respx.get(POSTER).mock(
        return_value=httpx.Response(200, content=b"<script>1</script>", headers={"content-type": "text/html"})
    )
    await add_xtream(api, proxy="always")
    tile = (await api.get("/browse/movies", params={"language": "all"})).json()["tiles"][0]
    # Provider-controlled markup must never be served from the hub's own origin.
    assert (await api.client.get(image_path(tile["poster"]))).status_code == 404
    assert page.call_count == 1 and not list((api.c.settings.cache_dir / "images").glob("*"))

    for bad in ("file:///etc/hostname", "ftp://manager.example.test/poster.jpg"):
        row_tile = api.c.catalog.image_fn(await api.c.sources.rows_by_id())
        source = next(iter((await api.c.sources.rows_by_id()).values()))
        assert row_tile(source, bad, None) == bad  # not a web URL: left alone, never fetched by the hub
        token = api.c.crypto.sign_json({"s": source.id, "u": bad})
        assert (await api.client.get(f"/api/v1/images/{token}")).status_code == 404


def test_channel_artwork_grants_bind_to_the_channel_under_content_policy() -> None:
    policy = SimpleNamespace(enabled=True, applies_to=lambda principal: True)
    token = active_principal.set(Principal("kid", False, True, 4, "kid-session"))
    try:
        payload = image_payload("panel", LOGO, "ch:panel:100", policy)
        assert payload is not None and payload["ref"] == "ch:panel:100"
        assert image_payload("panel", LOGO, "ch:other:100", policy) is None
    finally:
        active_principal.reset(token)


async def test_image_cache_keeps_type_and_evicts_oldest_first(tmp_path: Path) -> None:
    cache = ImageCache(tmp_path / "images", max_bytes=250)
    assert await cache.get("panel", "http://manager.example.test/a.jpg") is None
    for age, name in enumerate(("a", "b", "c")):
        await cache.put("panel", f"http://manager.example.test/{name}.jpg", name.encode() * 100, f"image/{name}")
        for path in (tmp_path / "images").iterdir():  # make write order unambiguous for the filesystem
            stamp = path.stat().st_mtime - 10 * (3 - age)
            os.utime(path, (stamp, stamp))
    assert await cache.get("panel", "http://manager.example.test/a.jpg") is None  # oldest went first
    assert await cache.get("panel", "http://manager.example.test/b.jpg") == (b"b" * 100, "image/b")
    assert await cache.get("panel", "http://manager.example.test/c.jpg") == (b"c" * 100, "image/c")
    assert await cache.get("other", "http://manager.example.test/c.jpg") is None  # keyed per source
    assert sum(p.stat().st_size for p in (tmp_path / "images").iterdir()) <= 250

    await cache.put("panel", "http://manager.example.test/huge.jpg", b"x" * 300, "image/jpeg")
    assert await cache.get("panel", "http://manager.example.test/huge.jpg") is None  # larger than the whole cap
    disabled = ImageCache(tmp_path / "off", max_bytes=0)
    await disabled.put("panel", "http://manager.example.test/a.jpg", b"a", "image/jpeg")
    assert await disabled.get("panel", "http://manager.example.test/a.jpg") is None
    assert not (tmp_path / "off").exists()


def test_image_cache_cap_is_a_setting(tmp_path: Path) -> None:
    from app.infra.settings import AppSettings

    assert AppSettings(config_dir=tmp_path).image_cache_max_mb == 512
    assert AppSettings(config_dir=tmp_path, image_cache_max_mb=8).image_cache_max_mb == 8
