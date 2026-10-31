"""Profiles + authentication (JWT access/refresh, PIN, device-code pairing)."""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from sqlalchemy import delete, func, select

from app.infra.crypto import Crypto, hash_secret, verify_secret
from app.infra.db import Database
from app.infra.models import DeviceCode, DisclaimerAcceptance, Profile, utcnow
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


class AuthService:
    def __init__(self, db: Database, crypto: Crypto, access_ttl: int = 900, refresh_ttl: int = 30 * 86400) -> None:
        self._db = db
        self._key = crypto.jwt_key
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        self._fail: dict[str, list[float]] = {}

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
    def _encode(self, profile: Profile, typ: str, ttl: int) -> str:
        now = int(time.time())
        payload = {
            "sub": profile.id,
            "typ": typ,
            "adm": profile.is_admin,
            "kid": profile.kids,
            "ver": profile.token_version,
            "iat": now,
            "exp": now + ttl,
            "jti": secrets.token_hex(8),
        }
        return jwt.encode(payload, self._key, algorithm=ALG)

    def issue(self, profile: Profile) -> dict[str, str]:
        return {
            "access_token": self._encode(profile, "access", self.access_ttl),
            "refresh_token": self._encode(profile, "refresh", self.refresh_ttl),
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
        return Principal(profile_id=str(p["sub"]), is_admin=bool(p.get("adm")), kids=bool(p.get("kid")))

    async def refresh(self, refresh_token: str) -> tuple[Profile, dict[str, str]]:
        p = self._decode(refresh_token, "refresh")
        async with self._db.session() as s:
            prof = await s.get(Profile, str(p["sub"]))
        if prof is None or prof.token_version != int(p.get("ver", -1)):
            raise Unauthorized("refresh token revoked")
        return prof, self.issue(prof)

    # ------------------------------------------------------------------ login
    def _throttle(self, profile_id: str) -> None:
        now = time.monotonic()
        recent = [t for t in self._fail.get(profile_id, []) if now - t < 300]
        self._fail[profile_id] = recent
        if len(recent) >= 10:
            raise ServiceError("too many attempts, try again later", code="rate_limited")

    async def login(self, profile_id: str, pin: str | None, password: str | None) -> tuple[Profile, dict[str, str]]:
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
            row.consumed = True
            await s.commit()
        return prof, self.issue(prof)

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
            if await s.get(Profile, target) is None:
                raise NotFound("profile not found")
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
            if "kids" in data and data["kids"] is not None:
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
