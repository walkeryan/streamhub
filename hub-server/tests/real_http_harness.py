"""Local-only real HTTP browser fixture. Never points at production/provider data."""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from app.container import build_container
from app.infra.cache import MemoryCache
from app.infra.models import Channel, ChannelGroup, DisclaimerAcceptance, Profile, Programme, Progress, Source, Title
from app.infra.settings import AppSettings
from app.main import create_app

MOVIES: dict[int, dict[str, Any]] = {
    11: {"id": 11, "title": "Star Wars", "release_date": "1977-05-25", "popularity": 100},
    140607: {"id": 140607, "title": "Star Wars: The Force Awakens", "release_date": "2015-12-15", "popularity": 80},
    330459: {"id": 330459, "title": "Rogue One: A Star Wars Story", "release_date": "2016-12-14", "popularity": 70},
    900001: {"id": 900001, "title": "Star Wars Parody Fixture", "release_date": "2020-01-01", "popularity": 1},
}


def outbound(request: httpx.Request) -> httpx.Response:
    """All upstream exchanges terminate in this process. Unknown requests fail."""
    path = request.url.path
    if request.url.host == "api.themoviedb.org":
        if path == "/3/search/multi":
            q = request.url.params.get("query", "").lower()
            results = (
                [{**v, "media_type": "movie", "genres": [{"name": "Adventure"}]} for v in MOVIES.values()]
                if "star" in q
                else []
            )
            return httpx.Response(200, json={"results": list(reversed(results))})
        if path.endswith("/watch/providers"):
            return httpx.Response(200, json={"results": {}})
        if path == "/3/trending/all/week":
            return httpx.Response(200, json={"results": []})
        if path.startswith("/3/movie/") and path.split("/")[-1].isdigit():
            movie = MOVIES.get(int(path.split("/")[-1]))
            return httpx.Response(
                200 if movie else 404, json={**movie, "genres": [{"name": "Adventure"}]} if movie else {}
            )
        if path.endswith("/videos"):
            return httpx.Response(200, json={"results": []})
    if request.url.host == "iptv-org.invalid":
        return httpx.Response(200, json=[])
    if request.url.host == "127.0.0.1" and path == "/player_api.php":
        action = request.url.params.get("action")
        if action == "get_vod_info":
            return httpx.Response(200, json={"info": {}})
        if action == "get_series_info":
            return httpx.Response(200, json={"episodes": {}})
    raise AssertionError(f"Unexpected outbound fixture request: {request.method} {request.url.host}{path}")


def http_factory(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(outbound), **kwargs)


async def seed(c: Any, base: str) -> None:
    now = datetime.now(UTC)
    async with c.db.session() as s:
        s.add_all(
            [
                Profile(id="fixture-english", name="Fixture English", languages=["eng"]),
                Profile(id="fixture-french", name="Fixture French", languages=["fra"]),
                Profile(id="fixture-clean", name="Fixture Clean", languages=["eng"]),
            ]
        )
        await s.flush()
        s.add_all(
            [
                DisclaimerAcceptance(profile_id=p, version=c.disclaimer.version)
                for p in ("fixture-english", "fixture-french", "fixture-clean")
            ]
        )
        s.add(
            Source(
                id="fixture-iptv",
                name="Fixture IPTV",
                type="xtream",
                enabled=True,
                config_enc=c.crypto.encrypt_json(
                    {
                        "server_url": base,
                        "username": "fixture",
                        "password": "fixture",
                        "proxy_policy": "never",
                    }
                ),
            )
        )
        await s.flush()
        for ext, title, tmdb, group, extra in [
            ("en", "EN-NETFLIX-force-awakens", 140607, "English", {}),
            (
                "fr",
                "FR-NETFLIX-force-awakens",
                140607,
                "French",
                {"audio_tracks": [{"language": "fra", "default": True}], "audio_evidence": "provider_audio_tracks"},
            ),
            ("newhope", "EN - STAR WARS (1977)", 11, "English", {}),
            ("parody", "Star Wars Parody Fixture", 900001, "English", {}),
        ]:
            s.add(
                Title(
                    source_id="fixture-iptv",
                    ext_id=ext,
                    kind="vod",
                    title=title,
                    norm_title=title.lower(),
                    tmdb_id=tmdb,
                    year=int(MOVIES[tmdb]["release_date"][:4]),
                    group_name=group,
                    genres=["Adventure"],
                    extra={"ext": "mp4", "stream_id": ext, **extra},
                )
            )
        s.add(ChannelGroup(id="fixture-sports", source_id="fixture-iptv", name="English Sports"))
        await s.flush()
        s.add(
            Channel(
                source_id="fixture-iptv",
                ext_id="live",
                name="Fixture Sports",
                norm_name="fixture sports",
                group_id="fixture-sports",
                group_name="English Sports",
                language="eng",
                language_source="group",
                epg_id="fixture.sports",
                number=1,
                data={"ext": "ts"},
            )
        )
        s.add(
            Programme(
                feed="fixture-iptv",
                epg_channel="fixture.sports",
                title="NBA: Fixture A vs Fixture B",
                category="Basketball",
                start=now - timedelta(minutes=15),
                stop=now + timedelta(hours=2),
            )
        )
        s.add(
            Progress(
                profile_id="fixture-french", ref_id="lib:fixture-iptv:fr", position_ms=600_000, duration_ms=7_200_000
            )
        )
        await s.commit()


def fixture_app(root: Path, dist: Path, base: str) -> FastAPI:
    settings = AppSettings(
        config_dir=root / "config",
        web_dist=dist,
        transcode_dir=root / "transcode",
        dvr_enabled=False,
        dvr_dir=root / "dvr",
        dvr_reserve_dir=root / "dvr-reserve",
        secret_key="fixture-only-not-a-production-secret",
        admin_password="fixture-only-admin-password",
        google_auth_required=False,
        google_client_id="",
        google_client_secret="",
        google_allowed_emails="",
        tmdb_api_key="fixture-only-metadata-key",
        database_url=f"sqlite+aiosqlite:///{root / 'fixture.db'}",
        enable_scheduler=False,
        iptv_org_channels_url="http://iptv-org.invalid/channels.json",
        ffmpeg_path="/fixture-disabled-ffmpeg",
        cors_origins="",
        log_format="text",
    )
    c = build_container(settings, http_factory=http_factory, cache=MemoryCache())
    app = create_app(settings, container=c, start_background=False)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with original(app):
            await seed(c, base)
            yield

    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5187)
    args = parser.parse_args()
    if not (args.dist / "index.html").is_file():
        raise SystemExit("Build the non-MSW web distribution first.")
    with TemporaryDirectory(prefix="streamhub-http-e2e-") as directory:
        app = fixture_app(Path(directory), args.dist.resolve(), f"http://127.0.0.1:{args.port}")
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
