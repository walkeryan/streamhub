from __future__ import annotations

from pathlib import Path

from tests.conftest import FIXTURES, Api, add_m3u


async def test_health_and_disclaimer_text(api: Api) -> None:
    assert (await api.client.get("/api/v1/health")).json()["status"] == "ok"
    d = (await api.client.get("/api/v1/disclaimer")).json()
    assert d["version"] == "1.0"
    assert d["text"].startswith("**StreamHub is a media player and organizer.**")
    assert "JustWatch" in d["text"]


async def test_unauthenticated_requests_rejected(api: Api) -> None:
    r = await api.client.get("/api/v1/sources")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


async def test_login_wrong_password(api: Api) -> None:
    r = await api.client.post("/api/v1/auth/login", json={"profile_id": api.profile_id, "password": "nope"})
    assert r.status_code == 401


async def test_refresh_flow(api: Api) -> None:
    r = await api.client.post("/api/v1/auth/login", json={"profile_id": api.profile_id, "password": "test-admin-pw"})
    tokens = r.json()
    r = await api.client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200 and r.json()["profile"]["is_admin"]
    # access token cannot be used as refresh
    r = await api.client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert r.status_code == 401


async def test_disclaimer_gate_412(api: Api) -> None:
    body = {"type": "m3u", "name": "x", "config": {"url": "http://example/x.m3u"}}
    r = await api.post("/sources", json=body)
    assert r.status_code == 412 and r.json()["code"] == "disclaimer_required"
    r = await api.post(
        "/sources",
        json={"type": "xtream", "name": "x", "config": {"server_url": "http://p", "username": "u", "password": "p"}},
    )
    assert r.status_code == 412
    # streaming_service is not gated
    r = await api.post(
        "/sources", json={"type": "streaming_service", "name": "Netflix", "service_key": "netflix", "config": {}}
    )
    assert r.status_code == 201
    # wrong version rejected, current version accepted
    assert (await api.post("/disclaimer/accept", json={"version": "0.9"})).status_code == 409
    await api.accept_disclaimer()
    me = next(p for p in (await api.client.get("/api/v1/profiles")).json() if p["id"] == api.profile_id)
    assert me["disclaimer_accepted_version"] == "1.0"
    r = await api.post("/sources", json=body)
    assert r.status_code == 201


async def test_profiles_pin_login(api: Api) -> None:
    r = await api.post(
        "/profiles", json={"name": "Kid", "kids": True, "languages": ["en"], "pin": "1234", "is_admin": True}
    )
    assert r.status_code == 201, r.text
    kid = r.json()
    assert kid["has_pin"] and kid["kids"] and not kid["is_admin"] and kid["languages"] == ["eng"]
    bad = await api.client.post("/api/v1/auth/login", json={"profile_id": kid["id"], "pin": "0000"})
    assert bad.status_code == 401
    ok = await api.client.post("/api/v1/auth/login", json={"profile_id": kid["id"], "pin": "1234"})
    assert ok.status_code == 200
    kid_h = {"Authorization": "Bearer " + ok.json()["access_token"]}
    # non-admin cannot create sources or profiles
    r = await api.client.post("/api/v1/profiles", json={"name": "x"}, headers=kid_h)
    assert r.status_code == 403
    assert (await api.put(f"/profiles/{kid['id']}/pin", json={"pin": "12"})).status_code == 400
    assert (await api.put(f"/profiles/{kid['id']}/pin", json={"pin": None})).status_code == 204
    # PIN change bumps token version → old refresh token revoked
    r = await api.client.post("/api/v1/auth/refresh", json={"refresh_token": ok.json()["refresh_token"]})
    assert r.status_code == 401
    r = await api.patch(f"/profiles/{kid['id']}", json={"theme_id": "oled"})
    assert r.json()["theme_id"] == "oled"
    assert (await api.patch(f"/profiles/{kid['id']}", json={"theme_id": "nope"})).status_code == 400
    assert (await api.delete(f"/profiles/{kid['id']}")).status_code == 204
    assert (await api.delete(f"/profiles/{api.profile_id}")).status_code == 409


