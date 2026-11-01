from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.proxy.dvr import HEADROOM, MAX_VOLUME, PARENT_RESERVE, DvrManager, VolumeProof, verify_volume
from app.proxy.stream_proxy import StreamProxy
from app.services.errors import Conflict, Forbidden, Gone
from app.services.playback import StreamGrant
from tests.test_epg_integrity import add_source
from tests.test_hls_admission import Process


@pytest.fixture
def dvr_harness(tmp_path, monkeypatch):
    root = tmp_path / "quota-fixture"
    root.mkdir()
    state = {"clock": 0.0, "free": MAX_VOLUME, "parent": 10 * PARENT_RESERVE}
    now = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    manager = DvrManager(
        root,
        enabled=True,
        clock=lambda: state["clock"],
        now=lambda: now + timedelta(seconds=state["clock"]),
        verify=lambda _: VolumeProof(MAX_VOLUME, state["free"], state["parent"]),
    )
    processes, args_seen = [], []

    async def spawn(*args, **kwargs):
        process = Process()
        processes.append(process)
        args_seen.append(args)
        playlist = Path(args[-1])
        (playlist.parent / "seg_00000.ts").write_bytes(b"synthetic segment")
        start = now - timedelta(seconds=2)
        playlist.write_text(
            f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-PROGRAM-DATE-TIME:{start.isoformat()}\n#EXTINF:2,\nseg_00000.ts\n"
        )
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    proxy = StreamProxy(lambda **_: None, tmp_path / "normal", dvr=manager, clock=lambda: state["clock"])
    return proxy, manager, state, processes, args_seen, now


async def lease(proxy, profile="owner"):
    return await proxy.buffer_lease(
        "source:https://fixture.invalid/live:none",
        profile,
        "https://fixture.invalid/live",
        {},
        source_id="source",
        max_connections=1,
        generation=proxy.generation,
    )


async def test_mount_proof_fails_closed_without_modifying_storage(tmp_path):
    with pytest.raises(Conflict, match="dedicated"):
        verify_volume(tmp_path)
    manager = DvrManager(tmp_path, enabled=False)
    with pytest.raises(Conflict, match="disabled"):
        manager.reserve("key", "id")
    assert not list(tmp_path.iterdir())


async def test_single_owned_hls_upstream_shared_and_window_is_real(dvr_harness):
    proxy, manager, _, processes, args_seen, now = dvr_harness
    first = await lease(proxy)
    second = await lease(proxy, "other")
    assert len(processes) == 1
    args = args_seen[0]
    assert args[args.index("-hls_list_size") + 1] == "1800"
    assert "program_date_time" in args[args.index("-hls_flags") + 1]
    assert first["window"]["buffered_seconds"] == 2 and first["window"]["target_seconds"] == 3600
    key = manager.key
    manifest = await proxy.buffer_manifest(key, now - timedelta(seconds=1))
    assert "TIME-OFFSET=1.000" in manifest
    with pytest.raises(Gone):
        await proxy.buffer_manifest(key, now - timedelta(minutes=60))
    assert not processes[0].stopped  # expired seek does not kill another viewer
    with pytest.raises(Forbidden):
        manager.heartbeat(key, "other", first["lease_id"], False)
    await proxy.buffer_release(key, "owner", first["lease_id"])
    assert not processes[0].stopped
    await proxy.buffer_release(key, "other", second["lease_id"])
    assert processes[0].stopped and manager.key is None and not list(manager.root.iterdir())
    await proxy.shutdown()


async def test_quota_parent_reserve_and_recovery_stop_release_owned_process(dvr_harness):
    proxy, manager, state, processes, _, _ = dvr_harness
    await lease(proxy)
    key = manager.key
    state["free"] = HEADROOM // 4
    with pytest.raises(Conflict):
        await proxy.buffer_window(key)
    assert processes[0].stopped and manager.key is None and not proxy._hls
    state["free"] = MAX_VOLUME
    await lease(proxy)
    proxy.pause()
    await proxy.stop_owned()
    assert manager.key is None and not manager.leases and all(p.stopped for p in processes)
    with pytest.raises(Forbidden):
        await lease(proxy)
    assert len(processes) == 2  # heartbeat/retry cannot restart after admin stop
    await proxy.shutdown()


async def test_paused_lease_protects_upstream_but_expires(dvr_harness):
    proxy, manager, state, processes, _, _ = dvr_harness
    first = await lease(proxy)
    key = manager.key
    manager.heartbeat(key, "owner", first["lease_id"], True)
    for tick in range(60, 3600, 60):
        state["clock"] = float(tick)
        manager.heartbeat(key, "owner", first["lease_id"], True)
        assert manager.held(key)
    state["clock"] = 3601
    assert not manager.held(key)
    await proxy.buffer_release(key, "owner", first["lease_id"])
    assert processes[0].stopped


async def test_existing_normal_process_is_not_duplicated_or_upgraded(dvr_harness):
    proxy, manager, _, processes, _, _ = dvr_harness
    await proxy.hls_session(
        "source:https://fixture.invalid/live:none",
        "https://fixture.invalid/live",
        {},
        live=True,
        transcode="none",
        source_id="source",
        max_connections=1,
    )
    with pytest.raises(Conflict, match="already started"):
        await lease(proxy)
    assert len(processes) == 1 and manager.key is None and not processes[0].stopped
    await proxy.shutdown()


