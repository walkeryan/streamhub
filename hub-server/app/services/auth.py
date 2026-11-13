"""Profiles + authentication (JWT access/refresh, PIN, device-code pairing)."""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

import jwt
from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.sqlite import insert

from app.infra.crypto import Crypto, hash_secret, verify_secret
from app.infra.db import Database
from app.infra.models import AuthSession, DeviceCode, Profile, RevokedSession, Role, User, utcnow
from app.services.errors import (
    Conflict,
    Forbidden,
    Gone,
    NotFound,
    PermissionDenied,
    PreconditionRequired,
    Unauthorized,
)
from app.services.permissions import BUILTIN_ROLES, NO_LIMITS, OWNER_GRANTED, OWNER_ROLE, Limits
from app.services.sessions import SessionMeta, SessionsService, adult_unlocked
from app.services.throttle import LoginThrottle, Ticket
from app.services.users import (
    Identity,
    UsersService,
    access,
    audit,
    ensure_user,
    sign_in_methods,
    standing,
    validate_pin,
)

log = logging.getLogger(__name__)

ALG = "HS256"
USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
DEVICE_CODE_TTL = 600
# How long the refresh token before the current one still works: two tabs, or a retried request,
# refreshing with the same token at the same moment.
REFRESH_GRACE_S = 60
DEVICE_POLL_INTERVAL = 5


@dataclass(slots=True, init=False)
class Principal:
    """Who is asking. Built from the database on every request, never from token claims alone.

    ``permissions`` is the authority. ``is_admin`` survives as a read-only view of it
    for the callers that predate permissions.
    """

    profile_id: str
    kids: bool
    token_version: int
    session_id: str
    owner_auth_time: float | None
    owner_pin_version: int
    google_subject: str
    user_id: str
    permissions: frozenset[str]
    limits: Limits
    user_version: int
    shared_device: bool
    adult_unlocked: bool

    def __init__(
        self,
        profile_id: str,
        is_admin: bool = False,
        kids: bool = False,
        token_version: int = 0,
        session_id: str = "",
        owner_auth_time: float | None = None,
        owner_pin_version: int = 0,
        google_subject: str = "",
        *,
        user_id: str = "",
        permissions: Iterable[str] | None = None,
        limits: Limits = NO_LIMITS,
        user_version: int = 0,
        shared_device: bool = False,
        adult_unlocked: bool = False,
    ) -> None:
        self.profile_id = profile_id
        self.kids = kids
        self.token_version = token_version
        self.session_id = session_id
        self.owner_auth_time = owner_auth_time
        self.owner_pin_version = owner_pin_version
        self.google_subject = google_subject
        self.user_id = user_id
        # ``is_admin`` is a constructor shorthand for code that builds a principal by hand
        # (tests, in-process callers): the Owner set, or what any viewer could always do
        # (watch and request, which is the Kid role's set). Request principals always pass
        # ``permissions`` as loaded from the user's role.
        if permissions is None:
            permissions = BUILTIN_ROLES[OWNER_ROLE if is_admin else "kid"].permissions
        self.permissions = frozenset(permissions)
        self.limits = limits
        self.user_version = user_version
        self.shared_device = shared_device
        self.adult_unlocked = adult_unlocked

    @property
    def is_admin(self) -> bool:
        return "system.manage" in self.permissions

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _can(principal: Any, permission: str) -> bool:
    return permission in getattr(principal, "permissions", ())


@dataclass(slots=True)
class SessionState:
    """One validated session: the identity behind it and its ``auth_session`` row."""

    identity: Identity
    session: AuthSession


