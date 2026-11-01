"""Profiles + authentication (JWT access/refresh, PIN, device-code pairing)."""

from __future__ import annotations

import logging
import math
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.sqlite import insert

from app.infra.crypto import Crypto, hash_secret, verify_secret
from app.infra.db import Database
from app.infra.models import DeviceCode, DisclaimerAcceptance, Profile, RevokedSession, utcnow
from app.services.errors import (
    Conflict,
    Forbidden,
    Gone,
    NotFound,
    PreconditionRequired,
    ServiceError,
    Unauthorized,
)

log = logging.getLogger(__name__)

ALG = "HS256"
USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
DEVICE_CODE_TTL = 600
DEVICE_POLL_INTERVAL = 5
MAX_PROFILES = 12


@dataclass(slots=True)
class Principal:
    profile_id: str
    is_admin: bool
    kids: bool
    token_version: int = 0
    session_id: str = ""
    owner_auth_time: float | None = None
    owner_pin_version: int = 0
    google_subject: str = ""


class AuthService:
    def __init__(self, db: Database, crypto: Crypto, access_ttl: int = 900, refresh_ttl: int = 30 * 86400) -> None:
        self._db = db
        self._key = crypto.jwt_key
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        self._fail: dict[str, list[float]] = {}
        self.content_policy: Any = None
        self.owner_pin: Any = None

    # ------------------------------------------------------------------ bootstrap
    async def ensure_admin(self, password: str | None) -> str | None:
        """Create the admin profile on first boot. Returns the generated password if one was generated."""
        async with self._db.session() as s:
            count = (await s.execute(select(func.count(Profile.id)))).scalar_one()
            if count:
                return None
            generated = None
            if not password:
                generated = secrets.token_urlsafe(12)
                password = generated
            s.add(
                Profile(
                    id=secrets.token_hex(8),
                    name="Admin",
                    is_admin=True,
                    theme_id="midnight",
                    languages=[],
                    password_hash=hash_secret(password),
                    prefs={},
                )
            )
            await s.commit()
            return generated

    # ------------------------------------------------------------------ tokens
    def _encode(
        self,
        profile: Profile,
        typ: str,
        ttl: int,
        session_id: str = "",
        owner_auth_time: float | None = None,
        owner_pin_version: int = 0,
        google_subject: str = "",
    ) -> str:
        now = int(time.time())
        payload = {
            "sub": profile.id,
            "typ": typ,
            "adm": self.effective_admin(profile),
            "kid": profile.kids,
            "ver": profile.token_version,
            "iat": now,
            "exp": now + ttl,
            "jti": secrets.token_hex(8),
            "sid": session_id or secrets.token_hex(16),
            "opv": owner_pin_version,
            "gs": google_subject,
        }
        if owner_auth_time is not None:
            payload["oat"] = owner_auth_time
        return jwt.encode(payload, self._key, algorithm=ALG)

    def effective_admin(self, profile: Profile) -> bool:
        return bool(
            profile.is_admin and not (self.content_policy is not None and self.content_policy.is_restricted(profile))
        )

    @staticmethod
    def owner_time(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return float(value)

    def issue(
        self,
        profile: Profile,
        session_id: str = "",
        owner_auth_time: float | None = None,
        owner_pin_version: int = 0,
        google_subject: str = "",
    ) -> dict[str, str]:
        owner_auth_time = self.owner_time(
            owner_auth_time if owner_auth_time is not None else getattr(profile, "_owner_auth_time", None)
        )
        session_id = session_id or secrets.token_hex(16)
        google_subject = google_subject or getattr(profile, "_google_subject", "")
        return {
            "access_token": self._encode(
                profile, "access", self.access_ttl, session_id, owner_auth_time, owner_pin_version, google_subject
            ),
            "refresh_token": self._encode(
                profile, "refresh", self.refresh_ttl, session_id, owner_auth_time, owner_pin_version, google_subject
            ),
        }

    def _decode(self, token: str, typ: str) -> dict[str, Any]:
        try:
            payload = jwt.decode(token, self._key, algorithms=[ALG])
        except jwt.ExpiredSignatureError as exc:
            raise Unauthorized("token expired", code="token_expired") from exc
        except jwt.PyJWTError as exc:
            raise Unauthorized("invalid token") from exc
        if payload.get("typ") != typ:
            raise Unauthorized("wrong token type")
        return payload

    def principal_from_access(self, token: str) -> Principal:
        p = self._decode(token, "access")
        return Principal(
            profile_id=str(p["sub"]),
            is_admin=bool(p.get("adm")),
            kids=bool(p.get("kid")),
            token_version=int(p.get("ver", -1)),
            session_id=str(p.get("sid") or ""),
            owner_auth_time=self.owner_time(p.get("oat")),
            owner_pin_version=int(p.get("opv", 0)),
            google_subject=str(p.get("gs") or ""),
        )

    async def session_valid(self, profile_id: str, version: int, session_id: str) -> Profile:
        async with self._db.session() as session:
            profile = await session.get(Profile, profile_id)
            revoked = await session.get(RevokedSession, session_id) if session_id else None
        if profile is None or profile.token_version != version or not session_id or revoked is not None:
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        return profile

    async def resolve_principal(self, token: str) -> Principal:
        p = self.principal_from_access(token)
        if self.content_policy is None or not self.content_policy.enabled:
            return p
        profile = await self.session_valid(p.profile_id, p.token_version, p.session_id)
        await self.check_owner_entry(profile, p.owner_pin_version)
        return Principal(
            profile.id,
            self.effective_admin(profile),
            profile.kids,
            profile.token_version,
            p.session_id,
            p.owner_auth_time,
            p.owner_pin_version,
            p.google_subject,
        )

    async def ensure_pin_ready(self) -> None:
        if self.owner_pin is None or not await self.owner_pin.configured():
            raise Conflict("Set up the owner PIN with local owner approval first", code="owner_pin_setup_required")

    async def check_owner_entry(self, profile: Profile, version: int) -> None:
        if self.content_policy is None or not self.content_policy.enabled:
            return
        await self.ensure_pin_ready()
        if not self.content_policy.is_restricted(profile):
            current = await self.owner_pin.credential_version()
            if not version or version != current:
                raise Forbidden("Owner PIN verification required", code="owner_pin_required")

    def owner_pending(self, profile: Profile) -> str:
        return self._encode(profile, "owner_pending", 600, google_subject=getattr(profile, "_google_subject", ""))

    async def validate_owner_pending(self, token: str) -> Principal:
        value = self._decode(token, "owner_pending")
        profile = await self.session_valid(str(value["sub"]), int(value["ver"]), str(value["sid"]))
        subject = str(value.get("gs") or "")
        if not profile.is_admin or not subject:
            raise Unauthorized("Verified Google owner identity required")
        return Principal(
            profile.id, True, profile.kids, profile.token_version, str(value["sid"]), google_subject=subject
        )

    def issue_owner_action(self, p: Principal, target: Profile, credential_version: int) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "typ": "owner_action",
                "sub": p.profile_id,
                "sid": p.session_id,
                "ver": p.token_version,
                "target": target.id,
                "target_ver": target.token_version,
                "action": "content_policy",
                "opv": credential_version,
                "jti": secrets.token_hex(16),
                "iat": now,
                "exp": now + 120,
            },
            self._key,
            algorithm=ALG,
        )

    async def validate_owner_action(self, token: str, p: Principal, target_id: str) -> dict[str, Any]:
        if not token:
            raise Forbidden("Verify owner PIN for this policy change", code="owner_pin_required")
        claims = self._decode(token, "owner_action")
        if (claims.get("sub"), claims.get("sid"), claims.get("ver"), claims.get("target"), claims.get("action")) != (
            p.profile_id,
            p.session_id,
            p.token_version,
            target_id,
            "content_policy",
        ):
            raise Forbidden("Owner approval belongs to another action", code="owner_pin_invalid")
        profile = await self.session_valid(p.profile_id, p.token_version, p.session_id)
        await self.check_owner_entry(profile, int(claims.get("opv", 0)))
        target = await self.get_profile(target_id)
        if target.token_version != claims.get("target_ver") or not self.effective_admin(profile):
            raise Forbidden("Owner approval expired after profile changes", code="owner_pin_invalid")
        return claims

    async def revoke_session(self, session_id: str) -> None:
        if not session_id:
            return

        async with self._db.session() as session:
            statement = insert(RevokedSession).values(
                session_id=session_id, expires_at=utcnow() + timedelta(seconds=self.refresh_ttl)
            )
            await session.execute(statement.on_conflict_do_nothing())
            await session.execute(delete(RevokedSession).where(RevokedSession.expires_at < utcnow()))
            await session.commit()

    async def consume_session(self, profile_id: str, version: int, session_id: str) -> None:
        """Atomically claim a live session for one transition, across workers/processes."""
        if not session_id:
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        async with self._db.session() as session:
            live = select(literal(session_id), literal(utcnow() + timedelta(seconds=self.refresh_ttl))).where(
                select(Profile.id).where(Profile.id == profile_id, Profile.token_version == version).exists(),
                ~select(RevokedSession.session_id).where(RevokedSession.session_id == session_id).exists(),
            )
            statement = insert(RevokedSession).from_select(["session_id", "expires_at"], live)
            result = await session.execute(statement.on_conflict_do_nothing().returning(RevokedSession.session_id))
            if result.scalar_one_or_none() is None:
                raise Unauthorized("Session already consumed or revoked", code="session_revoked")
            await session.commit()

    async def switch_profile(
        self, principal: Principal, target_id: str, pin: str | None, password: str | None
    ) -> tuple[Profile, dict[str, str]]:
        if self.content_policy is None or not self.content_policy.enabled:
            raise Conflict("Content policy is disabled", code="content_policy_disabled")
        current = await self.session_valid(principal.profile_id, principal.token_version, principal.session_id)
        target = await self.get_profile(target_id)
        await self.ensure_pin_ready()
        if target_id != current.id:
            if self.content_policy.is_restricted(current):
                raise Forbidden("Owner PIN is required to leave this profile", code="owner_pin_required")
            if not self.content_policy.is_restricted(target):
                raise Forbidden("Owner PIN is required for this profile", code="owner_pin_required")
            if not self.effective_admin(current):
                raise Forbidden("Owner PIN is required to switch profiles", code="owner_pin_required")
        await self.consume_session(principal.profile_id, principal.token_version, principal.session_id)
        return target, self.issue(
            target,
            owner_pin_version=principal.owner_pin_version if not self.content_policy.is_restricted(target) else 0,
            google_subject=principal.google_subject,
        )

    async def refresh(self, refresh_token: str) -> tuple[Profile, dict[str, str]]:
        p = self._decode(refresh_token, "refresh")
        async with self._db.session() as s:
            prof = await s.get(Profile, str(p["sub"]))
        if prof is None or prof.token_version != int(p.get("ver", -1)):
            raise Unauthorized("refresh token revoked")
        if self.content_policy is not None and self.content_policy.enabled:
            await self.session_valid(prof.id, int(p.get("ver", -1)), str(p.get("sid") or ""))
        await self.check_owner_entry(prof, int(p.get("opv", 0)))
        return prof, self.issue(
            prof, str(p.get("sid") or ""), self.owner_time(p.get("oat")), int(p.get("opv", 0)), str(p.get("gs") or "")
        )

    # ------------------------------------------------------------------ login
    def _throttle(self, profile_id: str) -> None:
        now = time.monotonic()
        recent = [t for t in self._fail.get(profile_id, []) if now - t < 300]
        self._fail[profile_id] = recent
        if len(recent) >= 10:
            raise ServiceError("too many attempts, try again later", code="rate_limited")

    async def login(self, profile_id: str, pin: str | None, password: str | None) -> tuple[Profile, dict[str, str]]:
        if self.content_policy is not None and self.content_policy.enabled:
            raise Forbidden("Use Google sign-in and owner PIN", code="owner_pin_required")
        self._throttle(profile_id)
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
        if prof is None:
            raise Unauthorized("invalid credentials")
        ok = True
        if prof.password_hash:
            # admin: password, or PIN when one is set (TV convenience)
            ok = (password is not None and verify_secret(password, prof.password_hash)) or (
                prof.pin_hash is not None and pin is not None and verify_secret(pin, prof.pin_hash)
            )
        elif prof.pin_hash:
            ok = pin is not None and verify_secret(pin, prof.pin_hash)
        if not ok:
            self._fail.setdefault(profile_id, []).append(time.monotonic())
            raise Unauthorized("invalid credentials")
        self._fail.pop(profile_id, None)
        return prof, self.issue(prof)

    # ------------------------------------------------------------------ device pairing
    async def start_device(self) -> dict[str, Any]:
        device_code = secrets.token_urlsafe(32)
        async with self._db.session() as s:
            await s.execute(delete(DeviceCode).where(DeviceCode.expires_at < utcnow()))
            for _ in range(10):
                user_code = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(6))
                exists = (
                    await s.execute(select(DeviceCode).where(DeviceCode.user_code == user_code))
                ).scalar_one_or_none()
                if exists is None:
                    break
            s.add(
                DeviceCode(
                    device_code=device_code,
                    user_code=user_code,
                    expires_at=utcnow() + timedelta(seconds=DEVICE_CODE_TTL),
                )
            )
            await s.commit()
        return {
            "device_code": device_code,
            "user_code": user_code,
            "expires_in": DEVICE_CODE_TTL,
            "interval": DEVICE_POLL_INTERVAL,
        }

    async def poll_device(self, device_code: str) -> tuple[Profile, dict[str, str]]:
        async with self._db.session() as s:
            row = await s.get(DeviceCode, device_code)
            if row is None or row.consumed:
                raise NotFound("unknown device code")
            if row.expires_at < utcnow():
                raise Gone("device code expired", code="expired_token")
            if not row.approved_profile_id:
                raise PreconditionRequired("waiting for approval")
            prof = await s.get(Profile, row.approved_profile_id)
            if prof is None:
                raise NotFound("profile removed")
            proof = None
            pin_version = 0
            google_subject = ""
            if self.content_policy is not None and self.content_policy.enabled:
                approver = await self.session_valid(
                    row.approved_by_profile_id or "", row.approved_by_version or 0, row.approved_by_session_id or ""
                )
                if approver.id != prof.id and not self.effective_admin(approver):
                    raise Forbidden("Pairing approval is no longer authorized")
                await self.ensure_pin_ready()
                pin_version = row.owner_pin_version or 0
                google_subject = row.approved_google_subject or ""
                await self.check_owner_entry(prof, pin_version)
            guards = [
                DeviceCode.device_code == device_code,
                DeviceCode.consumed.is_(False),
                DeviceCode.approved_profile_id == prof.id,
                select(Profile.id).where(Profile.id == prof.id, Profile.token_version == prof.token_version).exists(),
            ]
            if self.content_policy is not None and self.content_policy.enabled:
                guards.extend(
                    [
                        DeviceCode.approved_by_session_id == row.approved_by_session_id,
                        select(Profile.id)
                        .where(
                            Profile.id == row.approved_by_profile_id, Profile.token_version == row.approved_by_version
                        )
                        .exists(),
                        ~select(RevokedSession.session_id)
                        .where(RevokedSession.session_id == row.approved_by_session_id)
                        .exists(),
                    ]
                )
            result = await s.execute(
                update(DeviceCode).where(*guards).values(consumed=True).returning(DeviceCode.device_code)
            )
            if result.scalar_one_or_none() is None:
                raise NotFound("device code already consumed")
            await s.commit()
        return prof, self.issue(
            prof, owner_auth_time=proof, owner_pin_version=pin_version, google_subject=google_subject
        )

    @staticmethod
    def _require_pairing_proof(proof: float | None, created: float) -> None:
        if proof is None or proof < created - 2 or proof > time.time() + 2:
            raise Forbidden(
                "Fresh owner Google authentication is required for this pairing", code="owner_reauth_required"
            )

    async def approve_device(self, principal: Principal, user_code: str, profile_id: str | None) -> None:
        code = (user_code or "").strip().upper().replace("-", "").replace(" ", "")
        target = profile_id or principal.profile_id
        if target != principal.profile_id and not principal.is_admin:
            raise Forbidden("only an admin can pair a device to another profile")
        async with self._db.session() as s:
            row = (await s.execute(select(DeviceCode).where(DeviceCode.user_code == code))).scalar_one_or_none()
            if row is None or row.consumed:
                raise NotFound("unknown code")
            if row.expires_at < utcnow():
                raise Gone("code expired")
            destination = await s.get(Profile, target)
            if destination is None:
                raise NotFound("profile not found")
            if self.content_policy is not None and self.content_policy.enabled:
                approver = await self.session_valid(principal.profile_id, principal.token_version, principal.session_id)
                if target != approver.id and not self.effective_admin(approver):
                    raise Forbidden("Only an unrestricted owner can approve another profile")
                await self.ensure_pin_ready()
                await self.check_owner_entry(destination, principal.owner_pin_version)
                row.owner_pin_version = principal.owner_pin_version
                row.approved_google_subject = principal.google_subject
                row.owner_auth_time = principal.owner_auth_time
                row.approved_by_profile_id = principal.profile_id
                row.approved_by_version = principal.token_version
                row.approved_by_session_id = principal.session_id
            row.approved_profile_id = target
            await s.commit()

    # ------------------------------------------------------------------ profiles
    async def list_profiles(self) -> list[Profile]:
        async with self._db.session() as s:
            return list((await s.execute(select(Profile).order_by(Profile.created_at))).scalars())

    async def get_profile(self, profile_id: str) -> Profile:
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
        if prof is None:
            raise NotFound("profile not found")
        return prof

    async def create_profile(self, principal: Principal, data: dict[str, Any]) -> Profile:
        if not principal.is_admin:
            raise Forbidden("admin only")
        async with self._db.session() as s:
            if (await s.execute(select(func.count(Profile.id)))).scalar_one() >= MAX_PROFILES:
                raise Conflict("profile limit reached")
            prof = Profile(
                id=secrets.token_hex(8),
                name=str(data.get("name") or "Profile")[:64],
                avatar=data.get("avatar"),
                is_admin=False,  # admin is the bootstrap profile; never escalate via API
                theme_id=str(data.get("theme_id") or "midnight"),
                languages=list(data.get("languages") or []),
                kids=bool(data.get("kids")),
                prefs={},
            )
            pin = data.get("pin")
            if pin:
                prof.pin_hash = hash_secret(validate_pin(str(pin)))
            s.add(prof)
            await s.commit()
        return prof

    async def update_profile(self, principal: Principal, profile_id: str, data: dict[str, Any]) -> Profile:
        if profile_id != principal.profile_id and not principal.is_admin:
            raise Forbidden("cannot edit another profile")
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
            if prof is None:
                raise NotFound("profile not found")
            for key in ("name", "avatar", "theme_id"):
                if key in data and data[key] is not None:
                    setattr(prof, key, data[key])
            if "languages" in data and data["languages"] is not None:
                prof.languages = [str(x) for x in data["languages"]]
            if "kids" in data and data["kids"] is not None and prof.kids != bool(data["kids"]):
                if self.content_policy is not None and self.content_policy.enabled:
                    raise Forbidden(
                        "Use the owner-approved content policy editor to change restrictions",
                        code="owner_policy_required",
                    )
                if not principal.is_admin:
                    raise Forbidden("only admin can change kids flag")
                prof.kids = bool(data["kids"])
            if isinstance(data.get("prefs"), dict):
                prefs = dict(prof.prefs or {})
                prefs.update(data["prefs"])
                prof.prefs = prefs
            if principal.is_admin and data.get("password") and prof.is_admin:
                prof.password_hash = hash_secret(str(data["password"]))
                prof.token_version += 1
            await s.commit()
            await s.refresh(prof)
        return prof

    async def delete_profile(self, principal: Principal, profile_id: str) -> None:
        if not principal.is_admin:
            raise Forbidden("admin only")
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
            if prof is None:
                raise NotFound("profile not found")
            if prof.is_admin:
                raise Conflict("cannot delete the admin profile")
            await s.execute(delete(DisclaimerAcceptance).where(DisclaimerAcceptance.profile_id == profile_id))
            await s.delete(prof)
            await s.commit()

    async def set_pin(self, principal: Principal, profile_id: str, pin: str | None) -> None:
        if self.content_policy is not None and self.content_policy.enabled and not principal.is_admin:
            raise Forbidden("Only the owner can manage profile PINs")
        if profile_id != principal.profile_id and not principal.is_admin:
            raise Forbidden("cannot change another profile's PIN")
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
            if prof is None:
                raise NotFound("profile not found")
            prof.pin_hash = hash_secret(validate_pin(pin)) if pin else None
            prof.token_version += 1
            await s.commit()


def validate_pin(pin: str) -> str:
    if not (len(pin) == 4 and pin.isdigit()):
        raise ServiceError("PIN must be 4 digits", code="invalid_pin")
    return pin
