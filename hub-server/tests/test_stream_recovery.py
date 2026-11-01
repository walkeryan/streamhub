from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.proxy.stream_proxy import HttpSession
from app.services.errors import Forbidden
from app.services.playback import StreamGrant
from tests.conftest import Api
from tests.test_hls_admission import start

pytest_plugins = ("tests.test_hls_admission",)


async def test_admin_confirmation_generation_and_token_revocation(api: Api) -> None:
    for method, path in [
        ("GET", "/playback/recovery"),
        ("POST", "/playback/recovery/stop"),
        ("POST", "/playback/recovery/resume"),
    ]:
        response = await api.client.request(method, "/api/v1" + path, json={"confirm": True})
        assert response.status_code == 401
    kid = (await api.post("/profiles", json={"name": "Viewer"})).json()
    login = await api.client.post("/api/v1/auth/login", json={"profile_id": kid["id"]})
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    for method, path in [
        ("GET", "/playback/recovery"),
        ("POST", "/playback/recovery/stop"),
        ("POST", "/playback/recovery/resume"),
    ]:
        assert (
            await api.client.request(method, "/api/v1" + path, headers=headers, json={"confirm": True})
        ).status_code == 403
    old_epoch = (await api.get("/playback/generation")).json()["generation"]
    grant = StreamGrant(api.profile_id, "source", "https://provider.invalid/private", {}, "proxy", False, None, False)
    token = api.c.tokens.issue(grant)
    assert (await api.post("/playback/recovery/stop", json={"confirm": False})).status_code == 422
    assert not api.c.proxy.paused
    result = await api.post("/playback/recovery/stop", json={"confirm": True})
    assert result.json()["paused"] and result.json()["failed"] == 0
    assert api.c.app_settings.get("playback_recovery_paused") is True
    assert (await api.get("/playback/generation")).status_code == 403
    assert (await api.client.get("/api/v1/stream/" + token)).status_code == 403
    assert (await api.post("/playback/recovery/stop", json={"confirm": True})).json()["stopped"] == 0
    assert (await api.post("/playback/recovery/resume", json={"confirm": True})).json()["paused"] is False
    assert api.c.app_settings.get("playback_recovery_paused") is False
    assert api.c.tokens.verify(token) is None
    stale = await api.post("/playback/resolve", json={"id": "missing", "recovery_generation": old_epoch})
    assert stale.status_code == 403 and stale.json()["code"] == "playback_stopped"
    missing = await api.client.post("/api/v1/playback/resolve", headers=api.headers, json={"id": "missing"})
    assert missing.status_code == 403 and missing.json()["code"] == "playback_reload_required"


