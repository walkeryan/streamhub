"""Signed-in devices: the ``auth_session`` rows behind every token's ``sid``.

A session row is created when tokens are first issued and is what makes a device
listable and revocable. Revoking writes the legacy ``revoked_session`` deny-list in
the same transaction, so the stream and image chokepoints that only know that list
stop honouring the session too.
"""

from __future__ import annotations

import asyncio
import builtins
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.crypto import verify_secret
from app.infra.db import Database
from app.infra.models import AuthSession, Profile, RevokedSession, Role, User, utcnow
from app.services.errors import Conflict, Forbidden, NotFound, PermissionDenied, ServiceError, Unauthorized
from app.services.permissions import OWNER_ROLE, is_wider
from app.services.users import access, audit

SESSION_KINDS = ("browser", "tv", "api")
TOUCH_INTERVAL_S = 60
ADULT_UNLOCK_S = 4 * 3600
PIN_FAILURES = 5
PIN_WINDOW_S = 15 * 60


class RateLimited(ServiceError):
    status = 429
    code = "rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many attempts; try again later")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class SessionMeta:
    """What the transport knows about the device asking for tokens."""

    kind: str = "api"
    label: str | None = None
    user_agent: str | None = None
    ip: str | None = None
    shared_device: bool = False
    device_owner_user_id: str | None = None


def _clip(value: str | None, size: int) -> str | None:
    return str(value)[:size] if value else None


def adult_unlocked(row: AuthSession | None, now: datetime | None = None) -> bool:
    return bool(row is not None and row.adult_unlocked_until and row.adult_unlocked_until > (now or utcnow()))


