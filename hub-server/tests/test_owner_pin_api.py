"""Owner PIN API acceptance with disposable credentials and synthetic Google only."""

import respx

from app.infra.models import Profile
from tests.test_auth_policy_sessions import FIXTURE_PIN
from tests.test_google_auth import begin, configure, mock_google


async def pending(api):
    configure(api)
    api.c.settings.content_policy_enabled = True
    state, nonce = await begin(api)
    mock_google(nonce)  # No auth_time required; this is identity, not owner unlock.
    await api.get(f"/auth/google/callback?state={state}&code=fixture")
    r = await api.post("/auth/google/complete", headers={"Origin": api.c.google_auth.origin})
    assert r.status_code == 200, r.text
    assert r.json()["owner_pin_required"] and "access_token" not in r.json()
    return {"Authorization": "Bearer " + r.json()["pending_token"]}


async def setup(api, headers):
    r = await api.get("/auth/owner-pin/setup-status", headers=headers)
    assert r.json()["local_approval_required"] and not r.json()["setup_authorized"]
    await api.c.owner_pin.approve_setup_request(r.json()["setup_request_id"])
    grant = (await api.get("/auth/owner-pin/setup-status", headers=headers)).json()
    r = await api.post(
        "/auth/owner-pin/setup",
        headers=headers,
        json={"grant_id": grant["grant_id"], "grant_secret": grant["grant_secret"], "pin": FIXTURE_PIN},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@respx.mock
async def test_pending_google_is_not_access_and_local_enrollment_required(api):
    headers = await pending(api)
    assert (await api.get("/home", headers=headers)).status_code == 401
    denied = await api.post(
        "/auth/owner-pin/setup",
        headers=headers,
        json={"grant_id": "unknown", "grant_secret": "unknown", "pin": FIXTURE_PIN},
    )
    assert denied.status_code in {403, 410}
    owner = await setup(api, headers)
    assert (await api.get("/home", headers=owner)).status_code == 200
    assert (await api.get("/home", headers=headers)).status_code == 401
    # Clearing app state and using retained Google SSO still gives pending-only.
    new_pending = await pending(api)
    assert (await api.get("/home", headers=new_pending)).status_code == 401
    challenge = (
        await api.post(
            "/auth/owner-pin/start", headers=new_pending, json={"profile_id": api.profile_id, "action": "login"}
        )
    ).json()
    r = await api.post("/auth/owner-pin/verify", headers=new_pending, json={**challenge, "pin": FIXTURE_PIN})
    assert r.status_code == 200, r.text
    assert (
        await api.post("/auth/owner-pin/verify", headers=new_pending, json={**challenge, "pin": FIXTURE_PIN})
    ).status_code in {401, 410}


@respx.mock
async def test_admin_policy_requires_one_use_exact_target_proof(api):
    owner = await setup(api, await pending(api))
    async with api.c.db.session() as s:
        s.add_all([Profile(id="child", name="Child"), Profile(id="other", name="Other")])
        await s.commit()
    body = {"restricted": True, "allowed_movie_codes": ["G"], "allowed_tv_codes": [], "allow_unknown": False}
    r = await api.put("/profiles/child/content-policy", headers=owner, json=body)
    assert r.status_code == 403 and r.json()["code"] == "owner_pin_required"
    challenge = (
        await api.post("/auth/owner-pin/start", headers=owner, json={"profile_id": "child", "action": "admin"})
    ).json()
    proof = await api.post("/auth/owner-pin/verify", headers=owner, json={**challenge, "pin": FIXTURE_PIN})
    assert proof.status_code == 200, proof.text
    assert "access_token" not in proof.json()
    action = {**owner, "X-Owner-Action": proof.json()["owner_action_token"]}
    assert (await api.put("/profiles/other/content-policy", headers=action, json=body)).status_code == 403
    assert (await api.put("/profiles/child/content-policy", headers=action, json=body)).status_code == 200
    assert (await api.put("/profiles/child/content-policy", headers=action, json=body)).status_code in {401, 403}
    switched = await api.post("/auth/profile-switch", headers=owner, json={"profile_id": "child"})
    assert switched.status_code == 200, switched.text
    child = {"Authorization": "Bearer " + switched.json()["access_token"]}
    assert (await api.get("/home", headers=owner)).status_code == 401
    assert (await api.post("/auth/profile-switch", headers=child, json={"profile_id": api.profile_id})).json()[
        "code"
    ] == "owner_pin_required"
    challenge = (
        await api.post(
            "/auth/owner-pin/start", headers=child, json={"profile_id": api.profile_id, "action": "profile_switch"}
        )
    ).json()
    r = await api.post("/auth/owner-pin/verify", headers=child, json={**challenge, "pin": FIXTURE_PIN})
    assert r.status_code == 200 and r.json()["profile"]["id"] == api.profile_id
    assert (await api.get("/home", headers=child)).status_code == 401


@respx.mock
async def test_invalid_pin_privacy_and_persistent_http_limit(api, caplog):
    owner = await setup(api, await pending(api))
    challenge = (
        await api.post("/auth/owner-pin/start", headers=owner, json={"profile_id": api.profile_id, "action": "login"})
    ).json()
    for secret in ["invalid-sensitive-fixture", "1" * 100, "１２３４５６"]:
        r = await api.post("/auth/owner-pin/verify", headers=owner, json={**challenge, "pin": secret})
        assert r.status_code in {403, 422} and secret not in r.text and secret not in caplog.text
    for _ in range(4):
        r = await api.post("/auth/owner-pin/verify", headers=owner, json={**challenge, "pin": "000001"})
        assert r.status_code == 403, r.text
    r = await api.post("/auth/owner-pin/verify", headers=owner, json={**challenge, "pin": FIXTURE_PIN})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert FIXTURE_PIN not in r.text and FIXTURE_PIN not in caplog.text


@respx.mock
async def test_recovery_requires_local_approval_and_revokes_old_sessions_only_on_commit(api):
    owner = await setup(api, await pending(api))
    browser = await pending(api)
    status = await api.get("/auth/owner-pin/recovery-status", headers=browser)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["local_approval_required"] and not body["recovery_authorized"]
    assert "grant_secret" not in body
    denied = await api.post(
        "/auth/owner-pin/recover",
        headers=browser,
        json={"grant_id": body["recovery_request_id"], "grant_secret": "unknown", "pin": "847293"},
    )
    assert denied.status_code in {403, 410}
    assert (await api.get("/home", headers=owner)).status_code == 200
    await api.c.owner_pin.approve_recovery_request(body["recovery_request_id"])
    grant = (await api.get("/auth/owner-pin/recovery-status", headers=browser)).json()
    assert grant["recovery_authorized"]
    assert (await api.get("/home", headers=owner)).status_code == 200
    r = await api.post(
        "/auth/owner-pin/recover",
        headers=browser,
        json={"grant_id": grant["grant_id"], "grant_secret": grant["grant_secret"], "pin": "847293"},
    )
    assert r.status_code == 200, r.text
    new = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert (await api.get("/home", headers=new)).status_code == 200
    assert (await api.get("/home", headers=owner)).status_code == 401
    assert (await api.get("/auth/owner-pin/recovery-status", headers=browser)).status_code == 401
