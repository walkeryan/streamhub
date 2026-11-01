"""Synthetic PINs only: no real owner enrollment, credentials or network calls."""

import asyncio
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import update

from app.infra.models import OwnerCredential, OwnerPinAttempt, OwnerPinChallenge, Profile, utcnow
from app.services.errors import Conflict, Forbidden, Gone, Unauthorized
from app.services.owner_pin import HASH_PREFIX, OwnerPinRateLimited, OwnerPinService
from tests.conftest import Api

TEST_PIN = "73918462"


async def principal(api: Api) -> Any:
    profile = await api.c.auth.get_profile(api.profile_id)
    p = api.c.auth.principal_from_access(api.c.auth.issue(profile)["access_token"])
    return SimpleNamespace(**{**asdict(p), "google_subject": "synthetic-google-subject"})


async def enrolled(api: Api) -> tuple[OwnerPinService, Any]:
    service = OwnerPinService(api.c.auth, api.c.db, api.c.crypto)
    p = await principal(api)
    grant = await service.authorize_setup(p, p.google_subject)
    await service.setup(p, grant["challenge_id"], grant["poll_token"], TEST_PIN, p.google_subject)
    return service, await principal(api)


async def test_setup_requires_local_subject_bound_grant_and_revokes_all_sessions(api: Api) -> None:
    service = OwnerPinService(api.c.auth, api.c.db, api.c.crypto)
    p = await principal(api)
    child = await api.c.auth.create_profile(p, {"name": "Pre-enrollment child"})
    child_version = child.token_version
    assert not await service.configured()
    with pytest.raises(Forbidden):
        await service.start(p, p.profile_id, "login")
    with pytest.raises(Forbidden):
        await service.authorize_setup(p, "wrong-subject")
    grant = await service.authorize_setup(p, p.google_subject)
    status = await service.status(p)
    assert status["setup_authorized"] and status["grant_secret"] == grant["poll_token"]
    other = await principal(api)
    assert not (await service.status(other))["setup_authorized"]
    async with api.c.db.session() as s:
        row = await s.get(OwnerPinChallenge, grant["challenge_id"])
        assert row and row.secret_hash != grant["poll_token"] and row.grant_ciphertext
        assert grant["poll_token"] not in row.grant_ciphertext
    proof = await service.setup(p, grant["challenge_id"], grant["poll_token"], TEST_PIN, p.google_subject)
    assert proof.credential_version == 1 and proof.target_version == p.token_version + 1
    assert (await api.c.auth.get_profile(child.id)).token_version == child_version + 1
    with pytest.raises(Unauthorized):
        await api.c.auth.session_valid(p.profile_id, p.token_version, p.session_id)
    async with api.c.db.session() as s:
        credential = await s.get(OwnerCredential, 1)
        assert credential and credential.password_hash and credential.password_hash.startswith(HASH_PREFIX)
        assert TEST_PIN not in credential.password_hash
    assert await service.configured() and await service.credential_version() == 1
    with pytest.raises(Conflict):
        await service.authorize_setup(await principal(api), p.google_subject)