class AuthService:
    def __init__(self, db: Database, crypto: Crypto, access_ttl: int = 900, refresh_ttl: int = 30 * 86400) -> None:
        self._db = db
        self._key = crypto.jwt_key
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        # One throttle for every way of proving who one is with a secret: a profile's PIN here,
        # a username's password next door. An address is held to one allowance across both.
        self.throttle = LoginThrottle()
        self.content_policy: Any = None
        self.owner_pin: Any = None
        self.sessions = SessionsService(db, refresh_ttl)
        self.users = UsersService(db, sessions=self.sessions)

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
            profile = Profile(
                id=secrets.token_hex(8),
                name="Admin",
                is_admin=True,
                theme_id="midnight",
                languages=[],
                password_hash=hash_secret(password),
                prefs={},
            )
            s.add(profile)
            await s.flush()
            # The first user is the Owner; Google sign-in with the bootstrap address claims it later.
            await ensure_user(s, profile)
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
        user: User | None = None,
        generation: int = 0,
        refresh_seq: int = 0,
    ) -> str:
        now = int(time.time())
        payload = {
            "sub": profile.id,
            "typ": typ,
            # A hint for older clients only. No server check reads ``adm``.
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
        if user is not None:
            payload["uid"] = user.id
            payload["uv"] = user.version
        if generation:
            # Which hand-over of a shared device this token belongs to; absent means the first.
            payload["sg"] = generation
        if refresh_seq and typ == "refresh":
            # Which rotation of the session's refresh token this is; absent means the first.
            payload["rs"] = refresh_seq
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

    async def issue(
        self,
        profile: Profile,
        session_id: str = "",
        owner_auth_time: float | None = None,
        owner_pin_version: int = 0,
        google_subject: str = "",
        *,
        meta: SessionMeta | None = None,
        handover: bool = False,
    ) -> dict[str, str]:
        """Mint an access/refresh pair and make sure the ``auth_session`` row behind its ``sid`` exists.

        Only an active user gets tokens. A new ``sid`` creates its row here; an existing one
        (refresh) must still be live and belong to this profile. ``handover``: the refresh of a
        shared device whose user is suspended (see ``refresh``); such a pair opens nothing but
        the two hand-over routes, since every other route asks for an active user.
        """
        owner_auth_time = self.owner_time(
            owner_auth_time if owner_auth_time is not None else getattr(profile, "_owner_auth_time", None)
        )
        session_id = session_id or secrets.token_hex(16)
        google_subject = google_subject or getattr(profile, "_google_subject", "")
        async with self._db.session() as s:
            row = await s.get(Profile, profile.id)
            if row is None:
                raise Unauthorized("Session revoked; sign in again", code="session_revoked")
            user = await ensure_user(s, row)
            if not (handover and user.status == "suspended"):
                self._require_active(user)
            attached = await self.sessions.attach(s, session_id, user, row, meta)
            generation, refresh_seq = attached.generation, attached.refresh_seq
            await s.commit()
        return self._pair(
            profile, user, session_id, generation, owner_auth_time, owner_pin_version, google_subject, refresh_seq
        )

    def _pair(
        self,
        profile: Profile,
        user: User,
        session_id: str,
        generation: int,
        owner_auth_time: float | None = None,
        owner_pin_version: int = 0,
        google_subject: str = "",
        refresh_seq: int = 0,
    ) -> dict[str, str]:
        args = (session_id, owner_auth_time, owner_pin_version, google_subject, user, generation, refresh_seq)
        return {
            "access_token": self._encode(profile, "access", self.access_ttl, *args),
            "refresh_token": self._encode(profile, "refresh", self.refresh_ttl, *args),
        }

    @staticmethod
    def _require_active(user: User) -> None:
        if user.status == "pending":
            raise Forbidden("Waiting for the owner's approval", code="approval_pending")
        if user.status != "active":
            raise Unauthorized("This account is suspended", code="user_suspended")

    def _claims(self, token: str) -> dict[str, Any]:
        try:
            payload: dict[str, Any] = jwt.decode(token, self._key, algorithms=[ALG])
        except jwt.ExpiredSignatureError as exc:
            raise Unauthorized("token expired", code="token_expired") from exc
        except jwt.PyJWTError as exc:
            raise Unauthorized("invalid token") from exc
        return payload

    def _decode(self, token: str, typ: str) -> dict[str, Any]:
        payload = self._claims(token)
        if payload.get("typ") != typ:
            raise Unauthorized("wrong token type")
        return payload

    def principal_from_access(self, token: str) -> Principal:
        """The identity claims of an access token, unvalidated and carrying no permissions.

        For flows that validate the session themselves; anything that authorizes a
        request goes through ``resolve_principal``.
        """
        p = self._decode(token, "access")
        return Principal(
            profile_id=str(p["sub"]),
            kids=bool(p.get("kid")),
            token_version=int(p.get("ver", -1)),
            session_id=str(p.get("sid") or ""),
            owner_auth_time=self.owner_time(p.get("oat")),
            owner_pin_version=int(p.get("opv", 0)),
            google_subject=str(p.get("gs") or ""),
            user_id=str(p.get("uid") or ""),
            permissions=(),
            user_version=int(p.get("uv", -1)),
        )

    async def session_valid(self, profile_id: str, version: int, session_id: str, *, handover: bool = False) -> Profile:
        """The chokepoint for anything that carries (profile, version, sid) but no user version:
        stream and image grants, owner-PIN challenges. Refuses a revoked session and a user
        who is no longer active. ``handover`` (refresh only) lets a suspended user's shared
        device through while its row stands."""
        async with self._db.session() as session:
            profile = await session.get(Profile, profile_id)
            revoked = await session.get(RevokedSession, session_id) if session_id else None
            row = await session.get(AuthSession, session_id) if session_id else None
            user = await session.get(User, profile.user_id) if profile is not None and profile.user_id else None
        if (
            profile is None
            or profile.token_version != version
            or not session_id
            or revoked is not None
            or (row is not None and (row.revoked_at is not None or row.profile_id != profile_id))
        ):
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        if user is not None and not (handover and user.status == "suspended" and row is not None and row.shared_device):
            self._require_active(user)
        return profile

    def _access(
        self, profile: Profile, user: User, role: Role | None, *, handing_over: bool = False
    ) -> tuple[frozenset[str], Limits]:
        # A suspended user holds nothing. Their shared device is handed over by the rules
        # that applied to them before, so a wider target still needs its PIN.
        permissions, limits = standing(user, role) if handing_over and role is not None else access(user, role)
        # Legacy regime: a restricted profile never holds more than the Kid role does.
        if self.content_policy is not None and self.content_policy.is_restricted(profile):
            permissions &= BUILTIN_ROLES["kid"].permissions
        return permissions, limits

    async def validate(
        self,
        profile_id: str,
        version: int,
        session_id: str,
        user_version: int | None,
        generation: int | None = None,
        *,
        handover: bool = False,
    ) -> SessionState:
        """Unconditional session check: profile, user, role and session row, on every request.

        One short transaction of primary-key reads, so a role change, a suspension or a
        revocation applies to the very next request rather than when a cache expires.

        ``handover`` is for the two routes that hand a shared device to someone else. There a
        suspended user's session passes while its row stands, or the device would be stuck
        on a user nobody can use and could only be paired again.
        """
        async with self._db.session() as s:
            profile = await s.get(Profile, profile_id)
            revoked = await s.get(RevokedSession, session_id) if session_id else None
            row = await s.get(AuthSession, session_id) if session_id else None
            user = await s.get(User, profile.user_id) if profile is not None and profile.user_id else None
            role = await s.get(Role, user.role_id) if user is not None and user.role_id else None
        if profile is None or profile.token_version != version or not session_id or revoked is not None:
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        if user is None:
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        handing_over = handover and user.status == "suspended" and row is not None and row.shared_device
        if not handing_over:
            self._require_active(user)
        if row is None or row.revoked_at is not None or row.profile_id != profile.id or row.user_id != user.id:
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        if generation is not None and generation != row.generation:
            # A shared device changed hands since this token was minted.
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        # The suspension itself made the token stale, and a suspended user cannot refresh it.
        if not handing_over and (user_version is None or user_version != user.version):
            # Role, overrides or limits changed: the client refreshes and carries on.
            raise Unauthorized("Session is out of date; refresh it", code="session_stale")
        held = self._access(profile, user, role, handing_over=handing_over)
        return SessionState(Identity(profile, user, role, *held), row)

    def _principal(self, state: SessionState, claims: dict[str, Any]) -> Principal:
        identity, row = state.identity, state.session
        return Principal(
            profile_id=identity.profile.id,
            kids=identity.profile.kids,
            token_version=identity.profile.token_version,
            session_id=row.sid,
            owner_auth_time=self.owner_time(claims.get("oat")),
            owner_pin_version=int(claims.get("opv", 0)),
            google_subject=str(claims.get("gs") or ""),
            user_id=identity.user.id,
            permissions=identity.permissions,
            limits=identity.limits,
            user_version=identity.user.version,
            shared_device=row.shared_device,
            adult_unlocked=adult_unlocked(row),
        )

    @staticmethod
    def _user_version(claims: dict[str, Any]) -> int | None:
        value = claims.get("uv")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _generation(claims: dict[str, Any]) -> int:
        """The ``sg`` claim. Absent (every token of a session that never changed hands) is 0; junk matches nothing."""
        value = claims.get("sg", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else -1

    async def resolve_principal(self, token: str, *, owner_entry: bool = True, handover: bool = False) -> Principal:
        """The caller behind an access token. ``handover``: see ``validate``."""
        claims = self._claims(token)
        if claims.get("typ") == "pending":
            raise Forbidden("Waiting for the owner's approval", code="approval_pending")
        if claims.get("typ") != "access":
            raise Unauthorized("wrong token type")
        state = await self.validate(
            str(claims.get("sub") or ""),
            int(claims.get("ver", -1)),
            str(claims.get("sid") or ""),
            self._user_version(claims),
            self._generation(claims),
            handover=handover,
        )
        if owner_entry:
            await self.check_owner_entry(state.identity.profile, int(claims.get("opv", 0)))
        if state.identity.user.status == "active":
            await self.sessions.touch(state.session.sid, state.identity.user.id)
        return self._principal(state, claims)

    async def session_principal(self, profile_id: str, version: int, session_id: str) -> Principal:
        """The principal behind a session that is named without its access token (a room member
        token): the session validated as for any request, with the user's access as it is now."""
        identity = await self.users.identity(profile_id)
        if identity is None:
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        state = await self.validate(profile_id, version, session_id, identity.user.version)
        return self._principal(state, {})

    # ------------------------------------------------------------------ pending sign-ins
    async def issue_pending(self, profile: Profile, meta: SessionMeta | None = None) -> str:
        """A token for a user still waiting for approval. It opens ``GET /auth/me`` and nothing else."""
        sid = secrets.token_hex(16)
        async with self._db.session() as s:
            row = await s.get(Profile, profile.id)
            user = await s.get(User, row.user_id) if row is not None and row.user_id else None
            if row is None or user is None or user.status != "pending":
                raise Unauthorized("Sign-in expired or invalid; please start again")
            await self.sessions.attach(s, sid, user, row, meta)
            await s.commit()
        # Approval can take days; the waiting browser keeps polling with this one token.
        return self._encode(profile, "pending", self.refresh_ttl, sid, user=user)

    async def me(
        self, token: str, meta: SessionMeta | None = None
    ) -> tuple[Principal | None, Identity, dict[str, str] | None]:
        """Who the bearer is, for ``GET /auth/me``: the one call a pending token may make.

        Once the user has been approved, the pending session is consumed and full tokens
        come back in its place, so the waiting browser becomes signed in without a second
        trip through Google.
        """
        claims = self._claims(token)
        if claims.get("typ") != "pending":
            principal = await self.resolve_principal(token)
            identity = await self.users.identity(principal.profile_id)
            if identity is None:
                raise Unauthorized("Session revoked; sign in again", code="session_revoked")
            identity.permissions, identity.limits = principal.permissions, principal.limits
            return principal, identity, None
        sid = str(claims.get("sid") or "")
        identity = await self.users.identity(str(claims.get("sub") or ""))
        async with self._db.session() as s:
            row = await s.get(AuthSession, sid) if sid else None
            if (
                identity is None
                or row is None
                or row.revoked_at is not None
                or row.user_id != identity.user.id
                or identity.user.id != claims.get("uid")
            ):
                raise Unauthorized("Session revoked; sign in again", code="session_revoked")
            if identity.user.status == "pending":
                return None, identity, None
            self._require_active(identity.user)
            # One winner: a replayed pending token finds its session already consumed.
            if not await self.sessions.revoke_in(s, sid, self.refresh_ttl):
                raise Unauthorized("Session revoked; sign in again", code="session_revoked")
            await s.commit()
            carried = SessionMeta(kind=row.kind, label=row.label, user_agent=row.user_agent, ip=row.ip)
        tokens = await self.issue(identity.profile, meta=meta or carried)
        principal = await self.resolve_principal(tokens["access_token"], owner_entry=False)
        return principal, identity, tokens

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
        identity = await self.users.identity(profile.id)
        if identity is None or "system.manage" not in identity.permissions or not subject:
            raise Unauthorized("Verified Google owner identity required")
        return Principal(
            profile.id,
            kids=profile.kids,
            token_version=profile.token_version,
            session_id=str(value["sid"]),
            google_subject=subject,
            user_id=identity.user.id,
            permissions=identity.permissions,
            limits=identity.limits,
            user_version=identity.user.version,
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
            await self.sessions.revoke_in(session, session_id, self.refresh_ttl)
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
            await session.execute(
                update(AuthSession)
                .where(AuthSession.sid == session_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=utcnow(), adult_unlocked_until=None)
            )
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
        return target, await self.issue(
            target,
            owner_pin_version=principal.owner_pin_version if not self.content_policy.is_restricted(target) else 0,
            google_subject=principal.google_subject,
        )

    async def switch_user(
        self, principal: Principal, user_id: str, pin: str | None
    ) -> tuple[Profile, dict[str, str], dict[str, Any]]:
        """ "Who's watching?": hand a shared device to another user and mint their tokens for it.

        Returns the target's profile, the new token pair and the user as ``/auth/me`` shows them.
        Switching to oneself changes nothing and hands back a fresh pair for the same session.
        """
        if self.content_policy is not None and self.content_policy.enabled:
            # The owner-PIN regime has its own, stricter way between profiles.
            raise Conflict("Use the owner PIN to switch profiles", code="owner_pin_required")
        user, profile, role, row = await self.sessions.switch(principal, user_id, pin)
        if user.id == principal.user_id:
            tokens = await self.issue(
                profile,
                principal.session_id,
                principal.owner_auth_time,
                principal.owner_pin_version,
                principal.google_subject,
            )
        else:
            # Nothing of the previous user's sign-in (their Google subject, their proofs) carries over.
            tokens = self._pair(profile, user, row.sid, row.generation, refresh_seq=row.refresh_seq)
        return profile, tokens, {**self.users.user_out(user, profile, role), "current": True}

    async def refresh(self, refresh_token: str, meta: SessionMeta | None = None) -> tuple[Profile, dict[str, str]]:
        p = self._decode(refresh_token, "refresh")
        sid = str(p.get("sid") or "")
        async with self._db.session() as s:
            prof = await s.get(Profile, str(p["sub"]))
            row = await s.get(AuthSession, sid) if sid else None
            user = await s.get(User, prof.user_id) if prof is not None and prof.user_id else None
        if prof is None or prof.token_version != int(p.get("ver", -1)):
            raise Unauthorized("refresh token revoked")
        # A shared device whose user has been suspended keeps refreshing while its row stands:
        # access tokens are short-lived, and without a fresh one the device could not reach the
        # hand-over routes and would have to be paired again. The pair it gets opens only those.
        handover = (
            user is not None
            and user.status == "suspended"
            and row is not None
            and row.shared_device
            and row.revoked_at is None
        )
        # A token minted before sessions had rows carries no ``uid``; its first refresh adopts
        # the ``sid`` (issue() creates the row) so an upgrade signs nobody out. Every later
        # token has a row, and a missing one means the session is gone.
        if (row is None and "uid" in p) or (row is not None and row.generation != self._generation(p)):
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        # Checks the profile version, the deny-list, the row when present, and the user's status.
        await self.session_valid(prof.id, int(p.get("ver", -1)), sid, handover=handover)
        await self.check_owner_entry(prof, int(p.get("opv", 0)))
        if row is not None:
            await self._rotate(sid, self._refresh_seq(p))
        return prof, await self.issue(
            prof,
            sid,
            self.owner_time(p.get("oat")),
            int(p.get("opv", 0)),
            str(p.get("gs") or ""),
            meta=meta,
            handover=handover,
        )

    @staticmethod
    def _refresh_seq(claims: dict[str, Any]) -> int:
        """The ``rs`` claim. Absent (a token from before rotation, or a session's first) is 0; junk matches nothing."""
        value = claims.get("rs", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else -1

    async def _rotate(self, sid: str, presented: int) -> None:
        """Make the presented refresh token the previous one, or refuse it.

        One guarded UPDATE moves the counter, so of several refreshes arriving together exactly
        one rotates. The others, and a second tab a moment later, hold the token just before
        the current one and are let through without rotating again. Anything older has been
        superseded for longer than any honest client waits: a copy is being replayed, the
        session ends for whoever holds it, and the owner of the device signs in again.
        """
        now = utcnow()
        async with self._db.session() as s:
            moved = (
                await s.execute(
                    update(AuthSession)
                    .where(
                        AuthSession.sid == sid, AuthSession.refresh_seq == presented, AuthSession.revoked_at.is_(None)
                    )
                    .values(refresh_seq=AuthSession.refresh_seq + 1, refresh_rotated_at=now)
                    .returning(AuthSession.sid)
                )
            ).scalar_one_or_none()
            if moved is not None:
                await s.commit()
                return
            row = await s.get(AuthSession, sid)
            rotated = row.refresh_rotated_at if row is not None else None
            recent = rotated is not None and now - rotated <= timedelta(seconds=REFRESH_GRACE_S)
            if row is not None and row.revoked_at is None and presented == row.refresh_seq - 1 and recent:
                return
            if row is not None and row.revoked_at is None:
                await self.sessions.revoke_in(s, sid, self.refresh_ttl)
                audit(s, None, "session.refresh_reuse", row.user_id, kind=row.kind, label=row.label)
                await s.commit()
        raise Unauthorized("Session revoked; sign in again", code="session_revoked")

    # ------------------------------------------------------------------ login
    @staticmethod
    def _entered_by_a_tap(user: User | None, role: Role | None) -> bool:
        """May a profile that has no PIN and no password be entered by choosing it?

        The household convenience, for a child's profile on the home network, and for nobody
        with more to lose: not someone who signs in with Google, Plex or a password of their
        own (the tap would walk around it), and not someone who manages users, sources or the
        server. They set a PIN, or sign in their own way.
        """
        if user is None or role is None or sign_in_methods(user):
            return False
        return not standing(user, role)[0] & OWNER_GRANTED

    async def _settle(self, ok: bool, ticket: Ticket, user_id: str | None) -> None:
        """Close an attempt the throttle let through; a lock it caused goes in the audit log."""
        if ok:
            self.throttle.succeed(ticket)
            return
        locked = self.throttle.fail(ticket)
        if locked:
            async with self._db.session() as s:
                for key, seconds in locked.items():
                    scope = "address" if key.startswith("a:") else "profile"
                    audit(
                        s, None, "auth.lockout", user_id if scope == "profile" else None, scope=scope, seconds=seconds
                    )
                await s.commit()

    async def login(
        self,
        profile_id: str,
        pin: str | None,
        password: str | None,
        meta: SessionMeta | None = None,
        *,
        address: str | None = None,
    ) -> tuple[Profile, dict[str, str]]:
        """Sign a profile in with its PIN or password. The route in front of this answers on the
        home network only; this is the household's convenience and never a way in from outside."""
        if self.content_policy is not None and self.content_policy.enabled:
            raise Forbidden("Use Google sign-in and owner PIN", code="owner_pin_required")
        # Counted before anything is verified, by profile and by client address, so a burst sent
        # at once is held to the same number as one sent slowly. No await before this line.
        ticket = self.throttle.begin(f"p:{profile_id[:64]}", f"a:{address or 'unknown'}")
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
            user = await s.get(User, prof.user_id) if prof is not None and prof.user_id else None
            role = await s.get(Role, user.role_id) if user is not None and user.role_id else None
        ok = False
        if prof is None:
            pass
        elif prof.password_hash:
            # admin: password, or PIN when one is set (TV convenience)
            ok = (password is not None and await asyncio.to_thread(verify_secret, password, prof.password_hash)) or (
                prof.pin_hash is not None
                and pin is not None
                and await asyncio.to_thread(verify_secret, pin, prof.pin_hash)
            )
        elif prof.pin_hash:
            ok = pin is not None and await asyncio.to_thread(verify_secret, pin, prof.pin_hash)
        else:
            ok = self._entered_by_a_tap(user, role)
        await self._settle(ok, ticket, user.id if user is not None else None)
        if not ok or prof is None:
            raise Unauthorized("invalid credentials")
        return prof, await self.issue(prof, meta=meta)

    # ------------------------------------------------------------------ device pairing
    async def start_device(self, meta: SessionMeta | None = None) -> dict[str, Any]:
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
                    requested_ip=(meta.ip or "")[:64] or None if meta else None,
                    requested_agent=(meta.user_agent or "")[:512] or None if meta else None,
                )
            )
            await s.commit()
        return {
            "device_code": device_code,
            "user_code": user_code,
            "expires_in": DEVICE_CODE_TTL,
            "interval": DEVICE_POLL_INTERVAL,
        }

    async def poll_device(self, device_code: str, meta: SessionMeta | None = None) -> tuple[Profile, dict[str, str]]:
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
            # Refuse before the code is consumed: a suspended user's pairing stays unclaimed.
            self._require_active(await ensure_user(s, prof))
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
            if row.shared_device:
                # Shared only if whoever ticked the box may still decide that as the TV claims the code.
                approver_user = await s.get(User, row.approved_by_user_id) if row.approved_by_user_id else None
                approver_role = (
                    await s.get(Role, approver_user.role_id) if approver_user and approver_user.role_id else None
                )
                if "users.manage" in access(approver_user, approver_role)[0]:
                    meta = replace(
                        meta or SessionMeta(kind="tv"), shared_device=True, device_owner_user_id=row.approved_by_user_id
                    )
            await s.commit()
        return prof, await self.issue(
            prof, owner_auth_time=proof, owner_pin_version=pin_version, google_subject=google_subject, meta=meta
        )

    @staticmethod
    def _user_code(value: str) -> str:
        return (value or "").strip().upper().replace("-", "").replace(" ", "")

    async def describe_device(self, user_code: str) -> dict[str, Any]:
        """What is asking to be paired under this code, for the person about to approve it.

        A code in a link is the way an attacker's device gets signed in as someone else, so the
        approval screen says where the request came from. Nothing here signs anything in.
        """
        code = self._user_code(user_code)
        async with self._db.session() as s:
            row = (
                (await s.execute(select(DeviceCode).where(DeviceCode.user_code == code))).scalar_one_or_none()
                if code
                else None
            )
        now = utcnow()
        if row is None or row.consumed or row.approved_profile_id or row.expires_at <= now:
            raise NotFound("unknown code")
        return {
            "user_code": row.user_code,
            "ip": row.requested_ip,
            "user_agent": row.requested_agent,
            "expires_in": max(1, int((row.expires_at - now).total_seconds())),
        }

    @staticmethod
    def _require_pairing_proof(proof: float | None, created: float) -> None:
        if proof is None or proof < created - 2 or proof > time.time() + 2:
            raise Forbidden(
                "Fresh owner Google authentication is required for this pairing", code="owner_reauth_required"
            )

    async def approve_device(
        self, principal: Principal, user_code: str, profile_id: str | None, shared_device: bool = False
    ) -> None:
        code = self._user_code(user_code)
        target = profile_id or principal.profile_id
        if shared_device and not _can(principal, "users.manage"):
            # A shared device offers every household user at its picker: a manager's decision.
            raise PermissionDenied(["users.manage"])
        if target != principal.profile_id:
            # The paired device will act as that user: users.manage, and never upward.
            await self.users.guard_profile_identity(principal, target)
        async with self._db.session() as s:
            row = (await s.execute(select(DeviceCode).where(DeviceCode.user_code == code))).scalar_one_or_none()
            if row is None or row.consumed:
                raise NotFound("unknown code")
            if row.expires_at < utcnow():
                raise Gone("code expired")
            destination = await s.get(Profile, target)
            if destination is None:
                raise NotFound("profile not found")
            if (await ensure_user(s, destination)).status != "active":
                raise Conflict("That user is not active", code="not_active")
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
            row.shared_device = bool(shared_device)
            row.approved_by_user_id = principal.user_id or None
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
        """Legacy route to a managed user: a kids profile gets the Kid role, any other Family."""
        if not _can(principal, "users.manage"):
            raise PermissionDenied(["users.manage"])
        pin = data.get("pin")
        created = await self.users.create_managed(
            principal,
            {
                "name": str(data.get("name") or "Profile")[:64],
                "avatar": data.get("avatar"),
                "theme_id": data.get("theme_id"),
                "languages": data.get("languages"),
                "kids": bool(data.get("kids")),
                "pin": str(pin) if pin else None,
            },
        )
        return await self.get_profile(created["profile_id"])

    async def update_profile(self, principal: Principal, profile_id: str, data: dict[str, Any]) -> Profile:
        manager = _can(principal, "users.manage")
        if profile_id != principal.profile_id and not manager:
            raise PermissionDenied(["users.manage"])
        if data.get("password") and profile_id != principal.profile_id:
            # A password is a way in as that profile's user.
            await self.users.guard_profile_identity(principal, profile_id)
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
                if not manager:
                    raise PermissionDenied(["users.manage"])
                prof.kids = bool(data["kids"])
            if isinstance(data.get("prefs"), dict):
                prefs = dict(prof.prefs or {})
                prefs.update(data["prefs"])
                prof.prefs = prefs
            # The local password belongs to an owner's profile: their own to change, or a manager's.
            if data.get("password") and prof.is_admin and (manager or profile_id == principal.profile_id):
                prof.password_hash = hash_secret(str(data["password"]))
                prof.token_version += 1
                audit(s, getattr(principal, "user_id", None), "user.password_change", prof.user_id)
            await s.commit()
            await s.refresh(prof)
        return prof

    async def delete_profile(self, principal: Principal, profile_id: str) -> None:
        """Legacy route to removing a user; the same guards apply (the last owner stays)."""
        if not _can(principal, "users.manage"):
            raise PermissionDenied(["users.manage"])
        identity = await self.users.identity(profile_id)
        if identity is None:
            raise NotFound("profile not found")
        await self.users.delete(principal, identity.user.id)

    async def set_pin(
        self,
        principal: Principal,
        profile_id: str,
        pin: str | None,
        current_pin: str | None = None,
        current_password: str | None = None,
    ) -> None:
        manager = _can(principal, "users.manage")
        if self.content_policy is not None and self.content_policy.enabled and not manager:
            raise Forbidden("Only the owner can manage profile PINs")
        if profile_id != principal.profile_id:
            await self.users.guard_profile_identity(principal, profile_id)
        async with self._db.session() as s:
            prof = await s.get(Profile, profile_id)
            if prof is None:
                raise NotFound("profile not found")
            await self.users.prove_pin_holder(
                s, principal, await ensure_user(s, prof), prof, current_pin, current_password
            )
            prof.pin_hash = hash_secret(validate_pin(pin)) if pin else None
            prof.token_version += 1
            audit(s, getattr(principal, "user_id", None), "user.pin_set" if pin else "user.pin_clear", prof.user_id)
            await s.commit()
