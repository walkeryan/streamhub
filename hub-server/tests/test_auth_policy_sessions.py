"""Local concurrent session consumption and owner PIN proof fixtures."""

import asyncio

import pytest
from sqlalchemy import update

from app.infra.models import Profile
from app.services.errors import Forbidden, Unauthorized
from tests.conftest import Api

FIXTURE_PIN = "629417"


async def enroll(api: Api) -> int:
    """Exercise the local-only enrollment authority against disposable fixture data."""
    api.c.settings.content_policy_enabled = True
    auth = api.c.auth
    profile = await auth.get_profile(api.profile_id)
    pending = auth.principal_from_access(auth.issue(profile, google_subject="fixture-owner-subject")["access_token"])
    grant = await api.c.owner_pin.authorize_setup(pending, "fixture-owner-subject")
    proof = await api.c.owner_pin.setup(
        pending, grant["challenge_id"], grant["poll_token"], FIXTURE_PIN, "fixture-owner-subject"
    )
    return proof.credential_version


async def test_atomic_transition_has_one_winner_and_revokes_refresh(api: Api) -> None:
    version = await enroll(api)
    auth = api.c.auth
    profile = await auth.get_profile(api.profile_id)
    tokens = auth.issue(profile, owner_pin_version=version)
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


async def test_pin_proof_survives_refresh_and_unverified_pairing_is_denied(api: Api) -> None:
    version = await enroll(api)
    auth = api.c.auth
    profile = await auth.get_profile(api.profile_id)
    device = await auth.start_device()
    stale = auth.issue(profile, owner_pin_version=version - 1)
    with pytest.raises(Forbidden):
        await auth.resolve_principal(stale["access_token"])
    with pytest.raises(Forbidden):
        await auth.approve_device(auth.principal_from_access(stale["access_token"]), device["user_code"], profile.id)
    tokens = auth.issue(profile, owner_pin_version=version, google_subject="fixture-owner-subject")
    _, refreshed = await auth.refresh(tokens["refresh_token"])
    p = await auth.resolve_principal(refreshed["access_token"])
    assert p.owner_pin_version == version and p.google_subject == "fixture-owner-subject"
    await auth.approve_device(p, device["user_code"], profile.id)
    _, paired = await auth.poll_device(device["device_code"])
    paired_principal = await auth.resolve_principal(paired["access_token"])
    assert paired_principal.owner_pin_version == version
    assert paired_principal.google_subject == "fixture-owner-subject"


async def test_restricted_legacy_admin_has_no_admin_privilege(api: Api) -> None:
    await enroll(api)
    async with api.c.db.session() as s:
        await s.execute(
            update(Profile).where(Profile.id == api.profile_id).values(kids=True, content_policy={"restricted": True})
        )
        await s.commit()
    profile = await api.c.auth.get_profile(api.profile_id)
    token = api.c.auth.issue(profile)["access_token"]
    p = await api.c.auth.resolve_principal(token)
    assert not p.is_admin
    assert not api.c.auth._decode(token, "access")["adm"]


async def test_revoked_approver_cannot_finish_device_pairing(api: Api) -> None:
    version = await enroll(api)
    auth = api.c.auth
    profile = await auth.get_profile(api.profile_id)
    device = await auth.start_device()
    p = await auth.resolve_principal(auth.issue(profile, owner_pin_version=version)["access_token"])
    await auth.approve_device(p, device["user_code"], profile.id)
    await auth.consume_session(p.profile_id, p.token_version, p.session_id)
    with pytest.raises(Unauthorized):
        await auth.poll_device(device["device_code"])


async def test_pin_challenge_cannot_resurrect_consumed_source(api: Api) -> None:
    version = await enroll(api)
    auth = api.c.auth
    profile = await auth.get_profile(api.profile_id)
    p = await auth.resolve_principal(auth.issue(profile, owner_pin_version=version)["access_token"])
    challenge = await api.c.owner_pin.start(p, profile.id, "login")
    await auth.consume_session(p.profile_id, p.token_version, p.session_id)
    with pytest.raises(Unauthorized):
        await api.c.owner_pin.verify(p, challenge["challenge_id"], challenge["poll_token"], FIXTURE_PIN)


async def test_kids_mutation_revokes_sessions_and_cannot_restrict_admin(api: Api) -> None:
    from app.services.errors import ServiceError

    version = await enroll(api)
    auth = api.c.auth
    owner = await auth.get_profile(api.profile_id)
    p = await auth.resolve_principal(auth.issue(owner, owner_pin_version=version)["access_token"])
    with pytest.raises(ServiceError, match="owner profile cannot be restricted"):
        await auth.update_profile(p, owner.id, {"kids": True})
    child = await auth.create_profile(p, {"name": "Child", "is_admin": True, "kids": False})
    assert not child.is_admin
    tokens = auth.issue(child, owner_pin_version=version)
    old_version = child.token_version
    changed = await auth.update_profile(p, child.id, {"kids": True})
    assert changed.token_version == old_version + 1
    unchanged = await auth.update_profile(p, child.id, {"kids": True})
    assert unchanged.token_version == changed.token_version
    with pytest.raises(Unauthorized):
        await auth.resolve_principal(tokens["access_token"])