async def test_pin_challenge_binding_expiry_and_single_use(api: Api) -> None:
    service, p = await enrolled(api)
    challenge = await service.start(p, p.profile_id, "admin")
    cid, secret = challenge["challenge_id"], challenge["poll_token"]
    with pytest.raises(Forbidden):
        await service.verify(await principal(api), cid, secret, TEST_PIN)
    with pytest.raises(Forbidden):
        await service.verify(p, cid, "wrong-secret", TEST_PIN)
    proof = await service.verify(p, cid, secret, TEST_PIN)
    assert proof.action == "admin" and proof.source_session_id == p.session_id
    with pytest.raises(Gone):
        await service.verify(p, cid, secret, TEST_PIN)
    challenge = await service.start(p, p.profile_id, "admin")
    async with api.c.db.session() as s:
        await s.execute(
            update(OwnerPinChallenge)
            .where(OwnerPinChallenge.id == challenge["challenge_id"])
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await s.commit()
    with pytest.raises(Gone):
        await service.verify(p, challenge["challenge_id"], challenge["poll_token"], TEST_PIN)


async def test_global_failed_attempt_budget_survives_new_service(api: Api) -> None:
    service, p = await enrolled(api)
    for _ in range(5):
        challenge = await service.start(p, p.profile_id, "login")
        with pytest.raises(Forbidden) as error:
            await service.verify(p, challenge["challenge_id"], challenge["poll_token"], "111111")
        assert error.value.code == "owner_pin_invalid"
    service = OwnerPinService(api.c.auth, api.c.db, api.c.crypto)
    challenge = await service.start(p, p.profile_id, "login")
    with pytest.raises(OwnerPinRateLimited) as error:
        await service.verify(p, challenge["challenge_id"], challenge["poll_token"], TEST_PIN)
    assert error.value.status == 429 and error.value.retry_after == 900
    async with api.c.db.session() as s:
        await s.execute(
            update(OwnerPinAttempt)
            .where(OwnerPinAttempt.succeeded.is_(False))
            .values(created_at=utcnow() - timedelta(minutes=16))
        )
        for i in range(15):
            s.add(OwnerPinAttempt(id=f"older-failure-{i}", created_at=utcnow() - timedelta(hours=1), succeeded=False))
        await s.commit()
    with pytest.raises(OwnerPinRateLimited) as daily:
        await service.verify(p, challenge["challenge_id"], challenge["poll_token"], TEST_PIN)
    assert daily.value.retry_after == 86400


async def test_concurrent_hash_lease_and_cancel_during_hash(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    service, p = await enrolled(api)
    challenge = await service.start(p, p.profile_id, "admin")
    cid, secret = challenge["challenge_id"], challenge["poll_token"]
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._derive

    async def delayed(pin: str, encoded: str | None) -> str:
        entered.set()
        await release.wait()
        return await original(pin, encoded)

    monkeypatch.setattr(service, "_derive", delayed)
    task = asyncio.create_task(service.verify(p, cid, secret, TEST_PIN))
    await entered.wait()
    other = OwnerPinService(api.c.auth, api.c.db, api.c.crypto)
    with pytest.raises(Conflict):
        await other.verify(p, cid, secret, TEST_PIN)
    await service.cancel(p, cid, secret)
    release.set()
    with pytest.raises(Forbidden):
        await task
    async with api.c.db.session() as s:
        credential = await s.get(OwnerCredential, 1)
        assert credential and credential.lease_id is None


async def test_target_version_change_and_source_revocation_fail_closed(api: Api) -> None:
    service, p = await enrolled(api)
    challenge = await service.start(p, p.profile_id, "admin")
    await api.c.auth.consume_session(p.profile_id, p.token_version, p.session_id)
    with pytest.raises(Unauthorized):
        await service.verify(p, challenge["challenge_id"], challenge["poll_token"], TEST_PIN)
    p = await principal(api)
    target = await api.c.auth.create_profile(p, {"name": "Synthetic target"})
    challenge = await service.start(p, target.id, "profile_switch")
    async with api.c.db.session() as s:
        await s.execute(update(Profile).where(Profile.id == target.id).values(token_version=Profile.token_version + 1))
        await s.commit()
    with pytest.raises(Forbidden):
        await service.verify(p, challenge["challenge_id"], challenge["poll_token"], TEST_PIN)


async def test_expired_hash_lease_recovers_and_credential_change_invalidates_challenge(api: Api) -> None:
    service, p = await enrolled(api)
    challenge = await service.start(p, p.profile_id, "admin")
    async with api.c.db.session() as s:
        await s.execute(
            update(OwnerCredential)
            .where(OwnerCredential.id == 1)
            .values(lease_id="crashed-worker", lease_expires=utcnow() - timedelta(seconds=1))
        )
        await s.commit()
    proof = await service.verify(p, challenge["challenge_id"], challenge["poll_token"], TEST_PIN)
    assert proof.credential_version == 1
    challenge = await service.start(p, p.profile_id, "admin")
    async with api.c.db.session() as s:
        await s.execute(update(OwnerCredential).where(OwnerCredential.id == 1).values(credential_version=2))
        await s.commit()
    with pytest.raises(Forbidden):
        await service.verify(p, challenge["challenge_id"], challenge["poll_token"], TEST_PIN)


async def test_repeated_cancellation_waits_for_hash_worker(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    service = OwnerPinService(api.c.auth, api.c.db, api.c.crypto)
    entered, release = threading.Event(), threading.Event()

    def blocked(pin: str, encoded: str | None) -> str:
        entered.set()
        assert release.wait(timeout=5)
        return "synthetic-result"

    monkeypatch.setattr(service, "_hash", blocked)
    task = asyncio.create_task(service._derive(TEST_PIN, None))
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_status_requests_local_approval_without_enrolling_or_exposing_bearer(api: Api) -> None:
    service = OwnerPinService(api.c.auth, api.c.db, api.c.crypto)
    p = await principal(api)
    status = await service.status(p)
    assert status["local_approval_required"] and not status["setup_authorized"]
    assert "grant_secret" not in status
    request_id = status["setup_request_id"]
    assert (await service.status(p))["setup_request_id"] == request_id
    assert not await service.configured()
    approval = await service.approve_setup_request(request_id)
    assert "grant_secret" not in approval and "poll_token" not in approval
    status = await service.status(p)
    assert status["setup_authorized"] and status["grant_id"] == approval["grant_id"]
    other = await principal(api)
    assert not (await service.status(other))["setup_authorized"]
    with pytest.raises(Gone):
        await service.approve_setup_request(request_id)
    proof = await service.setup(p, status["grant_id"], status["grant_secret"], TEST_PIN, p.google_subject)
    assert proof.credential_version == 1


async def test_local_setup_request_expires_and_cannot_approve_revoked_session(api: Api) -> None:
    service = OwnerPinService(api.c.auth, api.c.db, api.c.crypto)
    p = await principal(api)
    request_id = (await service.status(p))["setup_request_id"]
    async with api.c.db.session() as s:
        await s.execute(
            update(OwnerPinChallenge)
            .where(OwnerPinChallenge.id == request_id)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await s.commit()
    with pytest.raises(Gone):
        await service.approve_setup_request(request_id)
    request_id = (await service.status(p))["setup_request_id"]
    await api.c.auth.consume_session(p.profile_id, p.token_version, p.session_id)
    with pytest.raises(Unauthorized):
        await service.approve_setup_request(request_id)
