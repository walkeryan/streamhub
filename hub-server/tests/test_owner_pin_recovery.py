"""Synthetic recovery only. Never run the local operator command against a real database."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.infra.models import OwnerCredential, OwnerPinChallenge, Profile, utcnow
from app.services.errors import Forbidden, Gone, Unauthorized
from tests.conftest import Api
from tests.test_owner_pin import TEST_PIN, enrolled, principal

NEW_PIN = "48291735"


async def test_local_recovery_replaces_only_on_commit_and_revokes_every_profile(api: Api) -> None:
    service, p = await enrolled(api)
    other_profile = await api.c.auth.create_profile(p, {"name": "Existing child"})
    original_other_version = other_profile.token_version
    status = await service.status(p)
    assert "recovery_request_id" not in status
    recovery = await service.recovery_status(p)
    request_id = recovery["recovery_request_id"]
    assert (await service.recovery_status(p))["recovery_request_id"] == request_id
    assert not recovery["recovery_authorized"] and "grant_secret" not in recovery
    with pytest.raises(Forbidden):
        await service.recover(p, request_id, "not-a-grant", NEW_PIN, p.google_subject)
    await service.approve_recovery_request(request_id)
    approved = await service.recovery_status(p)
    assert approved["recovery_authorized"]
    assert not (await service.recovery_status(await principal(api)))["recovery_authorized"]
    old = await service.start(p, p.profile_id, "login")
    assert (await service.verify(p, old["challenge_id"], old["poll_token"], TEST_PIN)).credential_version == 1
    assert await service.credential_version() == 1
    proof = await service.recover(p, approved["grant_id"], approved["grant_secret"], NEW_PIN, p.google_subject)
    assert proof.action == "login" and proof.credential_version == 2 and proof.target_version == p.token_version + 1
    assert (await api.c.auth.get_profile(other_profile.id)).token_version == original_other_version + 1
    with pytest.raises(Unauthorized):
        await api.c.auth.session_valid(p.profile_id, p.token_version, p.session_id)
    with pytest.raises(Gone):
        await service.recover(p, approved["grant_id"], approved["grant_secret"], NEW_PIN, p.google_subject)
    fresh = await principal(api)
    challenge = await service.start(fresh, fresh.profile_id, "login")
    with pytest.raises(Forbidden):
        await service.verify(fresh, challenge["challenge_id"], challenge["poll_token"], TEST_PIN)
    assert (
        await service.verify(fresh, challenge["challenge_id"], challenge["poll_token"], NEW_PIN)
    ).credential_version == 2
    async with api.c.db.session() as s:
        outstanding = list(
            (
                await s.execute(
                    select(OwnerPinChallenge).where(
                        OwnerPinChallenge.credential_version == 1, OwnerPinChallenge.consumed.is_(False)
                    )
                )
            ).scalars()
        )
        assert not outstanding


async def test_recovery_owner_identity_expiry_and_revocation(api: Api) -> None:
    service, p = await enrolled(api)
    wrong = await principal(api)
    wrong.google_subject = "wrong-google-subject"
    with pytest.raises(Forbidden):
        await service.recovery_status(wrong)
    request_id = (await service.recovery_status(p))["recovery_request_id"]
    async with api.c.db.session() as s:
        await s.execute(
            update(OwnerPinChallenge)
            .where(OwnerPinChallenge.id == request_id)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await s.commit()
    with pytest.raises(Gone):
        await service.approve_recovery_request(request_id)
    request_id = (await service.recovery_status(p))["recovery_request_id"]
    await api.c.auth.consume_session(p.profile_id, p.token_version, p.session_id)
    with pytest.raises(Unauthorized):
        await service.approve_recovery_request(request_id)


async def test_recovery_commit_rechecks_credential_revision_after_hash(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, p = await enrolled(api)
    request_id = (await service.recovery_status(p))["recovery_request_id"]
    await service.approve_recovery_request(request_id)
    approved = await service.recovery_status(p)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._derive

    async def delayed(pin: str, encoded: str | None) -> str:
        entered.set()
        await release.wait()
        return await original(pin, encoded)

    monkeypatch.setattr(service, "_derive", delayed)
    task = asyncio.create_task(
        service.recover(p, approved["grant_id"], approved["grant_secret"], NEW_PIN, p.google_subject)
    )
    await entered.wait()
    async with api.c.db.session() as s:
        before = await s.get(OwnerCredential, 1)
        assert before
        old_hash = before.password_hash
        await s.execute(update(OwnerCredential).where(OwnerCredential.id == 1).values(credential_version=2))
        await s.commit()
    release.set()
    with pytest.raises(Forbidden):
        await task
    async with api.c.db.session() as s:
        after = await s.get(OwnerCredential, 1)
        owner = await s.get(Profile, p.profile_id)
        assert after and after.password_hash == old_hash
        assert owner and owner.token_version == p.token_version


async def test_recovery_requires_current_revision_at_local_approval(api: Api) -> None:
    service, p = await enrolled(api)
    request_id = (await service.recovery_status(p))["recovery_request_id"]
    async with api.c.db.session() as s:
        await s.execute(update(OwnerCredential).where(OwnerCredential.id == 1).values(credential_version=2))
        await s.commit()
    with pytest.raises(Forbidden):
        await service.approve_recovery_request(request_id)
