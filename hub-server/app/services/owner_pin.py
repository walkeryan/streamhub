"""Owner PIN boundary. No HTTP enrollment authority, plaintext persistence, or logging."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy import delete, func, literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert

from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.models import OwnerCredential, OwnerPinAttempt, OwnerPinChallenge, Profile, RevokedSession, utcnow
from app.services.auth import AuthService, Principal
from app.services.errors import Conflict, Forbidden, Gone, ServiceError

ACTIONS = frozenset({"login", "profile_switch", "admin"})
HASH_PREFIX = "scrypt-v1$131072$8$1"


@dataclass(frozen=True)
class OwnerPinProof:
    source_profile_id: str
    source_version: int
    source_session_id: str
    target_profile_id: str
    target_version: int
    action: str
    credential_version: int


class OwnerPinRateLimited(ServiceError):
    status = 429
    code = "rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__("Owner PIN attempt limit reached; try later")
        self.retry_after = retry_after


class OwnerPinService:
    def __init__(self, auth: AuthService, db: Database, crypto: Crypto) -> None:
        self.auth, self.db, self.crypto = auth, db, crypto

    async def _credential(self) -> OwnerCredential:
        async with self.db.session() as s:
            existing = await s.get(OwnerCredential, 1)
            if existing is not None:
                return existing
            await s.execute(insert(OwnerCredential).values(id=1, credential_version=0).on_conflict_do_nothing())
            await s.commit()
            row = await s.get(OwnerCredential, 1)
            assert row is not None
            return row

    async def configured(self) -> bool:
        return bool((await self._credential()).password_hash)

    async def credential_version(self) -> int:
        row = await self._credential()
        return row.credential_version if row.password_hash else 0

    async def _new(
        self, p: Principal, target: str, action: str, subject: str | None = None, expected_version: int | None = None
    ) -> dict[str, Any]:
        await self.auth.session_valid(p.profile_id, p.token_version, p.session_id)
        destination = await self.auth.get_profile(target)
        credential = await self._credential()
        if expected_version is not None and credential.credential_version != expected_version:
            raise Forbidden("Owner credential changed", code="challenge_invalid")
        if action not in {"setup", "setup_request"} and not credential.password_hash:
            raise Forbidden("Owner PIN has not been configured", code="owner_pin_not_configured")
        cid, secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = utcnow()
        async with self.db.session() as s:
            await s.execute(delete(OwnerPinChallenge).where(OwnerPinChallenge.expires_at < now))
            # Keep setup grants independent of ordinary login challenge creation.
            await s.execute(
                update(OwnerPinChallenge)
                .where(
                    OwnerPinChallenge.source_session_id == p.session_id,
                    OwnerPinChallenge.action == action,
                )
                .values(consumed=True)
            )
            s.add(
                OwnerPinChallenge(
                    id=cid,
                    secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
                    grant_ciphertext=self.crypto.encrypt(secret.encode()) if action in {"setup", "recovery"} else None,
                    source_profile_id=p.profile_id,
                    source_version=p.token_version,
                    source_session_id=p.session_id,
                    target_profile_id=target,
                    target_version=destination.token_version,
                    action=action,
                    credential_version=credential.credential_version,
                    google_subject=subject,
                    expires_at=now + timedelta(minutes=10 if action in {"setup_request", "recovery_request"} else 5),
                    consumed=False,
                )
            )
            await s.commit()
        return {"challenge_id": cid, "poll_token": secret, "expires_in": 300}

    async def start(self, p: Principal, target: str, action: str) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ServiceError("Invalid owner PIN action")
        if action == "login" and target != p.profile_id:
            raise Forbidden("Owner PIN action must target the initiating profile")
        if action == "admin":
            current = await self.auth.get_profile(p.profile_id)
            if not self.auth.effective_admin(current):
                raise Forbidden("Only the owner can approve administrative changes", code="owner_pin_invalid")
        return await self._new(p, target, action)

    async def authorize_setup(self, owner: Principal, google_subject: str) -> dict[str, Any]:
        """LOCAL OPERATOR ONLY. Never expose this method via an HTTP endpoint."""
        profile = await self.auth.session_valid(owner.profile_id, owner.token_version, owner.session_id)
        if (
            not self.auth.effective_admin(profile)
            or not google_subject
            or google_subject != getattr(owner, "google_subject", "")
        ):
            raise Forbidden("Local setup requires the authenticated Google owner subject")
        if await self.configured():
            raise Conflict("Owner PIN already configured; use the separately authorized local recovery procedure")
        return await self._new(owner, owner.profile_id, "setup", google_subject)

    async def status(self, p: Principal) -> dict[str, Any]:
        profile = await self.auth.session_valid(p.profile_id, p.token_version, p.session_id)
        configured = await self.configured()
        result: dict[str, Any] = {
            "configured": configured,
            "setup_authorized": False,
            "local_approval_required": not configured,
        }
        if configured:
            return result
        subject = getattr(p, "google_subject", "")
        if not subject or not self.auth.effective_admin(profile):
            return result
        async with self.db.session() as s:
            row = (
                await s.execute(
                    select(OwnerPinChallenge).where(
                        OwnerPinChallenge.action == "setup",
                        OwnerPinChallenge.source_profile_id == p.profile_id,
                        OwnerPinChallenge.source_version == p.token_version,
                        OwnerPinChallenge.source_session_id == p.session_id,
                        OwnerPinChallenge.google_subject == subject,
                        OwnerPinChallenge.consumed.is_(False),
                        OwnerPinChallenge.expires_at > utcnow(),
                    )
                )
            ).scalar_one_or_none()
        if row is not None and row.grant_ciphertext:
            secret = self.crypto.decrypt(row.grant_ciphertext, ttl=300).decode()
            result.update(setup_authorized=True, local_approval_required=False, grant_id=row.id, grant_secret=secret)
            return result
        async with self.db.session() as s:
            request = (
                await s.execute(
                    select(OwnerPinChallenge).where(
                        OwnerPinChallenge.action == "setup_request",
                        OwnerPinChallenge.source_profile_id == p.profile_id,
                        OwnerPinChallenge.source_version == p.token_version,
                        OwnerPinChallenge.source_session_id == p.session_id,
                        OwnerPinChallenge.google_subject == subject,
                        OwnerPinChallenge.consumed.is_(False),
                        OwnerPinChallenge.expires_at > utcnow(),
                    )
                )
            ).scalar_one_or_none()
        if request is None:
            created = await self._new(p, p.profile_id, "setup_request", subject)
            result["setup_request_id"] = created["challenge_id"]
        else:
            result["setup_request_id"] = request.id
        return result

    async def approve_setup_request(self, request_id: str) -> dict[str, Any]:
        """LOCAL OPERATOR ONLY: approve exactly one displayed nonsecret request ID.

        The request was created from an authenticated subject/session by status().
        This method must never be wired to HTTP or invoked automatically.
        """
        async with self.db.session() as s:
            request = await s.get(OwnerPinChallenge, request_id)
        if (
            request is None
            or request.action != "setup_request"
            or request.consumed
            or request.expires_at <= utcnow()
            or not request.google_subject
        ):
            raise Gone("Local setup request expired or consumed", code="owner_pin_expired")
        profile = await self.auth.session_valid(
            request.source_profile_id, request.source_version, request.source_session_id
        )
        owner = cast(
            Principal,
            SimpleNamespace(
                profile_id=profile.id,
                token_version=profile.token_version,
                session_id=request.source_session_id,
                google_subject=request.google_subject,
                is_admin=self.auth.effective_admin(profile),
                kids=profile.kids,
            ),
        )
        async with self.db.session() as s:
            consumed = await s.execute(
                update(OwnerPinChallenge)
                .where(
                    OwnerPinChallenge.id == request_id,
                    OwnerPinChallenge.consumed.is_(False),
                    OwnerPinChallenge.expires_at > utcnow(),
                )
                .values(consumed=True)
                .returning(OwnerPinChallenge.id)
            )
            if consumed.scalar_one_or_none() is None:
                raise Gone("Local setup request already consumed", code="owner_pin_expired")
            await s.commit()
        grant = await self.authorize_setup(owner, request.google_subject)
        # Only the bound authenticated browser receives the decrypted secret through status().
        return {"request_id": request_id, "grant_id": grant["challenge_id"], "expires_in": 300}

    async def _recovery_owner(self, p: Principal) -> OwnerCredential:
        profile = await self.auth.session_valid(p.profile_id, p.token_version, p.session_id)
        credential = await self._credential()
        if (
            not credential.password_hash
            or not self.auth.effective_admin(profile)
            or credential.owner_profile_id != p.profile_id
            or not credential.google_subject
            or credential.google_subject != getattr(p, "google_subject", "")
        ):
            raise Forbidden("Recovery requires the configured Google owner", code="owner_pin_invalid")
        return credential

    async def recovery_status(self, p: Principal) -> dict[str, Any]:
        """Explicit recovery request, separate from ordinary configured status()."""
        credential = await self._recovery_owner(p)
        result: dict[str, Any] = {"configured": True, "recovery_authorized": False, "local_approval_required": True}
        async with self.db.session() as s:
            rows = list(
                (
                    await s.execute(
                        select(OwnerPinChallenge).where(
                            OwnerPinChallenge.action.in_(["recovery", "recovery_request"]),
                            OwnerPinChallenge.source_profile_id == p.profile_id,
                            OwnerPinChallenge.source_version == p.token_version,
                            OwnerPinChallenge.source_session_id == p.session_id,
                            OwnerPinChallenge.google_subject == credential.google_subject,
                            OwnerPinChallenge.credential_version == credential.credential_version,
                            OwnerPinChallenge.consumed.is_(False),
                            OwnerPinChallenge.expires_at > utcnow(),
                        )
                    )
                ).scalars()
            )
        grant = next((r for r in rows if r.action == "recovery"), None)
        if grant is not None and grant.grant_ciphertext:
            result.update(
                recovery_authorized=True,
                local_approval_required=False,
                grant_id=grant.id,
                grant_secret=self.crypto.decrypt(grant.grant_ciphertext, ttl=300).decode(),
                expires_in=300,
            )
            return result
        request = next((r for r in rows if r.action == "recovery_request"), None)
        if request is None:
            created = await self._new(
                p, p.profile_id, "recovery_request", credential.google_subject, credential.credential_version
            )
            result["recovery_request_id"] = created["challenge_id"]
        else:
            result["recovery_request_id"] = request.id
        return result

    async def approve_recovery_request(self, request_id: str) -> dict[str, Any]:
        """LOCAL OPERATOR ONLY. Never wire to HTTP or automatically approve a request."""
        async with self.db.session() as s:
            request = await s.get(OwnerPinChallenge, request_id)
        if (
            request is None
            or request.action != "recovery_request"
            or request.consumed
            or request.expires_at <= utcnow()
            or not request.google_subject
        ):
            raise Gone("Local recovery request expired or consumed", code="owner_pin_expired")
        owner = cast(
            Principal,
            SimpleNamespace(
                profile_id=request.source_profile_id,
                token_version=request.source_version,
                session_id=request.source_session_id,
                google_subject=request.google_subject,
            ),
        )
        credential = await self._recovery_owner(owner)
        if credential.credential_version != request.credential_version:
            raise Forbidden("Owner credential changed", code="challenge_invalid")
        async with self.db.session() as s:
            consumed = await s.execute(
                update(OwnerPinChallenge)
                .where(
                    OwnerPinChallenge.id == request_id,
                    OwnerPinChallenge.consumed.is_(False),
                    OwnerPinChallenge.expires_at > utcnow(),
                )
                .values(consumed=True)
                .returning(OwnerPinChallenge.id)
            )
            if consumed.scalar_one_or_none() is None:
                raise Gone("Local recovery request already consumed", code="owner_pin_expired")
            await s.commit()
        grant = await self._new(
            owner, owner.profile_id, "recovery", credential.google_subject, request.credential_version
        )
        return {"request_id": request_id, "grant_id": grant["challenge_id"], "expires_in": 300}

    async def recover(self, p: Principal, cid: str, secret: str, new_pin: str, google_subject: str) -> OwnerPinProof:
        row = await self._bound(p, cid, secret)
        credential = await self._recovery_owner(p)
        if (
            row.action != "recovery"
            or not google_subject
            or row.google_subject != google_subject
            or credential.google_subject != google_subject
            or row.target_profile_id != p.profile_id
        ):
            raise Forbidden("A matching local recovery grant is required", code="challenge_invalid")
        lease = await self._reserve(row.credential_version, recovery_challenge=row.id)
        try:
            if not self._pin_valid(new_pin):
                raise ServiceError("Owner PIN must contain 6 to 12 ASCII digits", code="owner_pin_invalid")
            derived = await self._derive(new_pin, None)
            return await self._consume(row, lease, derived)
        finally:
            await self._release(lease)

    async def _bound(self, p: Principal, cid: str, secret: str) -> OwnerPinChallenge:
        if len(secret) > 128:
            raise Forbidden("Invalid owner PIN challenge", code="challenge_invalid")
        async with self.db.session() as s:
            row = await s.get(OwnerPinChallenge, cid)
        if row is None or row.consumed or row.expires_at <= utcnow():
            raise Gone("Owner PIN challenge expired or consumed", code="owner_pin_expired")
        if not hmac.compare_digest(row.secret_hash, hashlib.sha256(secret.encode()).hexdigest()) or (
            row.source_profile_id,
            row.source_version,
            row.source_session_id,
        ) != (p.profile_id, p.token_version, p.session_id):
            raise Forbidden("Owner PIN challenge belongs to another session", code="challenge_invalid")
        await self.auth.session_valid(p.profile_id, p.token_version, p.session_id)
        target = await self.auth.get_profile(row.target_profile_id)
        credential = await self._credential()
        if target.token_version != row.target_version or credential.credential_version != row.credential_version:
            raise Forbidden("Owner PIN challenge binding changed", code="challenge_invalid")
        return row

    async def cancel(self, p: Principal, cid: str, secret: str) -> None:
        await self._bound(p, cid, secret)
        async with self.db.session() as s:
            await s.execute(update(OwnerPinChallenge).where(OwnerPinChallenge.id == cid).values(consumed=True))
            await s.commit()

    async def _reserve(self, version: int, *, recovery_challenge: str | None = None) -> str:
        """One atomic writer reservation before counting/reserving a globally bounded attempt."""
        now, lease = utcnow(), secrets.token_hex(16)
        async with self.db.session() as s:
            acquired = await s.execute(
                update(OwnerCredential)
                .where(
                    OwnerCredential.id == 1,
                    OwnerCredential.credential_version == version,
                    or_(OwnerCredential.lease_expires.is_(None), OwnerCredential.lease_expires <= now),
                )
                .values(lease_id=lease, lease_expires=now + timedelta(minutes=5))
                .returning(OwnerCredential.id)
            )
            if acquired.scalar_one_or_none() is None:
                raise Conflict("Owner PIN verification is busy; try again shortly", code="owner_pin_busy")
            for seconds, maximum in ((300, 3),) if recovery_challenge else ((900, 5), (86400, 20)):
                count = (
                    await s.execute(
                        select(func.count())
                        .select_from(OwnerPinAttempt)
                        .where(
                            OwnerPinAttempt.created_at > now - timedelta(seconds=seconds),
                            OwnerPinAttempt.credential_version == version,
                            OwnerPinAttempt.challenge_id == recovery_challenge
                            if recovery_challenge
                            else OwnerPinAttempt.purpose == "verify",
                            literal(True) if recovery_challenge else OwnerPinAttempt.succeeded.is_(False),
                        )
                    )
                ).scalar_one()
                if count >= maximum:
                    raise OwnerPinRateLimited(seconds)
            await s.execute(delete(OwnerPinAttempt).where(OwnerPinAttempt.created_at < now - timedelta(days=1)))
            s.add(
                OwnerPinAttempt(
                    id=lease,
                    created_at=now,
                    succeeded=False,
                    credential_version=version,
                    purpose="recovery" if recovery_challenge else "verify",
                    challenge_id=recovery_challenge,
                )
            )
            await s.commit()
        return lease

    @staticmethod
    def _pin_valid(pin: str) -> bool:
        return 6 <= len(pin) <= 12 and pin.isascii() and pin.isdigit()

    @staticmethod
    def _hash(pin: str, encoded: str | None = None) -> str:
        salt = secrets.token_bytes(32)
        if encoded is not None:
            pieces = encoded.split("$")
            if len(pieces) != 6 or "$".join(pieces[:4]) != HASH_PREFIX:
                raise Forbidden("Owner PIN credential format is invalid")
            try:
                salt = bytes.fromhex(pieces[4])
                if len(salt) != 32 or len(bytes.fromhex(pieces[5])) != 32:
                    raise ValueError("length")
            except ValueError as exc:
                raise Forbidden("Owner PIN credential format is invalid") from exc
        derived = hashlib.scrypt(pin.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024, dklen=32)
        return f"{HASH_PREFIX}${salt.hex()}${derived.hex()}"

    async def _derive(self, pin: str, encoded: str | None) -> str:
        # Cancellation must not release the global lease while the hashing thread still runs.
        task = asyncio.create_task(asyncio.to_thread(self._hash, pin, encoded))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            # Even repeated cancellation cannot free the lease before the worker exits.
            if not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        return task.result()

    async def _release(self, lease: str) -> None:
        async with self.db.session() as s:
            await s.execute(
                update(OwnerCredential)
                .where(OwnerCredential.id == 1, OwnerCredential.lease_id == lease)
                .values(lease_id=None, lease_expires=None)
            )
            await s.commit()

    async def _consume(self, row: OwnerPinChallenge, lease: str, new_hash: str | None = None) -> OwnerPinProof:
        now = utcnow()
        async with self.db.session() as s:
            # Commit proof, challenge consumption, and successful-attempt record together.
            live_source = (
                select(Profile.id)
                .where(Profile.id == row.source_profile_id, Profile.token_version == row.source_version)
                .exists()
            )
            live_target = (
                select(Profile.id)
                .where(Profile.id == row.target_profile_id, Profile.token_version == row.target_version)
                .exists()
            )
            not_revoked = (
                ~select(RevokedSession.session_id).where(RevokedSession.session_id == row.source_session_id).exists()
            )
            credential = (
                select(OwnerCredential.id)
                .where(
                    OwnerCredential.id == 1,
                    OwnerCredential.credential_version == row.credential_version,
                    OwnerCredential.lease_id == lease,
                    OwnerCredential.lease_expires > now,
                )
                .exists()
            )
            if row.action == "recovery":
                credential = (
                    select(OwnerCredential.id)
                    .where(
                        credential,
                        OwnerCredential.id == 1,
                        OwnerCredential.owner_profile_id == row.source_profile_id,
                        OwnerCredential.google_subject == row.google_subject,
                        OwnerCredential.password_hash.is_not(None),
                    )
                    .exists()
                )
            result = await s.execute(
                update(OwnerPinChallenge)
                .where(
                    OwnerPinChallenge.id == row.id,
                    OwnerPinChallenge.consumed.is_(False),
                    OwnerPinChallenge.expires_at > now,
                    live_source,
                    live_target,
                    not_revoked,
                    credential,
                )
                .values(consumed=True)
                .returning(OwnerPinChallenge.id)
            )
            if result.scalar_one_or_none() is None:
                raise Forbidden("Owner PIN challenge no longer valid")
            version = row.credential_version
            target_version = row.target_version
            if new_hash is not None:
                version += 1
                target_version += 1
                await s.execute(update(Profile).values(token_version=Profile.token_version + 1))
                await s.execute(update(OwnerPinChallenge).values(consumed=True))
                await s.execute(
                    update(OwnerCredential)
                    .where(OwnerCredential.id == 1)
                    .values(
                        password_hash=new_hash,
                        credential_version=version,
                        owner_profile_id=row.source_profile_id,
                        google_subject=row.google_subject,
                    )
                )
            await s.execute(update(OwnerPinAttempt).where(OwnerPinAttempt.id == lease).values(succeeded=True))
            await s.commit()
        return OwnerPinProof(
            row.source_profile_id,
            row.source_version,
            row.source_session_id,
            row.target_profile_id,
            target_version,
            "login" if row.action in {"setup", "recovery"} else row.action,
            version,
        )

    async def verify(self, p: Principal, cid: str, secret: str, pin: str) -> OwnerPinProof:
        row = await self._bound(p, cid, secret)
        if row.action not in ACTIONS:
            raise Forbidden("Use the authorized setup operation")
        credential = await self._credential()
        if not credential.password_hash:
            raise Forbidden("Owner PIN has not been configured", code="owner_pin_not_configured")
        lease = await self._reserve(row.credential_version)
        try:
            if not self._pin_valid(pin):
                raise Forbidden("Invalid owner PIN", code="owner_pin_invalid")
            derived = await self._derive(pin, credential.password_hash)
            if not hmac.compare_digest(derived, credential.password_hash):
                raise Forbidden("Invalid owner PIN", code="owner_pin_invalid")
            return await self._consume(row, lease)
        finally:
            await self._release(lease)

    async def setup(self, p: Principal, cid: str, secret: str, pin: str, google_subject: str) -> OwnerPinProof:
        row = await self._bound(p, cid, secret)
        if (
            row.action != "setup"
            or not google_subject
            or row.google_subject != google_subject
            or google_subject != getattr(p, "google_subject", "")
            or await self.configured()
        ):
            raise Forbidden("A matching local setup grant is required")
        profile = await self.auth.get_profile(p.profile_id)
        if not self.auth.effective_admin(profile):
            raise Forbidden("Owner setup authorization changed")
        lease = await self._reserve(row.credential_version)
        try:
            if not self._pin_valid(pin):
                raise ServiceError("Owner PIN must contain 6 to 12 ASCII digits", code="owner_pin_invalid")
            derived = await self._derive(pin, None)
            return await self._consume(row, lease, derived)
        finally:
            await self._release(lease)
