from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.container import Container, build_container
from app.infra.cache import MemoryCache
from app.infra.settings import AppSettings
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
ADMIN_PASSWORD = "test-admin-pw"


IPTV_ORG_DB = [{"id": "bbc1.uk", "languages": ["eng"], "country": "UK"}]


class _TestTransport(httpx.AsyncHTTPTransport):
    """Answers the iptv-org DB locally; everything else goes through httpcore (where respx hooks in)."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "iptv-org.invalid":
            return httpx.Response(200, json=IPTV_ORG_DB)
        return await super().handle_async_request(request)


def http_factory(**kw: Any) -> httpx.AsyncClient:
    kw.pop("timeout", None)
    return httpx.AsyncClient(timeout=5.0, follow_redirects=True, transport=_TestTransport(), **kw)


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        config_dir=tmp_path / "config",
        secret_key="test-secret-key-0123456789",
        admin_password=ADMIN_PASSWORD,
        web_dist=tmp_path / "web-dist",
        transcode_dir=tmp_path / "transcode",
        enable_scheduler=False,
        log_format="text",
        tmdb_api_key=None,
        cors_origins="",
        iptv_org_channels_url="http://iptv-org.invalid/channels.json",
    )


@pytest.fixture
def container(settings: AppSettings) -> Container:
    return build_container(settings, http_factory=http_factory, cache=MemoryCache())


class Api:
    """Thin helper around an AsyncClient with the admin logged in."""

    def __init__(self, client: AsyncClient, c: Container) -> None:
        self.client = client
        self.c = c
        self.headers: dict[str, str] = {}
        self.profile_id = ""

    async def login_admin(self) -> None:
        profiles = (await self.client.get("/api/v1/profiles")).json()
        admin = next(p for p in profiles if p["is_admin"])
        r = await self.client.post("/api/v1/auth/login", json={"profile_id": admin["id"], "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        self.profile_id = admin["id"]

    async def req(self, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = {**self.headers, **kw.pop("headers", {})}
        return await self.client.request(method, "/api/v1" + url, headers=headers, **kw)

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.req("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.req("POST", url, **kw)

    async def put(self, url: str, **kw: Any) -> httpx.Response:
        return await self.req("PUT", url, **kw)

    async def patch(self, url: str, **kw: Any) -> httpx.Response:
        return await self.req("PATCH", url, **kw)

    async def delete(self, url: str, **kw: Any) -> httpx.Response:
        return await self.req("DELETE", url, **kw)

    async def accept_disclaimer(self) -> None:
        r = await self.post("/disclaimer/accept", json={"version": "1.0"})
        assert r.status_code == 204, r.text


@pytest.fixture
async def api(settings: AppSettings, container: Container) -> AsyncIterator[Api]:
    app = create_app(settings, container=container, start_background=False)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://hub.test") as client,
    ):
        a = Api(client, container)
        await a.login_admin()
        yield a


async def add_m3u(api: Api, path: Path, name: str = "Test M3U", **cfg: Any) -> dict[str, Any]:
    await api.accept_disclaimer()
    r = await api.post("/sources", json={"type": "m3u", "name": name, "config": {"file": path.read_text(), **cfg}})
    assert r.status_code == 201, r.text
    await api.c.sync.wait_idle()
    src: dict[str, Any] = (await api.get(f"/sources/{r.json()['id']}")).json()
    return src