async def test_inflight_resolution_cannot_cross_stop_and_resume(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def resolve(*args, **kwargs):
        entered.set()
        await release.wait()
        return {}

    monkeypatch.setattr(api.c.playback, "resolve", resolve)
    task = asyncio.create_task(api.post("/playback/resolve", json={"id": "fixture"}))
    await entered.wait()
    await api.post("/playback/recovery/stop", json={"confirm": True})
    await api.post("/playback/recovery/resume", json={"confirm": True})
    release.set()
    result = await task
    assert result.status_code == 403 and result.json()["code"] == "playback_paused"


async def test_owned_hls_stale_entries_and_cleanup_failure(harness, monkeypatch: pytest.MonkeyPatch) -> None:
    proxy, _, processes, _ = harness
    first = await start(proxy, "first", maximum=2)
    second = await start(proxy, "second", maximum=2)
    second.proc.terminate()  # Stale already-exited owned worker still requires cleanup.
    import app.proxy.stream_proxy as module

    real_rmtree = module.shutil.rmtree

    def fail_cleanup(path, *args, **kwargs):
        if Path(path) == first.workdir:
            raise PermissionError("fixture")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", fail_cleanup)
    proxy.pause()
    result = await proxy.stop_owned()
    assert result["stopped"] == 2 and result["failed"] == 0 and result["cleanup_failed"] == 1
    assert all(p.stopped for p in processes)
    with pytest.raises(Forbidden):
        await start(proxy, "third")
    monkeypatch.setattr(module.shutil, "rmtree", real_rmtree)
    again = await proxy.stop_owned()
    assert again["stopped"] == again["failed"] == again["cleanup_failed"] == 0
    assert not first.workdir.exists() and not second.workdir.exists()


async def test_multiple_shared_viewers_release_once_and_old_generator_cannot_drop_new(api: Api) -> None:
    proxy = api.c.proxy
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://fixture.invalid/live").respond(200, content=b"x" * 65536)
        _, one = await proxy.live_shared("same", "https://fixture.invalid/live", {}, "src")
        _, two = await proxy.live_shared("same", "https://fixture.invalid/live", {}, "src")
        await anext(one)
        await anext(two)
        assert api.c.active.count("src") == 2
        old_upstream = proxy._shared["same"]
        # A slow viewer may already have been removed from the fanout set,
        # but its active-source counter still belongs to this upstream.
        for queue in list(old_upstream._subs):
            while not queue.full():
                queue.put_nowait(b"buffered")
        old_upstream._broadcast(b"overflow")
        assert not old_upstream._subs
        proxy.pause()
        assert (await proxy.stop_owned())["stopped"] == 1
        assert api.c.active.count("src") == 0
        proxy.paused = False
        _, new = await proxy.live_shared("same", "https://fixture.invalid/live", {}, "src")
        await anext(new)
        await one.aclose()
        await two.aclose()
        assert old_upstream._linger is None
        assert proxy.recovery_status()["shared_upstreams"] == 1
        assert api.c.active.count("src") == 1
        proxy.pause()
        await proxy.stop_owned()
        await new.aclose()
        assert api.c.active.count("src") == 0


async def test_failed_http_cleanup_retains_ownership_and_blocks_resume(api: Api) -> None:
    client = AsyncMock()
    response = AsyncMock()
    response.aclose.side_effect = OSError("fixture")
    session = HttpSession(client, None, response, "src", True)
    api.c.proxy._http.add(session)
    api.c.active.inc("src")
    result = (await api.post("/playback/recovery/stop", json={"confirm": True})).json()
    assert result["failed"] == 1 and result["stopped"] == 0
    client.aclose.assert_awaited()
    assert (await api.post("/playback/recovery/resume", json={"confirm": True})).status_code == 409
    response.aclose.side_effect = None
    assert (await api.post("/playback/recovery/stop", json={"confirm": True})).json()["failed"] == 0
    assert api.c.active.count("src") == 0


async def test_inflight_http_admission_is_owned_and_canceled(api: Api) -> None:
    entered = asyncio.Event()
    client = AsyncMock()
    client.build_request = lambda *a, **k: httpx.Request("GET", "https://fixture.invalid/film")

    async def send(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    client.send.side_effect = send
    api.c.proxy._client_factory = lambda **kw: client
    task = asyncio.create_task(api.c.proxy.passthrough("https://fixture.invalid/film", {}, "src"))
    await entered.wait()
    assert api.c.proxy.recovery_status()["http_sessions"] == 1
    api.c.proxy.pause()
    result = await api.c.proxy.stop_owned()
    assert result["failed"] == 0
    with pytest.raises(asyncio.CancelledError):
        await task
    client.aclose.assert_awaited()


async def test_hls_spawn_racing_stop_never_escapes_ownership(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_hls_admission import Process

    entered, release = asyncio.Event(), asyncio.Event()
    proc = Process()

    async def spawn(*args, **kwargs):
        entered.set()
        await release.wait()
        Path(args[-1]).write_text("#EXTM3U\n#EXTINF:2,\nseg.ts\n")
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    request = asyncio.create_task(start(api.c.proxy, "racing"))
    await entered.wait()
    stop = asyncio.create_task(api.post("/playback/recovery/stop", json={"confirm": True}))
    while not api.c.proxy.paused:
        await asyncio.sleep(0)
    release.set()
    with pytest.raises(Forbidden):
        await request
    result = (await stop).json()
    assert proc.stopped and result["active_sessions"] == result["failed"] == 0


async def test_recovery_pause_survives_server_reinitialization(api: Api) -> None:
    from app.container import build_container, shutdown, startup

    await api.post("/playback/recovery/stop", json={"confirm": True})
    restarted = build_container(api.c.settings)
    try:
        await startup(restarted, start_background=False)
        assert restarted.proxy.paused
        with pytest.raises(Forbidden):
            restarted.proxy.ensure_running()
    finally:
        await shutdown(restarted)