async def test_api_lease_requires_profile_owned_live_grant_and_stop_revokes(api, dvr_harness):
    proxy, manager, _, processes, _, _ = dvr_harness
    source = await add_source(api)
    api.c.proxy.dvr = manager
    # Use application's recovery lifecycle but fake only its subprocess/storage capability.
    channel = (await api.get("/channels", params={"language": "all"})).json()["channels"][0]
    grant = StreamGrant(
        api.profile_id, source["id"], "https://fixture.invalid/live", {}, "hls", True, channel["id"][3:], False
    )
    token = api.c.tokens.issue(grant)
    body = {"stream_token": token}
    result = await api.post("/playback/buffer/lease", json=body)
    assert result.status_code == 200, result.text
    viewer = {**body, "lease_id": result.json()["lease_id"]}
    assert (await api.post("/playback/buffer/heartbeat", json={**viewer, "paused": True})).status_code == 200
    grant.profile_id = "another-profile"
    other_token = api.c.tokens.issue(grant)
    assert (await api.post("/playback/buffer/lease", json={"stream_token": other_token})).status_code == 403
    assert (await api.client.post("/api/v1/playback/buffer/lease", json=body)).status_code == 401
    assert (await api.post("/playback/recovery/stop", json={"confirm": True})).status_code == 200
    assert (await api.post("/playback/buffer/heartbeat", json=viewer)).status_code == 403
    assert manager.key is None and not manager.leases and processes[0].stopped
    assert len(processes) == 1
    assert (await api.post("/playback/recovery/resume", json={"confirm": True})).status_code == 200
    assert (await api.post("/playback/buffer/heartbeat", json=viewer)).status_code == 401
    assert len(processes) == 1
    await proxy.shutdown()


async def test_cancelled_start_stops_only_owned_producer(dvr_harness, monkeypatch):
    proxy, manager, _, processes, _, _ = dvr_harness
    entered = asyncio.Event()

    async def spawn_without_ready_playlist(*args, **kwargs):
        process = Process()
        processes.append(process)
        entered.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_without_ready_playlist)
    task = asyncio.create_task(lease(proxy))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].stopped and not proxy._hls and manager.key is None
    assert not list(manager.root.iterdir())


async def test_one_buffer_channel_even_when_source_has_two_slots(dvr_harness):
    proxy, manager, _, processes, _, _ = dvr_harness
    await lease(proxy)
    with pytest.raises(Conflict, match="one-channel"):
        await proxy.buffer_lease(
            "another-key",
            "owner",
            "https://fixture.invalid/other",
            {},
            source_id="source",
            max_connections=2,
            generation=proxy.generation,
        )
    assert len(processes) == 1 and not processes[0].stopped and manager.held(manager.key)
    await proxy.shutdown()


async def test_storage_preflight_rejects_reserve_before_start(dvr_harness):
    proxy, manager, state, processes, _, _ = dvr_harness
    state["parent"] = PARENT_RESERVE - 1
    with pytest.raises(Conflict, match="reserve"):
        await lease(proxy)
    assert not processes and manager.key is None and not list(manager.root.iterdir())


async def test_expired_seek_does_not_restart_and_segments_bind_exact_grant(api, dvr_harness):
    _, manager, _, processes, _, now = dvr_harness
    source = await add_source(api)
    api.c.proxy.dvr = manager
    channel = (await api.get("/channels", params={"language": "all"})).json()["channels"][0]
    grant = StreamGrant(
        api.profile_id, source["id"], "https://fixture.invalid/live", {}, "hls", True, channel["id"][3:], False
    )
    token = api.c.tokens.issue(grant)
    response = await api.post("/playback/buffer/lease", json={"stream_token": token})
    assert response.status_code == 200
    body = {"stream_token": token, "lease_id": response.json()["lease_id"]}
    seek = await api.post("/playback/buffer/seek", json={**body, "at": (now - timedelta(seconds=1)).isoformat()})
    assert seek.status_code == 200
    seek_url = seek.json()["url"]
    media = await api.client.get(seek_url)
    assert media.status_code == 200 and "TIME-OFFSET=1.000" in media.text
    sid = response.json()["session_id"]
    path = f"/api/v1/stream/{token}/hls/{sid}/seg_00000.ts"
    assert (await api.client.get(path)).status_code == 200
    grant.url = "https://fixture.invalid/different"
    other_token = api.c.tokens.issue(grant)
    assert (await api.client.get(path.replace(token, other_token))).status_code == 403
    assert (await api.post("/playback/buffer/release", json=body)).status_code == 204
    assert processes[0].stopped
    assert (await api.client.get(seek_url)).status_code == 410
    assert len(processes) == 1 and not manager.leases


async def test_cancel_racing_process_creation_reconciles_owned_child(dvr_harness, monkeypatch):
    proxy, manager, _, processes, _, _ = dvr_harness
    created, release = asyncio.Event(), asyncio.Event()

    async def delayed_spawn(*args, **kwargs):
        process = Process()
        processes.append(process)
        created.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(lease(proxy))
    await created.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].stopped and not proxy._hls and manager.key is None
    assert not list(manager.root.iterdir())
