"""Local concurrent session consumption and signed owner-proof fixtures."""

import asyncio
import time

import pytest
from sqlalchemy import update

from app.infra.models import Profile
from app.services.errors import Forbidden, Unauthorized
from tests.conftest import Api


def enable(api: Api) -> None:
    api.c.settings.content_policy_enabled = True


async def test_atomic_transition_has_one_winner_and_revokes_refresh(api: Api) -> None:
    enable(api)
    auth = api.c.auth
    profile = await auth.get_profile(api.profile_id)
    tokens = auth.issue(profile)
    p = await auth.resolve_principal(tokens["access_token"])
    results = await asyncio.gather(
        auth.consume_session(p.profile_id, p.token_version, p.session_id),
        auth.consume_session(p.profile_id, p.token_version, p.session_id),
        return_exceptions=True,
    )
    assert sum(x is None for x in results) == 1
    assert sum(isinstance(x, Unauthorized) for x in results) == 1
    with pytest.raises(Unauthorized):
        await auth.refresh(tokens["refresh_token"])
    with pytest.raises(Unauthorized):
        await auth.consume_session(p.profile_id, p.token_version + 1, "wrong-version")


async def test_owner_proof_survives_refresh_and_stale_pairing_is_denied(api: Api) -> None:
    enable(api)
    auth = api.c.auth
    profile = await auth.get_profile(api.profile_id)
    device = await auth.start_device()
    stale = auth.issue(profile, owner_auth_time=time.time() - 3600)
    p = await auth.resolve_principal(stale["access_token"])
    with pytest.raises(Forbidden):
        await auth.approve_device(p, device["user_code"], profile.id)
    timestamp = time.time()
    profile.__dict__["_owner_auth_time"] = timestamp
    tokens = auth.issue(profile)
    _, refreshed = await auth.refresh(tokens["refresh_token"])
    p = await auth.resolve_principal(refreshed["access_token"])
    assert p.owner_auth_time == timestamp
    await auth.approve_device(p, device["user_code"], profile.id)
    _, paired = await auth.poll_device(device["device_code"])
    assert (await auth.resolve_principal(paired["access_token"])).owner_auth_time == timestamp


async def test_restricted_legacy_admin_has_no_admin_privilege(api: Api) -> None:
    enable(api)
    async with api.c.db.session() as s:
        await s.execute(update(Profile).where(Profile.id == api.profile_id).values(kids=True))
        await s.commit()
    profile = await api.c.auth.get_profile(api.profile_id)
    token = api.c.auth.issue(profile)["access_token"]
    p = await api.c.auth.resolve_principal(token)
    assert not p.is_admin
    assert not api.c.auth._decode(token, "access")["adm"]


async def test_revoked_approver_cannot_finish_device_pairing(api: Api) -> None:
    enable(api)
    auth = api.c.auth
    profile = await auth.get_profile(api.profile_id)
    device = await auth.start_device()
    p = await auth.resolve_principal(auth.issue(profile, owner_auth_time=time.time())["access_token"])
    await auth.approve_device(p, device["user_code"], profile.id)
    await auth.consume_session(p.profile_id, p.token_version, p.session_id)
    with pytest.raises(Unauthorized):
        await auth.poll_device(device["device_code"])


async def test_approved_challenge_cannot_resurrect_consumed_source(api: Api) -> None:
    from app.services.owner_reauth import OwnerReauthService
    from tests.test_google_auth import configure

    configure(api)
    enable(api)
    auth = api.c.auth
    profile = await auth.get_profile(api.profile_id)
    p = await auth.resolve_principal(auth.issue(profile)["access_token"])
    service = OwnerReauthService(auth, api.c.google_auth)
    challenge = await service.start(p, profile.id)
    service.challenges[challenge["challenge_id"]].status = "approved"
    await auth.consume_session(p.profile_id, p.token_version, p.session_id)
    with pytest.raises(Unauthorized):
        await service.poll(p, challenge["challenge_id"], challenge["poll_token"])


async def test_kids_mutation_revokes_sessions_and_cannot_restrict_admin(api: Api) -> None:
    from app.services.errors import ServiceError

    enable(api)
    auth = api.c.auth
    owner = await auth.get_profile(api.profile_id)
    p = await auth.resolve_principal(auth.issue(owner)["access_token"])
    with pytest.raises(ServiceError, match="owner profile cannot be restricted"):
        await auth.update_profile(p, owner.id, {"kids": True})
    child = await auth.create_profile(p, {"name": "Child", "is_admin": True, "kids": False})
    assert not child.is_admin
    tokens = auth.issue(child)
    old_version = child.token_version
    changed = await auth.update_profile(p, child.id, {"kids": True})
    assert changed.token_version == old_version + 1
    unchanged = await auth.update_profile(p, child.id, {"kids": True})
    assert unchanged.token_version == changed.token_version
    with pytest.raises(Unauthorized):
        await auth.resolve_principal(tokens["access_token"])