async def test_device_pairing_flow(api: Api) -> None:
    r = await api.client.post("/api/v1/auth/device")
    code = r.json()
    assert len(code["user_code"]) == 6 and code["interval"] > 0
    poll = await api.client.post("/api/v1/auth/device/poll", json={"device_code": code["device_code"]})
    assert poll.status_code == 428
    # approval requires auth
    r = await api.client.post("/api/v1/auth/device/approve", json={"user_code": code["user_code"]})
    assert r.status_code == 401
    r = await api.post("/auth/device/approve", json={"user_code": code["user_code"].lower()})
    assert r.status_code == 204
    poll = await api.client.post("/api/v1/auth/device/poll", json={"device_code": code["device_code"]})
    assert poll.status_code == 200 and poll.json()["profile"]["id"] == api.profile_id
    # one-time use
    again = await api.client.post("/api/v1/auth/device/poll", json={"device_code": code["device_code"]})
    assert again.status_code == 404
    assert (await api.post("/auth/device/approve", json={"user_code": "ZZZZZZ"})).status_code == 404


async def test_admin_password_generated_once(settings: object, caplog: object, tmp_path: Path) -> None:
    import logging

    from app.container import build_container, shutdown, startup
    from app.infra.cache import MemoryCache
    from app.infra.settings import AppSettings

    s = AppSettings(config_dir=tmp_path / "gen", secret_key="k" * 20, enable_scheduler=False)
    c = build_container(s, cache=MemoryCache())
    with caplog.at_level(logging.WARNING):  # type: ignore[attr-defined]
        await startup(c, start_background=False)
    await shutdown(c)
    msgs = [r.getMessage() for r in caplog.records if "Generated admin password" in r.getMessage()]  # type: ignore[attr-defined]
    assert len(msgs) == 1
    c2 = build_container(s, cache=MemoryCache())
    caplog.clear()  # type: ignore[attr-defined]
    await startup(c2, start_background=False)
    await shutdown(c2)
    assert not [r for r in caplog.records if "Generated admin password" in r.getMessage()]  # type: ignore[attr-defined]


async def test_source_crud_and_secret_redaction(api: Api) -> None:
    await api.accept_disclaimer()
    r = await api.post(
        "/sources",
        json={
            "type": "xtream",
            "name": "Panel",
            "config": {
                "server_url": "http://panel.example",
                "username": "bob",
                "password": "hunter22",
                "output": "m3u8",
            },
        },
    )
    assert r.status_code == 201
    src = r.json()
    await api.c.sync.wait_idle()  # sync fails (no network) – must not crash
    listed = (await api.get("/sources")).text
    assert "hunter22" not in listed and "bob" not in listed
    got = (await api.get(f"/sources/{src['id']}")).json()
    assert got["status"] in ("error", "unauthenticated")
    r = await api.patch(f"/sources/{src['id']}", json={"name": "Renamed", "enabled": False})
    assert r.json()["name"] == "Renamed" and r.json()["enabled"] is False
    # stored config is encrypted at rest
    row = await api.c.sources.get(src["id"])
    assert "hunter22" not in row.config_enc
    assert (await api.delete(f"/sources/{src['id']}")).status_code == 204
    assert (await api.get(f"/sources/{src['id']}")).status_code == 404


async def test_log_redaction() -> None:
    from app.infra.logging import redact_text, redact_url, secret_registry

    secret_registry.add("topsecretvalue")
    assert "topsecretvalue" not in redact_text("password is topsecretvalue")
    assert redact_text("GET http://p/live/user1/pass1/101.ts") == "GET http://p/live/***/***/101.ts"
    assert "pw1" not in (redact_url("http://p/player_api.php?username=u&password=pw1") or "")
    assert "tok" not in (redact_url("http://plex:32400/a?X-Plex-Token=tok") or "")


async def test_m3u_upload_persisted(api: Api) -> None:
    src = await add_m3u(api, FIXTURES / "sample.m3u")
    assert src["status"] == "ok" and src["counts"]["channels"] == 7
    files = list(api.c.settings.playlist_dir.glob("upload-*.m3u"))
    assert len(files) == 1
    assert src["config_summary"]["file"] == files[0].name
    # a path-like file reference is rejected
    r = await api.post("/sources", json={"type": "m3u", "name": "x", "config": {"file": "../../etc/passwd"}})
    assert r.status_code == 400