class SessionsService:
    def __init__(self, db: Database, ttl_s: int) -> None:
        self._db = db
        self._ttl_s = ttl_s
        self._touched: dict[str, float] = {}
        self._pin_failures: dict[str, list[float]] = {}

    # ------------------------------------------------------------------ lifecycle
    async def attach(
        self, s: AsyncSession, sid: str, user: User, profile: Profile, meta: SessionMeta | None
    ) -> AuthSession:
        """Create the row for a new ``sid``, or confirm an existing one still belongs to this profile.

        Runs inside the caller's transaction: tokens are only returned once the row is committed.
        """
        now = utcnow()
        row = await s.get(AuthSession, sid)
        if row is None:
            if await s.get(RevokedSession, sid) is not None:
                raise Unauthorized("Session revoked; sign in again", code="session_revoked")
            meta = meta or SessionMeta()
            row = AuthSession(
                sid=sid,
                user_id=user.id,
                profile_id=profile.id,
                kind=meta.kind if meta.kind in SESSION_KINDS else "api",
                label=_clip(meta.label, 128),
                user_agent=_clip(meta.user_agent, 512),
                ip=_clip(meta.ip, 64),
                shared_device=meta.shared_device,
                device_owner_user_id=meta.device_owner_user_id,
                created_at=now,
                last_seen_at=now,
            )
            s.add(row)
            # Rows outlive their tokens only until the longest-lived token would have expired.
            await s.execute(delete(AuthSession).where(AuthSession.last_seen_at < now - timedelta(seconds=self._ttl_s)))
        elif row.revoked_at is not None or row.profile_id != profile.id or row.user_id != user.id:
            raise Unauthorized("Session revoked; sign in again", code="session_revoked")
        else:
            row.last_seen_at = now
            if meta is not None:
                row.user_agent = _clip(meta.user_agent, 512) or row.user_agent
                row.ip = _clip(meta.ip, 64) or row.ip
        user.last_seen_at = now
        return row

    async def touch(self, sid: str, user_id: str) -> None:
        """Record activity, at most once a minute per session so reads stay reads."""
        now = time.monotonic()
        if now - self._touched.get(sid, -TOUCH_INTERVAL_S) < TOUCH_INTERVAL_S:
            return
        if len(self._touched) > 10_000:
            self._touched = {k: v for k, v in self._touched.items() if now - v < TOUCH_INTERVAL_S}
        self._touched[sid] = now
        seen = utcnow()
        async with self._db.session() as s:
            await s.execute(update(AuthSession).where(AuthSession.sid == sid).values(last_seen_at=seen))
            await s.execute(update(User).where(User.id == user_id).values(last_seen_at=seen))
            await s.commit()

    @staticmethod
    async def revoke_in(s: AsyncSession, sid: str, ttl_s: int) -> bool:
        """Mark one session revoked in both stores. True when this call is what revoked it."""
        now = utcnow()
        result = await s.execute(
            update(AuthSession)
            .where(AuthSession.sid == sid, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now, adult_unlocked_until=None)
            .returning(AuthSession.sid)
        )
        claimed = result.scalar_one_or_none() is not None
        await s.execute(
            insert(RevokedSession)
            .values(session_id=sid, expires_at=now + timedelta(seconds=ttl_s))
            .on_conflict_do_nothing()
        )
        return claimed

    # ------------------------------------------------------------------ listing / revoking
    @staticmethod
    def to_api(row: AuthSession, current_sid: str = "") -> dict[str, Any]:
        return {
            "sid": row.sid,
            "user_id": row.user_id,
            "kind": row.kind,
            "label": row.label,
            "user_agent": row.user_agent,
            "ip": row.ip,
            "shared_device": row.shared_device,
            "created_at": row.created_at,
            "last_seen_at": row.last_seen_at,
            "current": row.sid == current_sid,
        }

    async def list(self, user_id: str, current_sid: str = "") -> builtins.list[dict[str, Any]]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(AuthSession)
                    .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                    .order_by(AuthSession.last_seen_at.desc(), AuthSession.sid)
                )
            ).scalars()
            return [self.to_api(r, current_sid) for r in rows]

    async def revoke(self, actor: Any, sid: str) -> None:
        """Sign a device out. One's own, or anyone's with ``users.manage``."""
        async with self._db.session() as s:
            row = await s.get(AuthSession, sid)
            manager = "users.manage" in actor.permissions
            # Someone else's session is indistinguishable from a missing one without users.manage.
            if row is None or row.revoked_at is not None or (row.user_id != actor.user_id and not manager):
                raise NotFound("session not found")
            if row.user_id != actor.user_id:
                # An owner's devices are only ever signed out by an owner.
                owner, me = await s.get(User, row.user_id), await s.get(User, actor.user_id)
                if owner is not None and owner.role_id == OWNER_ROLE and (me is None or me.role_id != OWNER_ROLE):
                    raise Forbidden("Only an owner can change an owner", code="owner_required")
            await self.revoke_in(s, sid, self._ttl_s)
            audit(s, actor.user_id, "session.revoke", row.user_id, kind=row.kind, label=row.label)
            await s.commit()
        self._touched.pop(sid, None)

    async def revoke_all(self, s: AsyncSession, user_id: str) -> int:
        """Revoke every live session of one user, inside the caller's transaction."""
        sids = list(
            (
                await s.execute(
                    select(AuthSession.sid).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                )
            ).scalars()
        )
        for sid in sids:
            await self.revoke_in(s, sid, self._ttl_s)
        return len(sids)

    # ------------------------------------------------------------------ shared devices
    async def update(self, actor: Any, sid: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Relabel a device, or mark it shared (or personal again).

        A label is its user's to change, or a manager's. What kind of device it is, shared
        or personal, is only ever a ``users.manage`` holder's call, their own device included.
        """
        manager = "users.manage" in actor.permissions
        async with self._db.session() as s:
            row = await s.get(AuthSession, sid)
            # Someone else's session is indistinguishable from a missing one without users.manage.
            if row is None or row.revoked_at is not None or (row.user_id != actor.user_id and not manager):
                raise NotFound("session not found")
            if "shared_device" in patch and not manager:
                raise PermissionDenied(["users.manage"])
            if row.user_id != actor.user_id:
                # An owner's devices are only ever changed by an owner.
                owner, me = await s.get(User, row.user_id), await s.get(User, actor.user_id)
                if owner is not None and owner.role_id == OWNER_ROLE and (me is None or me.role_id != OWNER_ROLE):
                    raise Forbidden("Only an owner can change an owner", code="owner_required")
            changed: dict[str, Any] = {}
            if "shared_device" in patch and bool(patch["shared_device"]) != row.shared_device:
                row.shared_device = changed["shared_device"] = bool(patch["shared_device"])
                # Whoever marked it answers for it; a personal device has no such person.
                row.device_owner_user_id = actor.user_id if row.shared_device else None
            if "label" in patch:
                label = _clip(str(patch["label"] or "").strip(), 128)
                if label != row.label:
                    row.label = changed["label"] = label
            if changed:
                audit(s, actor.user_id, "session.update", row.user_id, kind=row.kind, **changed)
                await s.commit()
            return self.to_api(row, actor.session_id)

    @staticmethod
    async def _switchable(s: AsyncSession, *where: Any) -> builtins.list[tuple[User, Profile, Role]]:
        """Active users who appear on shared devices: by their role, unless their own override says otherwise."""
        rows = await s.execute(
            select(User, Profile, Role)
            .join(Profile, Profile.user_id == User.id)
            .join(Role, Role.id == User.role_id)
            .where(User.status == "active", func.coalesce(User.on_shared_devices, Role.on_shared_devices), *where)
        )
        return [(u, p, r) for u, p, r in rows.all()]

    @staticmethod
    def picker_out(user: User, profile: Profile, role: Role, current_user_id: str) -> dict[str, Any]:
        """One face on the "Who's watching?" screen. Nothing here is worth hiding from the household."""
        return {
            "id": user.id,
            "display_name": user.display_name,
            "avatar": profile.avatar,
            "picture": user.picture,
            "role_name": role.name,
            "has_pin": bool(profile.pin_hash),
            "current": user.id == current_user_id,
        }

    async def switchable(self, actor: Any) -> builtins.list[dict[str, Any]]:
        """Who this shared device can be handed to: owners and family first, then by name."""
        if not actor.shared_device:
            raise Forbidden("This is not a shared device", code="not_shared_device")
        async with self._db.session() as s:
            rows = await self._switchable(s)
        first = {OWNER_ROLE: 0, "family": 1}
        rows.sort(key=lambda r: (first.get(r[2].id, 2), r[0].display_name.casefold(), r[0].id))
        return [self.picker_out(user, profile, role, actor.user_id) for user, profile, role in rows]

    def _switch_budget(self, target_user_id: str) -> str:
        """Five wrong PINs in fifteen minutes close a target to every device, right PIN or not."""
        key = f"switch:{target_user_id}"
        now = time.monotonic()
        recent = [t for t in self._pin_failures.get(key, []) if now - t < PIN_WINDOW_S]
        self._pin_failures[key] = recent
        if len(recent) >= PIN_FAILURES:
            raise RateLimited(int(PIN_WINDOW_S - (now - recent[0])) + 1)
        return key

    async def _check_switch_pin(self, user_id: str, pin: str, pin_hash: str) -> None:
        key = self._switch_budget(user_id)
        # Counted before it is checked, so guesses sent in parallel share the one budget.
        self._pin_failures.setdefault(key, []).append(time.monotonic())
        if not await asyncio.to_thread(verify_secret, pin, pin_hash):
            raise Unauthorized("Wrong PIN", code="pin_invalid")
        self._pin_failures.pop(key, None)

    async def switch(self, actor: Any, target_user_id: str, pin: str | None) -> tuple[User, Profile, Role, AuthSession]:
        """Hand this shared device to another user: the same ``auth_session`` row, re-bound.

        A target with a PIN is only reached with it. A target with more access than the
        current user must have one, so a child cannot step into a parent. The re-bind is a
        compare-and-set on the row as this request saw it, and bumps its generation, so the
        previous user's tokens for this device are dead from the commit on.

        Switching to oneself hands nothing over. With a PIN it is the device asking "is this
        their PIN?": checked like any other target's, against the same budget of guesses.
        """
        if not actor.shared_device:
            raise Forbidden("This is not a shared device", code="not_shared_device")
        async with self._db.session() as s:
            row = await s.get(AuthSession, actor.session_id)
            if row is None or row.revoked_at is not None or row.user_id != actor.user_id or not row.shared_device:
                raise Unauthorized("Session revoked; sign in again", code="session_revoked")
            found = await self._switchable(s, User.id == target_user_id)
            if not found:
                raise NotFound("user not found")
            user, profile, role = found[0]
            if user.id == actor.user_id:
                if pin and profile.pin_hash:
                    await self._check_switch_pin(user.id, pin, profile.pin_hash)
                return user, profile, role, row
            if profile.pin_hash:
                if not pin:
                    self._switch_budget(user.id)
                    raise Unauthorized("Enter this user's PIN", code="pin_required")
                await self._check_switch_pin(user.id, pin, profile.pin_hash)
            elif is_wider(*access(user, role), frozenset(actor.permissions), actor.limits):
                raise Conflict("That user needs a PIN before this device can switch to them", code="pin_setup_required")
            claimed = await s.execute(
                update(AuthSession)
                .where(
                    AuthSession.sid == row.sid,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.shared_device.is_(True),
                    AuthSession.user_id == actor.user_id,
                    AuthSession.generation == row.generation,
                    # The target as it was judged above: still active, same access, same PIN.
                    select(User.id)
                    .where(User.id == user.id, User.status == "active", User.version == user.version)
                    .exists(),
                    select(Profile.id)
                    .where(Profile.id == profile.id, Profile.pin_hash.is_not_distinct_from(profile.pin_hash))
                    .exists(),
                )
                .values(
                    user_id=user.id,
                    profile_id=profile.id,
                    generation=row.generation + 1,
                    adult_unlocked_until=None,
                    last_seen_at=utcnow(),
                )
                .returning(AuthSession.sid)
            )
            if claimed.scalar_one_or_none() is None:
                raise Conflict("This device or that user just changed; try again", code="switch_conflict")
            await s.execute(update(User).where(User.id == user.id).values(last_seen_at=utcnow()))
            audit(s, actor.user_id, "session.switch", user.id, kind=row.kind, label=row.label)
            await s.commit()
            await s.refresh(row)
        return user, profile, role, row

    # ------------------------------------------------------------------ adult unlock
    def _pin_budget(self, user_id: str) -> None:
        now = time.monotonic()
        recent = [t for t in self._pin_failures.get(user_id, []) if now - t < PIN_WINDOW_S]
        self._pin_failures[user_id] = recent
        if len(recent) >= PIN_FAILURES:
            raise RateLimited(int(PIN_WINDOW_S - (now - recent[0])) + 1)

    async def adult_unlock(self, actor: Any, pin: str) -> datetime:
        """Open the adult category on this session for four hours with the user's own PIN."""
        if "watch.adult" not in actor.permissions:
            raise PermissionDenied(["watch.adult"])
        self._pin_budget(actor.user_id)
        async with self._db.session() as s:
            profile = await s.get(Profile, actor.profile_id)
            row = await s.get(AuthSession, actor.session_id)
            if profile is None or row is None or row.revoked_at is not None:
                raise Unauthorized("Session revoked; sign in again", code="session_revoked")
            if not profile.pin_hash:
                raise Conflict("Set a PIN before opening the adult category", code="pin_setup_required")
            if not verify_secret(pin, profile.pin_hash):
                self._pin_failures.setdefault(actor.user_id, []).append(time.monotonic())
                # 403, not 401: clients answer a 401 by refreshing and retrying the request.
                raise Forbidden("Wrong PIN", code="pin_invalid")
            until = utcnow() + timedelta(seconds=ADULT_UNLOCK_S)
            row.adult_unlocked_until = until
            await s.commit()
        self._pin_failures.pop(actor.user_id, None)
        return until

    async def adult_lock(self, actor: Any) -> None:
        async with self._db.session() as s:
            await s.execute(
                update(AuthSession).where(AuthSession.sid == actor.session_id).values(adult_unlocked_until=None)
            )
            await s.commit()

    # ------------------------------------------------------------------ activity
    # ``StreamRegistry``, assigned by the composition root: every stream resolved for a user.
    streams: Any = None

    async def now_playing(self, user_id: str) -> builtins.list[dict[str, Any]]:
        """What this user is streaming right now: ``id``, ``title``, ``kind``, ``since`` and ``client`` per stream.

        A stream is listed while it shows activity (see ``app.services.streams``); the Users
        screen and the stream-limit check read the same records.
        """
        if self.streams is None:
            return []
        playing: builtins.list[dict[str, Any]] = self.streams.now_playing(user_id)
        return playing
