"""Signed-in devices: the ``auth_session`` rows behind every token's ``sid``.

A session row is created when tokens are first issued and is what makes a device
listable and revocable. Revoking writes the legacy ``revoked_session`` deny-list in
the same transaction, so the stream and image chokepoints that only know that list
stop honouring the session too.
"""

from __future__ import annotations

import builtins
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.crypto import verify_secret
from app.infra.db import Database
from app.infra.models import AuthSession, Profile, RevokedSession, User, utcnow
from app.services.errors import Conflict, Forbidden, NotFound, PermissionDenied, ServiceError, Unauthorized
from app.services.permissions import OWNER_ROLE
from app.services.users import audit

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
