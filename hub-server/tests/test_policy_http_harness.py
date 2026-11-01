"""Actual ASGI owner-PIN contract, only synthetic credentials and local fixture data."""

from pathlib import Path

import httpx

from tests.policy_http_harness import policy_app

PIN = "629417"


async def test_policy_real_http_contract(tmp_path: Path):
    base = "http://127.0.0.1:5188"
    app = policy_app(tmp_path, tmp_path / "dist", base)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=base) as client,
    ):

        async def google_pending():
            handoff = (await client.post("/__fixture/google-handoff")).json()["handoff"]
            response = await client.post(
                "/api/v1/auth/google/complete",
                headers={
                    "Origin": base,
                    "Cookie": f"__Host-sh-google-handoff={handoff}",
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["owner_pin_required"] and "access_token" not in data
            return data, {"Authorization": f"Bearer {data['pending_token']}"}

        pending_data, pending = await google_pending()
        assert (await client.get("/api/v1/home", headers=pending)).status_code == 401
        status = (await client.get("/api/v1/auth/owner-pin/setup-status", headers=pending)).json()
        assert status["local_approval_required"] and not status["setup_authorized"]
        rejected = await client.post(
            "/api/v1/auth/owner-pin/setup",
            headers=pending,
            json={
                "grant_id": "unapproved",
                "grant_secret": "unapproved",
                "pin": PIN,
            },
        )
        assert rejected.status_code in (403, 410)
        approved = await client.post("/__fixture/local-approve-setup", json={"request_id": status["setup_request_id"]})
        assert approved.status_code == 200, approved.text
        grant = (await client.get("/api/v1/auth/owner-pin/setup-status", headers=pending)).json()
        setup = await client.post(
            "/api/v1/auth/owner-pin/setup",
            headers=pending,
            json={
                "grant_id": grant["grant_id"],
                "grant_secret": grant["grant_secret"],
                "pin": PIN,
            },
        )
        assert setup.status_code == 200, setup.text
        owner_data = setup.json()
        owner = {"Authorization": f"Bearer {owner_data['access_token']}"}
        assert (await client.get("/api/v1/home", headers=owner)).status_code == 200

        async def challenge(headers, target, action):
            response = await client.post(
                "/api/v1/auth/owner-pin/start", headers=headers, json={"profile_id": target, "action": action}
            )
            assert response.status_code == 200, response.text
            data = response.json()
            return {key: data[key] for key in ("challenge_id", "poll_token")}

        policy = {"restricted": True, "allowed_movie_codes": ["PG"], "allowed_tv_codes": [], "allow_unknown": False}
        endpoint = "/api/v1/profiles/fixture-child/content-policy"
        assert (await client.put(endpoint, headers=owner, json=policy)).status_code == 403
        request = await challenge(owner, "fixture-child", "admin")
        verified = await client.post("/api/v1/auth/owner-pin/verify", headers=owner, json={**request, "pin": PIN})
        assert verified.status_code == 200, verified.text
        proof = verified.json()
        assert "access_token" not in proof and proof["action"] == "content_policy"
        admin = {**owner, "X-Owner-Action": proof["owner_action_token"]}
        assert (await client.put(endpoint, headers=admin, json=policy)).status_code == 200
        assert (await client.put(endpoint, headers=admin, json=policy)).status_code in (401, 403)
        switched = await client.post("/api/v1/auth/profile-switch", headers=owner, json={"profile_id": "fixture-child"})
        assert switched.status_code == 200, switched.text
        child = {"Authorization": f"Bearer {switched.json()['access_token']}"}
        assert (await client.get("/api/v1/home", headers=owner)).status_code == 401
        search = await client.get("/api/v1/search?q=Star%20Wars", headers=child)
        assert search.status_code == 200 and [row["id"] for row in search.json()["results"]] == ["tmdb:movie:11"]
        denied = await client.post(
            "/api/v1/playback/resolve",
            headers=child,
            json={
                "id": "lib:fixture-iptv:en",
                "variant_id": "lib:fixture-iptv:en",
                "allow_unverified_audio": True,
                "client": "android_tv",
            },
        )
        assert denied.status_code == 403 and denied.json()["code"] == "content_restricted"
        owner_id = owner_data["profile"]["id"]
        escape = await client.post("/api/v1/auth/profile-switch", headers=child, json={"profile_id": owner_id})
        assert escape.status_code == 403 and escape.json()["code"] == "owner_pin_required"
        request = await challenge(child, owner_id, "profile_switch")
        wrong = await client.post("/api/v1/auth/owner-pin/verify", headers=child, json={**request, "pin": "000001"})
        assert wrong.status_code == 403 and "000001" not in wrong.text
        assert (await client.post("/api/v1/auth/owner-pin/cancel", headers=child, json=request)).status_code == 204
        assert (
            await client.post("/api/v1/auth/owner-pin/verify", headers=child, json={**request, "pin": PIN})
        ).status_code == 410
        request = await challenge(child, owner_id, "profile_switch")
        restored = await client.post("/api/v1/auth/owner-pin/verify", headers=child, json={**request, "pin": PIN})
        assert restored.status_code == 200 and restored.json()["profile"]["is_admin"], restored.text
        assert (await client.get("/api/v1/home", headers=child)).status_code == 401

        # Simulated retained Google SSO is still only a pending identity after enrollment.
        pending_data, fresh_pending = await google_pending()
        assert not pending_data["setup_required"]
        assert (await client.get("/api/v1/home", headers=fresh_pending)).status_code == 401
        status = (await client.get("/api/v1/auth/owner-pin/setup-status", headers=fresh_pending)).json()
        assert status["configured"] and not status["setup_authorized"] and "grant_secret" not in status
        assert (await client.post("/api/v1/auth/owner-pin/reset", headers=fresh_pending)).status_code in (404, 405)
        request = await challenge(fresh_pending, owner_id, "login")
        for _ in range(4):
            assert (
                await client.post(
                    "/api/v1/auth/owner-pin/verify", headers=fresh_pending, json={**request, "pin": "000001"}
                )
            ).status_code == 403
        limited = await client.post(
            "/api/v1/auth/owner-pin/verify", headers=fresh_pending, json={**request, "pin": PIN}
        )
        assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
        assert PIN not in limited.text
