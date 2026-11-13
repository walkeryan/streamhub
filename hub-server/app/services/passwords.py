"""Username and password sign-in, for people with neither a Google nor a Plex account.

Nobody registers themselves and nothing is recovered by e-mail: the owner hands out a
setup link (an invite, or one that names an existing user), and the owner resets.
A wrong name and a wrong password are one answer, in body, status and work done.
Guessing is throttled by name and by client address, with a ceiling for everyone.
In-memory state: one server worker, as the Google and Plex transactions need.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.crypto import hash_secret, verify_secret
from app.infra.db import Database
from app.infra.models import Invite, Profile, Role, User, utcnow
from app.services.auth import AuthService
from app.services.common_passwords import COMMON_PASSWORDS
from app.services.errors import Conflict, Forbidden, NotFound, ServiceError, Unauthorized
from app.services.invites import TOKEN_RE, InvitesService, token_hash
from app.services.sessions import RateLimited, SessionMeta, SessionsService
from app.services.users import access, audit, mirror

__all__ = ["COMMON_PASSWORDS", "LoginThrottle", "PasswordAuth", "clean_username", "validate_password"]

USERNAME_RE = re.compile(r"[a-z0-9._-]{3,32}")
PASSWORD_MIN, PASSWORD_MAX = 10, 256
SETUP_HOURS = 72


def clean_username(value: Any) -> str:
    """Lower case, 3 to 32 of a-z 0-9 . - _ ; or ``invalid_username``."""
    name = str(value or "").strip().lower()
    if not USERNAME_RE.fullmatch(name):
        raise ServiceError(
            "A username is 3 to 32 letters, digits, dots, dashes or underscores", code="invalid_username"
        )
    return name


def validate_password(password: Any, username: str) -> str:
    """Long enough, not built around the username, not one everybody tries first."""
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise ServiceError(f"A password needs at least {PASSWORD_MIN} characters", code="weak_password")
    lowered = password.lower()
    if username and username.lower() in lowered:
        raise ServiceError("A password cannot contain the username", code="weak_password")
    if lowered in COMMON_PASSWORDS:
        raise ServiceError("That password is one of the most common; choose another", code="weak_password")
    return password


# ---------------------------------------------------------------- throttle
@dataclass(slots=True)
class _Record:
    attempts: list[float] = field(default_factory=list)
    locked_until: float = 0.0
    strikes: int = 0
    last: float = 0.0


@dataclass(frozen=True, slots=True)
class Ticket:
    """One attempt, counted against its keys from the moment it was let through."""

    keys: tuple[str, ...]
    at: float


class LoginThrottle:
    """Attempts are counted when they start, not when they fail, so a burst sent at once is
    held to the same number as one sent slowly. A key that fills its allowance with failures
    is locked, for twice as long each time, up to an hour; a day of quiet forgets that.

    Keys carry a one-letter scope: ``u:`` a name being signed in to, ``a:`` a client address.
    """

    WINDOW_S = 15 * 60
    BASE_LOCK_S = 60
    MAX_LOCK_S = 3600
    FORGET_S = 24 * 3600
    MAX_KEYS = 20_000

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        # scope -> (attempts allowed per window, before a lock)
        self.limits: dict[str, tuple[int]] = {"u": (5,), "a": (20,), "p": (10,)}
        # (failures by everyone, per this many seconds)
        self.ceiling: tuple[int, int] = (300, 300)
        self._records: dict[str, _Record] = {}
        self._all: deque[float] = deque()

    def _record(self, key: str, now: float) -> _Record:
        record = self._records.get(key)
        if record is None:
            if len(self._records) >= self.MAX_KEYS:
                self._records = {
                    k: r for k, r in self._records.items() if r.locked_until > now or now - r.last < self.WINDOW_S
                }
            record = self._records[key] = _Record()
        if record.strikes and now - record.last > self.FORGET_S:
            record.strikes = 0
        record.attempts = [t for t in record.attempts if now - t < self.WINDOW_S]
        return record

    def begin(self, *keys: str) -> Ticket:
        """Count one attempt against every key, or refuse it with how long to wait. No awaits:
        between the check and the count nothing else runs."""
        now = self.clock()
        limit, window = self.ceiling
        while self._all and now - self._all[0] >= window:
            self._all.popleft()
        if len(self._all) >= limit:
            raise RateLimited(max(1, int(window - (now - self._all[0]) + 0.999)))
        records = [(key, self._record(key, now)) for key in keys]
        for key, record in records:
            if record.locked_until > now:
                raise RateLimited(max(1, int(record.locked_until - now + 0.999)))
            if len(record.attempts) >= self.limits[key[0]][0]:
                # Full of attempts still being judged: wait for the oldest to age out.
                raise RateLimited(max(1, int(self.WINDOW_S - (now - record.attempts[0]) + 0.999)))
        for _key, record in records:
            record.attempts.append(now)
            record.last = now
        self._all.append(now)
        return Ticket(tuple(keys), now)

    def fail(self, ticket: Ticket) -> dict[str, int]:
        """The attempt was wrong. Returns the keys this failure locked, with the seconds each is locked for."""
        now = self.clock()
        locked: dict[str, int] = {}
        for key in ticket.keys:
            record = self._records.get(key)
            if record is None or record.locked_until > now:
                continue
            if len(record.attempts) >= self.limits[key[0]][0]:
                record.strikes += 1
                seconds = min(self.BASE_LOCK_S * 2 ** (record.strikes - 1), self.MAX_LOCK_S)
                record.locked_until, record.attempts, record.last = now + seconds, [], now
                locked[key] = seconds
        return locked

    def succeed(self, ticket: Ticket) -> None:
        """The attempt was right: it is not held against anyone, and the name starts clean."""
        for key in ticket.keys:
            record = self._records.get(key)
            if record is None:
                continue
            if key[0] == "a":
                if ticket.at in record.attempts:
                    record.attempts.remove(ticket.at)
            else:
                self._records.pop(key, None)
        try:
            self._all.remove(ticket.at)
        except ValueError:
            pass


# ---------------------------------------------------------------- the service
class PasswordAuth:
    def __init__(
        self,
        db: Database,
        auth: AuthService,
        setting: Callable[[str], Any],
        *,
        available: Callable[[], bool] = lambda: True,
        throttle: LoginThrottle | None = None,
    ) -> None:
        self._db = db
        self.auth = auth
        self._setting = setting
        self._available = available
        self.throttle = throttle or LoginThrottle()
        # What an unknown name is verified against, so it costs what a known one costs.
        self._stand_in = hash_secret(secrets.token_urlsafe(24))

    @property
    def enabled(self) -> bool:
        return bool(self._setting("password_sign_in")) and self._available()

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise Forbidden("Username and password sign-in is not enabled", code="password_disabled")

    @property
    def _sessions(self) -> SessionsService:
        return self.auth.sessions

    # ------------------------------------------------------------------ sign in
    async def _judge(self, password: str, stored: str | None, ticket: Ticket, user: User | None) -> bool:
        """Verify, off the event loop, and settle the attempt. A lock is written to the audit log."""
        ok = await asyncio.to_thread(verify_secret, password, stored or self._stand_in)
        if ok and stored:
            self.throttle.succeed(ticket)
            return True
        locked = self.throttle.fail(ticket)
        if locked:
            async with self._db.session() as s:
                for key, seconds in locked.items():
                    # The name that was typed is not written down: it may be a password in the wrong box.
                    scope = {"u": "username", "a": "address", "p": "profile"}[key[0]]
                    target = user.id if user is not None and scope != "address" else None
                    audit(s, None, "auth.lockout", target, scope=scope, seconds=seconds)
                await s.commit()
        return False

    async def sign_in(
        self, username: str, password: str, address: str | None, meta: SessionMeta | None = None
    ) -> tuple[Profile, dict[str, str]]:
        self._require_enabled()
        # Whatever was typed is a name to look for; an impossible one is simply not found.
        name = str(username or "").strip().lower()[:64]
        ticket = self.throttle.begin(f"u:{name}", f"a:{address or 'unknown'}")
        async with self._db.session() as s:
            user = (await s.execute(select(User).where(User.username == name))).scalar_one_or_none() if name else None
            profile = (
                (await s.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
                if user is not None
                else None
            )
        stored = user.password_hash if user is not None and profile is not None else None
        if not await self._judge(password, stored, ticket, user) or profile is None:
            raise Unauthorized("Wrong username or password", code="invalid_credentials")
        # Pending or suspended is said only to someone who has just proved who they are.
        return profile, await self.auth.issue(profile, meta=meta)

    # ------------------------------------------------------------------ setup links
    @staticmethod
    async def _find(s: AsyncSession, token: str) -> Invite | None:
        if not TOKEN_RE.fullmatch(token or ""):
            return None
        return (await s.execute(select(Invite).where(Invite.token_hash == token_hash(token)))).scalar_one_or_none()

    @staticmethod
    async def _setup_target(s: AsyncSession, row: Invite) -> User | None:
        """The user a setup link is for, while the link is good and whoever issued it still may."""
        if row.revoked_at is not None or row.expires_at <= utcnow() or row.uses >= row.max_uses:
            return None
        issuer = await s.get(User, row.created_by_user_id) if row.created_by_user_id else None
        issuer_role = await s.get(Role, issuer.role_id) if issuer is not None and issuer.role_id else None
        if "users.manage" not in access(issuer, issuer_role)[0]:
            return None
        return await s.get(User, row.user_id) if row.user_id else None

    async def lookup(self, token: str) -> dict[str, Any]:
        """What the page behind a link needs to draw itself. Every dead link looks the same."""
        nothing = {"valid": False, "display_name": None, "username": None, "setup": False}
        async with self._db.session() as s:
            row = await self._find(s, token)
            if row is None:
                return nothing
            if row.user_id is not None:
                user = await self._setup_target(s, row)
                if user is None:
                    return nothing
                return {"valid": True, "display_name": user.display_name, "username": user.username, "setup": True}
            if not await InvitesService.usable(s, row):
                return nothing
            return {"valid": True, "display_name": row.display_name, "username": None, "setup": False}

    async def accept(
        self, token: str, username: str, password: str, meta: SessionMeta | None = None
    ) -> tuple[Profile, dict[str, str]]:
        """Redeem a link by choosing a username and a password, and sign in with them.

        An invite makes a new user with the invite's role. A setup link sets the password of
        the one user it names, who keeps the username they already had. Nothing is spent
        unless the whole thing goes through: a refused name or password leaves the link good.
        """
        self._require_enabled()
        dead = Forbidden("This link can no longer be used", code="invite_invalid")
        async with self._db.session() as s:
            row = await self._find(s, token)
            if row is None:
                raise dead
            target = await self._setup_target(s, row) if row.user_id is not None else None
            if row.user_id is not None and target is None:
                raise dead
            name = target.username if target is not None and target.username else clean_username(username)
            validate_password(password, name)
        hashed = await asyncio.to_thread(hash_secret, password)
        async with self._db.session() as s:
            try:
                if row.user_id is not None:
                    profile = await self._set_up(s, row, name, hashed)
                else:
                    profile = await self._admit(s, token, name, hashed)
                if profile is None:
                    raise dead
                await s.commit()
            except IntegrityError as exc:
                # Two people chose one name at the same moment; the index decided.
                raise Conflict("That username is taken", code="username_taken") from exc
        return profile, await self.auth.issue(profile, meta=meta)

    @staticmethod
    async def _name_free(s: AsyncSession, name: str, user_id: str | None = None) -> None:
        holder = (await s.execute(select(User.id).where(User.username == name))).scalar_one_or_none()
        if holder is not None and holder != user_id:
            raise Conflict("That username is taken", code="username_taken")

    async def _admit(self, s: AsyncSession, token: str, name: str, hashed: str) -> Profile | None:
        await self._name_free(s, name)
        invited = await InvitesService.redeem_in(s, token)
        if invited is None:
            return None
        role = invited.role
        user = User(
            id=secrets.token_hex(8),
            kind="password",
            username=name,
            password_hash=hashed,
            display_name=(invited.display_name or name)[:64],
            status="active",
            role_id=role.id,
            grants=[],
            denials=[],
            limits={},
            approved_at=utcnow(),
            approved_by_user_id=invited.inviter,
        )
        s.add(user)
        await s.flush()
        profile = Profile(
            id=secrets.token_hex(8),
            name=user.display_name,
            is_admin=False,
            theme_id="midnight",
            languages=[],
            kids=role.id == "kid",
            prefs={},
            user_id=user.id,
        )
        mirror(profile, user, role)
        s.add(profile)
        audit(
            s, user.id, "user.invite_accept", user.id, invite_id=invited.invite_id, role_id=role.id, method="password"
        )
        return profile

    async def _set_up(self, s: AsyncSession, row: Invite, name: str, hashed: str) -> Profile | None:
        if await self._setup_target(s, row) is None:
            return None
        claimed = (
            await s.execute(
                update(Invite)
                .where(Invite.id == row.id, Invite.revoked_at.is_(None), Invite.uses < Invite.max_uses)
                .values(uses=Invite.uses + 1)
                .returning(Invite.user_id)
            )
        ).scalar_one_or_none()
        user = await s.get(User, claimed) if claimed else None
        profile = (
            (await s.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
            if user is not None
            else None
        )
        if user is None or profile is None:
            return None
        await self._name_free(s, name, user.id)
        user.username, user.password_hash = name, hashed
        audit(s, user.id, "user.password_set", user.id, via="setup_link")
        return profile

    # ------------------------------------------------------------------ change and reset
    async def change(
        self, principal: Any, current: str | None, new: str, username: str | None, address: str | None
    ) -> None:
        """Set one's own password. Changing an existing one needs it, and signs every other device out."""
        self._require_enabled()
        if principal.shared_device:
            # Whoever holds the household's remote must not be able to give this user a way in from anywhere.
            raise Forbidden("Sign-in methods cannot be changed on a shared device", code="shared_device")
        async with self._db.session() as s:
            user = await s.get(User, principal.user_id)
            if user is None:
                raise NotFound("user not found")
            had = bool(user.username and user.password_hash)
            name = user.username or clean_username(username)
            stored = user.password_hash
        if had:
            ticket = self.throttle.begin(f"u:{name}", f"a:{address or 'unknown'}")
            if not await self._judge(current or "", stored, ticket, user):
                raise Forbidden("That is not your current password", code="wrong_password")
        validate_password(new, name)
        hashed = await asyncio.to_thread(hash_secret, new)
        async with self._db.session() as s:
            user = await s.get(User, principal.user_id)
            if user is None:
                raise NotFound("user not found")
            await self._name_free(s, name, user.id)
            user.username, user.password_hash = name, hashed
            if had:
                await self._sessions.revoke_others(s, user.id, principal.session_id)
            audit(s, user.id, "user.password_change" if had else "user.password_set", user.id)
            try:
                await s.commit()
            except IntegrityError as exc:
                raise Conflict("That username is taken", code="username_taken") from exc

    async def reset(self, actor: Any, user_id: str) -> dict[str, Any]:
        """The owner's reset: the password and every session go now, and a new setup link comes back."""
        token = secrets.token_urlsafe(32)
        async with self._db.session() as s:
            user = await s.get(User, user_id)
            if user is None:
                raise NotFound("user not found")
            await self.auth.users.guard_identity(s, actor, user)
            if user.status == "pending" or not user.role_id:
                raise Conflict("Approve this user before giving them a password", code="not_active")
            user.password_hash = None
            await self._sessions.revoke_others(s, user.id, actor.session_id if user.id == actor.user_id else "")
            # One live setup link per person: an older one stops working.
            await s.execute(
                update(Invite).where(Invite.user_id == user.id, Invite.revoked_at.is_(None)).values(revoked_at=utcnow())
            )
            row = Invite(
                id=secrets.token_hex(8),
                token_hash=token_hash(token),
                role_id=user.role_id,
                created_by_user_id=actor.user_id or None,
                expires_at=utcnow() + timedelta(hours=SETUP_HOURS),
                max_uses=1,
                uses=0,
                display_name=user.display_name,
                user_id=user.id,
            )
            s.add(row)
            audit(s, actor.user_id, "user.password_reset", user.id, invite_id=row.id)
            await s.commit()
        # The only time the token is ever returned.
        return {"url": f"/invite/{token}", "expires_at": row.expires_at}
