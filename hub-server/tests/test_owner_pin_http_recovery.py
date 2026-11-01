"""Recovery and expiry through real HTTP endpoints with local synthetic authority."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt.api_jwt

from tests.policy_http_harness import policy_app

PIN, NEW_PIN = "629417", "840296"


async def pending(client, base):
    handoff = (await client.post("/__fixture/google-handoff")).json()["handoff"]
    response = await client.post(
        "/api/v1/auth/google/complete", headers={"Origin": base, "Cookie": f"__Host-sh-google-handoff={handoff}"}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    return data, {"Authorization": f"Bearer {data['pending_token']}"}


async def enroll(client, base):
    _, headers = await pending(client, base)
    status = (await client.get("/api/v1/auth/owner-pin/setup-status", headers=headers)).json()
    assert (
        await client.post("/__fixture/local-approve-setup", json={"request_id": status["setup_request_id"]})
    ).status_code == 200
    status = (await client.get("/api/v1/auth/owner-pin/setup-status", headers=headers)).json()
    response = await client.post(
        "/api/v1/auth/owner-pin/setup",
        headers=headers,
        json={"grant_id": status["grant_id"], "grant_secret": status["grant_secret"], "pin": PIN},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    return data, {"Authorization": f"Bearer {data['access_token']}"}


async def challenge(client, headers, target, action):
    response = await client.post(
        "/api/v1/auth/owner-pin/start", headers=headers, json={"profile_id": target, "action": action}
    )
    assert response.status_code == 200, response.text
    return {key: response.json()[key] for key in ("challenge_id", "poll_token")}


async def test_local_recovery_revokes_old_pin_sessions_actions_and_media(tmp_path: Path):
    base = "http://127.0.0.1:5188"
    app = policy_app(tmp_path, tmp_path / "dist", base)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=base) as client,
    ):
        data, owner = await enroll(client, base)
        old_request = await challenge(client, owner, "fixture-child", "admin")
        action = await client.post("/api/v1/auth/owner-pin/verify", headers=owner, json={**old_request, "pin": PIN})
        assert action.status_code == 200
        media = await client.post(
            "/api/v1/playback/resolve",
            headers=owner,
            json={
                "id": "lib:fixture-iptv:en",
                "variant_id": "lib:fixture-iptv:en",
                "allow_unverified_audio": True,
                "client": "android_tv",
            },
        )
        assert media.status_code == 200, media.text
        assert "/api/v1/stream/" in media.json()["url"]
        _, recovery_headers = await pending(client, base)
        status = (await client.get("/api/v1/auth/owner-pin/recovery-status", headers=recovery_headers)).json()
        assert not status["recovery_authorized"] and status["local_approval_required"]
        denied = await client.post(
            "/api/v1/auth/owner-pin/recover",
            headers=recovery_headers,
            json={"grant_id": status["recovery_request_id"], "grant_secret": "no-local-grant", "pin": NEW_PIN},
        )
        assert denied.status_code in (403, 410)
        assert (await client.get("/api/v1/home", headers=owner)).status_code == 200
        # Failed guesses cannot self-authorize recovery, but must not trap the
        # independently approved local operator behind the old credential lock.
        guessing = await challenge(client, recovery_headers, data["profile"]["id"], "login")
        for _ in range(5):
            failure = await client.post(
                "/api/v1/auth/owner-pin/verify", headers=recovery_headers, json={**guessing, "pin": "000001"}
            )
            assert failure.status_code == 403
        locked = await client.post(
            "/api/v1/auth/owner-pin/verify", headers=recovery_headers, json={**guessing, "pin": PIN}
        )
        assert locked.status_code == 429
        approved = await client.post(
            "/__fixture/local-approve-recovery", json={"request_id": status["recovery_request_id"]}
        )
        assert approved.status_code == 200, approved.text
        grant = (await client.get("/api/v1/auth/owner-pin/recovery-status", headers=recovery_headers)).json()
        recovered = await client.post(
            "/api/v1/auth/owner-pin/recover",
            headers=recovery_headers,
            json={"grant_id": grant["grant_id"], "grant_secret": grant["grant_secret"], "pin": NEW_PIN},
        )
        assert recovered.status_code == 200, recovered.text
        new_owner = {"Authorization": f"Bearer {recovered.json()['access_token']}"}
        assert (await client.get("/api/v1/home", headers=owner)).status_code == 401
        assert (
            await client.post("/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]})
        ).status_code == 401
        # Stale media is rejected before the fixture provider can be contacted.
        assert (await client.get(media.json()["url"])).status_code in (401, 403)
        old_action = {**new_owner, "X-Owner-Action": action.json()["owner_action_token"]}
        denied_action = await client.put(
            "/api/v1/profiles/fixture-child/content-policy",
            headers=old_action,
            json={"restricted": True, "allowed_movie_codes": ["PG"], "allowed_tv_codes": [], "allow_unknown": False},
        )
        assert denied_action.status_code in (401, 403)
        _, login_headers = await pending(client, base)
        request = await challenge(client, login_headers, data["profile"]["id"], "login")
        assert (
            await client.post("/api/v1/auth/owner-pin/verify", headers=login_headers, json={**request, "pin": PIN})
        ).status_code == 403
        good = await client.post(
            "/api/v1/auth/owner-pin/verify", headers=login_headers, json={**request, "pin": NEW_PIN}
        )
        assert good.status_code == 200 and good.json()["profile"]["is_admin"]


async def test_pending_login_and_setup_grants_expire_without_waiting(tmp_path: Path, monkeypatch):
    import app.services.owner_pin as pin_module

    base = "http://127.0.0.1:5188"
    app = policy_app(tmp_path, tmp_path / "dist", base)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=base) as client,
    ):
        _, headers = await pending(client, base)
        status = (await client.get("/api/v1/auth/owner-pin/setup-status", headers=headers)).json()
        assert (
            await client.post("/__fixture/local-approve-setup", json={"request_id": status["setup_request_id"]})
        ).status_code == 200
        grant = (await client.get("/api/v1/auth/owner-pin/setup-status", headers=headers)).json()
        now = datetime.now(UTC)
        monkeypatch.setattr(pin_module, "utcnow", lambda: now + timedelta(minutes=9))
        expired = await client.post(
            "/api/v1/auth/owner-pin/setup",
            headers=headers,
            json={"grant_id": grant["grant_id"], "grant_secret": grant["grant_secret"], "pin": PIN},
        )
        assert expired.status_code == 410

        class FutureDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(minutes=11)

        monkeypatch.setattr(jwt.api_jwt, "datetime", FutureDateTime)
        assert (await client.get("/api/v1/auth/owner-pin/setup-status", headers=headers)).status_code == 401


async def test_admin_action_expires_without_revoking_valid_owner_session(tmp_path: Path, monkeypatch):
    base = "http://127.0.0.1:5188"
    app = policy_app(tmp_path, tmp_path / "dist", base)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=base) as client,
    ):
        _, owner = await enroll(client, base)
        request = await challenge(client, owner, "fixture-child", "admin")
        verified = await client.post("/api/v1/auth/owner-pin/verify", headers=owner, json={**request, "pin": PIN})
        assert verified.status_code == 200

        class FutureDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(minutes=9)

        monkeypatch.setattr(jwt.api_jwt, "datetime", FutureDateTime)
        response = await client.put(
            "/api/v1/profiles/fixture-child/content-policy",
            headers={**owner, "X-Owner-Action": verified.json()["owner_action_token"]},
            json={"restricted": True, "allowed_movie_codes": ["PG"], "allowed_tv_codes": [], "allow_unknown": False},
        )
        assert response.status_code == 401
        policy = await client.get("/api/v1/profiles/fixture-child/content-policy", headers=owner)
        assert policy.status_code == 200 and not policy.json()["restricted"]
