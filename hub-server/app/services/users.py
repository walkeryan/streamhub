"""Users and roles: one person, one user, above the profile that holds their viewer state.

Every rule about who may change whom lives here, next to the change it guards:
the last active owner stays an owner, nobody edits their own access, and only an
owner hands out the Owner role or the management permissions. Every mutation bumps
the affected users' ``version`` (so tokens go stale on the next request) and writes
the audit log in the same transaction.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.crypto import hash_secret
from app.infra.db import Database
from app.infra.models import AuditLog, AuthSession, Profile, Progress, Role, User, utcnow
from app.services.errors import Conflict, Forbidden, NotFound, PermissionDenied, ServiceError
from app.services.notices import new_notice
from app.services.permissions import (
    BUILTIN_ROLES,
    NO_LIMITS,
    OWNER_GRANTED,
    OWNER_ROLE,
    Limits,
    clean_limits,
    clean_permissions,
    effective_limits,
    effective_permissions,
    is_wider,
)

log = logging.getLogger(__name__)

STATUSES = ("pending", "active", "suspended")


def audit(
    s: AsyncSession, actor_user_id: str | None, action: str, target_user_id: str | None = None, **detail: Any
) -> None:
    """Queue one audit row in the caller's transaction. Never pass a secret, a token or a PIN."""
    s.add(AuditLog(actor_user_id=actor_user_id or None, action=action, target_user_id=target_user_id, detail=detail))


def validate_pin(pin: str) -> str:
    if not (4 <= len(pin) <= 8 and pin.isascii() and pin.isdigit()):
        raise ServiceError("PIN must be 4 to 8 digits", code="invalid_pin")
    return pin


def access(user: User | None, role: Role | None) -> tuple[frozenset[str], Limits]:
    """Effective permissions and limits. Anyone not active, or without a role, holds nothing."""
    if user is None or role is None or user.status != "active":
        return frozenset(), NO_LIMITS
    return standing(user, role)


def standing(user: User, role: Role) -> tuple[frozenset[str], Limits]:
    """What the role and the user's own overrides add up to, whatever the user's status.

    Authority comes from ``access``. This is only the measure a shared device's hand-over is
    judged by once its current user has been suspended.
    """
    return (
        effective_permissions(role.permissions or [], user.grants or [], user.denials or []),
        effective_limits(role.limits or {}, user.limits or {}),
    )


def sign_in_methods(user: User) -> list[str]:
    """The ways this person can prove who they are from anywhere, in the order a screen lists them."""
    held = (
        ("google", user.google_subject),
        ("plex", user.plex_account_id),
        ("password", user.username and user.password_hash),
    )
    return [name for name, value in held if value]


def mirror(profile: Profile, user: User, role: Role | None) -> None:
    """Keep the profile's legacy admin flag equal to what the user's access now says."""
    profile.is_admin = "system.manage" in access(user, role)[0]


async def ensure_user(s: AsyncSession, profile: Profile) -> User:
    """The profile's user, creating one for a row written without it.

    Mirrors the upgrade rule (admin profile: Owner, kids profile: Kid, otherwise Family)
    so a profile inserted by an older code path is never left without an identity.
    """
    if profile.user_id:
        user = await s.get(User, profile.user_id)
        if user is not None:
            return user
    role_id = OWNER_ROLE if profile.is_admin else "kid" if profile.kids else "family"
    user = User(
        id=secrets.token_hex(8),
        kind="managed",
        display_name=profile.name,
        status="active",
        role_id=role_id,
        grants=[],
        denials=[],
        limits={},
        approved_at=utcnow(),
    )
    s.add(user)
    await s.flush()
    # Compare-and-set: two first requests for the same profile must agree on one user.
    claimed = await s.execute(
        update(Profile).where(Profile.id == profile.id, Profile.user_id.is_(None)).values(user_id=user.id)
    )
    if not claimed.rowcount:  # type: ignore[attr-defined]
        await s.delete(user)
        await s.flush()
        await s.refresh(profile)
        existing = await s.get(User, profile.user_id) if profile.user_id else None
        if existing is None:
            raise Conflict("profile has no user", code="user_missing")
        return existing
    profile.user_id = user.id
    return user


@dataclass(slots=True)
class Identity:
    """A profile with the user and role above it, and the access they add up to."""

    profile: Profile
    user: User
    role: Role | None
    permissions: frozenset[str]
    limits: Limits


@dataclass(slots=True)
class GoogleSignIn:
    user: User
    profile: Profile
    created: bool


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """Someone an identity provider vouches for. ``subject`` is the provider's own stable id."""

    provider: str  # google | plex
    subject: str
    email: str = ""
    name: str = ""
    picture: str | None = None
    handle: str | None = None  # the Plex username; Google has none


# Where each provider's stable id lives on the user.
_SUBJECT = {"google": User.google_subject, "plex": User.plex_account_id}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "role"


def _snapshot(user: User) -> dict[str, Any]:
    return {
        "status": user.status,
        "role_id": user.role_id,
        "grants": sorted(user.grants or []),
        "denials": sorted(user.denials or []),
        "limits": dict(user.limits or {}),
    }


class UsersService:
    def __init__(
        self,
        db: Database,
        *,
        sessions: Any = None,
        setting: Callable[[str], Any] | None = None,
        http_factory: Any = None,
    ) -> None:
        self._db = db
        self._sessions = sessions
        self._setting = setting or (lambda _key: None)
        self._http = http_factory

    # ------------------------------------------------------------------ reading
    async def identity(self, profile_id: str) -> Identity | None:
        async with self._db.session() as s:
            profile = await s.get(Profile, profile_id)
            if profile is None:
                return None
            user = await ensure_user(s, profile)
            role = await s.get(Role, user.role_id) if user.role_id else None
            await s.commit()
        return Identity(profile, user, role, *access(user, role))

    @staticmethod
    def role_out(role: Role, user_count: int = 0) -> dict[str, Any]:
        return {
            "id": role.id,
            "name": role.name,
            "builtin": role.builtin,
            "editable": role.id != OWNER_ROLE,
            "permissions": sorted(role.permissions or []),
            "limits": effective_limits(role.limits or {}, {}).as_dict(),
            "on_shared_devices": role.on_shared_devices,
            "user_count": user_count,
            "updated_at": role.updated_at,
        }

    @staticmethod
    def user_out(user: User, profile: Profile | None, role: Role | None) -> dict[str, Any]:
        shared = user.on_shared_devices if user.on_shared_devices is not None else bool(role and role.on_shared_devices)
        return {
            "id": user.id,
            "kind": user.kind,
            "display_name": user.display_name,
            "email": user.email,
            "picture": user.picture,
            "avatar": profile.avatar if profile else None,
            "status": user.status,
            "role_id": user.role_id,
            "role_name": role.name if role else None,
            "profile_id": profile.id if profile else None,
            "has_pin": bool(profile and profile.pin_hash),
            "on_shared_devices": shared,
            "plex_account_linked": user.plex_account_linked,
            "username": user.username,
            "plex_username": user.plex_username if user.plex_account_id else None,
            "sign_in_methods": sign_in_methods(user),
            "created_at": user.created_at,
            "approved_at": user.approved_at,
            "last_seen_at": user.last_seen_at,
        }

    def me_out(
        self, identity: Identity, *, shared_device: bool = False, adult_unlocked: bool = False
    ) -> dict[str, Any]:
        role = identity.role
        return {
            "status": identity.user.status,
            "user": self.user_out(identity.user, identity.profile, role),
            "role": {"id": role.id, "name": role.name, "builtin": role.builtin} if role else None,
            "permissions": sorted(identity.permissions),
            "limits": identity.limits.as_dict(),
            "shared_device": shared_device,
            "adult_unlocked": adult_unlocked,
            "tokens": None,
        }

    async def _rows(self, s: AsyncSession, *where: Any) -> builtins.list[tuple[User, Profile | None, Role | None]]:
        result = await s.execute(
            select(User, Profile, Role)
            .outerjoin(Profile, Profile.user_id == User.id)
            .outerjoin(Role, Role.id == User.role_id)
            .where(*where)
            .order_by(User.created_at, User.id)
        )
        return [(u, p, r) for u, p, r in result.all()]

    async def list(self, status: str | None = None) -> builtins.list[dict[str, Any]]:
        if status is not None and status not in STATUSES:
            raise ServiceError("unknown status", code="invalid_status")
        async with self._db.session() as s:
            rows = await self._rows(s, *([User.status == status] if status else []))
        out = []
        for user, profile, role in rows:
            playing = await self._sessions.now_playing(user.id) if self._sessions is not None else []
            out.append({**self.user_out(user, profile, role), "now_playing": playing})
        return out

    async def get(self, user_id: str) -> dict[str, Any]:
        async with self._db.session() as s:
            rows = await self._rows(s, User.id == user_id)
            if not rows:
                raise NotFound("user not found")
            user, profile, role = rows[0]
            history = []
            if profile is not None:
                history = [
                    {
                        "ref_id": p.ref_id,
                        "kind": p.kind,
                        "position_ms": p.position_ms,
                        "duration_ms": p.duration_ms,
                        "completed": p.completed,
                        "updated_at": p.updated_at,
                    }
                    for p in (
                        await s.execute(
                            select(Progress)
                            .where(Progress.profile_id == profile.id)
                            .order_by(Progress.updated_at.desc())
                            .limit(20)
                        )
                    ).scalars()
                ]
        permissions, limits = access(user, role)
        sessions = await self._sessions.list(user.id) if self._sessions is not None else []
        playing = await self._sessions.now_playing(user.id) if self._sessions is not None else []
        return {
            **self.user_out(user, profile, role),
            "now_playing": playing,
            "grants": sorted(user.grants or []),
            "denials": sorted(user.denials or []),
            "limit_overrides": dict(user.limits or {}),
            "on_shared_devices_override": user.on_shared_devices,
            "permissions": sorted(permissions),
            "limits": limits.as_dict(),
            "sessions": sessions,
            "history": history,
        }

    async def audit_log(self, user_id: str | None = None, limit: int = 100) -> builtins.list[dict[str, Any]]:
        query = select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit)
        if user_id:
            query = query.where((AuditLog.target_user_id == user_id) | (AuditLog.actor_user_id == user_id))
        async with self._db.session() as s:
            rows = list((await s.execute(query)).scalars())
            ids = {x for r in rows for x in (r.actor_user_id, r.target_user_id) if x}
            names = (
                dict((await s.execute(select(User.id, User.display_name).where(User.id.in_(ids)))).tuples().all())
                if ids
                else {}
            )
        return [
            {
                "id": r.id,
                "at": r.at,
                "action": r.action,
                "actor_user_id": r.actor_user_id,
                "actor_name": names.get(r.actor_user_id or ""),
                "target_user_id": r.target_user_id,
                "target_name": names.get(r.target_user_id or ""),
                "detail": r.detail or {},
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ guards
    @staticmethod
    async def _load(s: AsyncSession, user_id: str) -> tuple[User, Profile, Role | None]:
        user = await s.get(User, user_id)
        profile = (
            (await s.execute(select(Profile).where(Profile.user_id == user_id))).scalar_one_or_none()
            if user is not None
            else None
        )
        if user is None or profile is None:
            raise NotFound("user not found")
        return user, profile, await s.get(Role, user.role_id) if user.role_id else None

    @staticmethod
    async def _actor_is_owner(s: AsyncSession, actor: Any) -> bool:
        me = await s.get(User, actor.user_id) if actor.user_id else None
        return me is not None and me.role_id == OWNER_ROLE and me.status == "active"

    @staticmethod
    def _owner_only(is_owner: bool, user: User) -> None:
        """An owner's account is only ever changed by an owner."""
        if user.role_id == OWNER_ROLE and not is_owner:
            raise Forbidden("Only an owner can change an owner", code="owner_required")

    async def guard_identity(self, s: AsyncSession, actor: Any, user: User) -> None:
        """May ``actor`` set what proves someone is ``user``: their PIN, their password, a paired device?

        Each of those lets the actor act as that user afterwards, so beyond ``users.manage``
        the target may not hold more access than the actor does. Oneself is always fine.
        """
        if user.id == actor.user_id:
            return
        if "users.manage" not in actor.permissions:
            raise PermissionDenied(["users.manage"])
        is_owner = await self._actor_is_owner(s, actor)
        self._owner_only(is_owner, user)
        if is_owner:
            return
        role = await s.get(Role, user.role_id) if user.role_id else None
        held = effective_permissions(role.permissions or [] if role else [], user.grants or [], user.denials or [])
        limits = effective_limits(role.limits or {} if role else {}, user.limits or {})
        if is_wider(held, limits, frozenset(actor.permissions), actor.limits):
            raise Forbidden("That user has more access than you do", code="wider_access")

    async def guard_profile_identity(self, actor: Any, profile_id: str) -> None:
        async with self._db.session() as s:
            profile = await s.get(Profile, profile_id)
            if profile is None:
                raise NotFound("profile not found")
            await self.guard_identity(s, actor, await ensure_user(s, profile))
            await s.commit()

    @staticmethod
    def _may_assign(is_owner: bool, role: Role, gained: frozenset[str] | set[str]) -> None:
        if is_owner:
            return
        if role.id == OWNER_ROLE or gained & OWNER_GRANTED:
            raise Forbidden("Only an owner can grant management access", code="owner_required")

    @staticmethod
    async def _keep_an_owner(s: AsyncSession, user: User, role_id: str | None, status: str) -> None:
        if user.role_id != OWNER_ROLE or user.status != "active" or (role_id == OWNER_ROLE and status == "active"):
            return
        others = (
            await s.execute(
                select(func.count(User.id)).where(
                    User.role_id == OWNER_ROLE, User.status == "active", User.id != user.id
                )
            )
        ).scalar_one()
        if not others:
            raise Conflict("The last active owner cannot be demoted, suspended or removed", code="last_owner")

    @staticmethod
    def _changed(user: User, profile: Profile, role: Role | None) -> None:
        user.version += 1
        mirror(profile, user, role)

    @staticmethod
    async def _role(s: AsyncSession, role_id: Any) -> Role:
        role = await s.get(Role, str(role_id)) if role_id else None
        if role is None:
            raise NotFound("role not found")
        return role

    # ------------------------------------------------------------------ users
    async def create_managed(self, actor: Any, data: dict[str, Any]) -> dict[str, Any]:
        """A user without a Google identity (a child, typically), usable on shared devices."""
        name = str(data.get("name") or "").strip()[:64]
        if not name:
            raise ServiceError("name is required", code="invalid_name")
        pin = data.get("pin")
        pin_hash = await asyncio.to_thread(hash_secret, validate_pin(str(pin))) if pin else None
        async with self._db.session() as s:
            role = await self._role(s, data.get("role_id") or ("kid" if data.get("kids") else "family"))
            self._may_assign(await self._actor_is_owner(s, actor), role, set(role.permissions or []))
            user = User(
                id=secrets.token_hex(8),
                kind="managed",
                display_name=name,
                status="active",
                role_id=role.id,
                grants=[],
                denials=[],
                limits={},
                approved_at=utcnow(),
                approved_by_user_id=actor.user_id or None,
            )
            s.add(user)
            await s.flush()
            profile = Profile(
                id=secrets.token_hex(8),
                name=name,
                avatar=data.get("avatar"),
                is_admin=False,
                theme_id=str(data.get("theme_id") or "midnight"),
                languages=list(data.get("languages") or []),
                kids=bool(data.get("kids")) or role.id == "kid",
                pin_hash=pin_hash,
                prefs={},
                user_id=user.id,
            )
            mirror(profile, user, role)
            s.add(profile)
            audit(s, actor.user_id, "user.create", user.id, kind="managed", role_id=role.id)
            await s.commit()
        return self.user_out(user, profile, role)

    async def update(self, actor: Any, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._db.session() as s:
            user, profile, role = await self._load(s, user_id)
            is_owner = await self._actor_is_owner(s, actor)
            self._owner_only(is_owner, user)
            before = _snapshot(user)
            if {"role_id", "grants", "denials", "limits"} & patch.keys():
                if user.id == actor.user_id:
                    raise Forbidden("You cannot change your own role, permissions or limits", code="self_edit")
                if user.status == "pending":
                    raise Conflict("Approve this user before changing their access", code="not_active")
                new_role = await self._role(s, patch["role_id"]) if "role_id" in patch else role
                assert new_role is not None
                switched = new_role.id != user.role_id
                grants = clean_permissions(patch["grants"] or []) if "grants" in patch else list(user.grants or [])
                denials = clean_permissions(patch["denials"] or []) if "denials" in patch else list(user.denials or [])
                limits = clean_limits(patch["limits"] or {}) if "limits" in patch else dict(user.limits or {})
                if new_role.id == OWNER_ROLE:
                    if any(patch.get(k) for k in ("grants", "denials", "limits")):
                        raise ServiceError("The Owner role cannot be narrowed or extended", code="owner_fixed")
                    grants, denials, limits = [], [], {}
                # Judged on what the stored role and overrides confer, whatever the user's status.
                held = effective_permissions(
                    role.permissions or [] if role else [], user.grants or [], user.denials or []
                )
                gained = effective_permissions(new_role.permissions or [], grants, denials) - held
                if not is_owner and ((switched and new_role.id == OWNER_ROLE) or gained & OWNER_GRANTED):
                    raise Forbidden("Only an owner can grant management access", code="owner_required")
                await self._keep_an_owner(s, user, new_role.id, user.status)
                user.role_id, user.grants, user.denials, user.limits = new_role.id, grants, denials, limits
                if switched and "kid" in (before["role_id"], new_role.id):
                    profile.kids = new_role.id == "kid"
                role = new_role
            if patch.get("name") is not None:
                name = str(patch["name"]).strip()[:64]
                if not name:
                    raise ServiceError("name is required", code="invalid_name")
                user.display_name = profile.name = name
            if "avatar" in patch:
                profile.avatar = patch["avatar"]
            if "on_shared_devices" in patch:
                value = patch["on_shared_devices"]
                user.on_shared_devices = None if value is None else bool(value)
            if patch.get("plex_account_linked") is not None:
                await self._link_plex(s, actor, user, bool(patch["plex_account_linked"]), is_owner)
            after = _snapshot(user)
            self._changed(user, profile, role)
            if before["role_id"] != after["role_id"]:
                audit(s, actor.user_id, "user.role_change", user.id, before=before["role_id"], after=after["role_id"])
            access_before = {k: before[k] for k in ("grants", "denials", "limits")}
            access_after = {k: after[k] for k in ("grants", "denials", "limits")}
            if access_before != access_after:
                audit(s, actor.user_id, "user.access_change", user.id, before=access_before, after=access_after)
            profile_fields = sorted({"name", "avatar", "on_shared_devices"} & patch.keys())
            if profile_fields:
                audit(s, actor.user_id, "user.update", user.id, fields=profile_fields)
            await s.commit()
        return await self.get(user_id)

    async def _link_plex(self, s: AsyncSession, actor: Any, user: User, linked: bool, is_owner: bool) -> None:
        """Say whose Plex account the server's Plex token belongs to. One user at most: linking
        one unlinks whoever held it, and each change is its own audit row."""
        if user.plex_account_linked == linked:
            return
        if linked:
            holders = (
                await s.execute(select(User).where(User.plex_account_linked.is_(True), User.id != user.id))
            ).scalars()
            for holder in holders:
                self._owner_only(is_owner, holder)
                holder.plex_account_linked = False
                audit(s, actor.user_id, "user.plex_unlink", holder.id, moved_to=user.id)
        user.plex_account_linked = linked
        audit(s, actor.user_id, "user.plex_link" if linked else "user.plex_unlink", user.id)

    async def approve(self, actor: Any, user_id: str, role_id: str) -> dict[str, Any]:
        async with self._db.session() as s:
            user, profile, _ = await self._load(s, user_id)
            if user.status != "pending":
                raise Conflict("This user is not waiting for approval", code="not_pending")
            role = await self._role(s, role_id)
            self._may_assign(await self._actor_is_owner(s, actor), role, set(role.permissions or []))
            user.status, user.role_id = "active", role.id
            user.approved_at, user.approved_by_user_id = utcnow(), actor.user_id or None
            profile.kids = role.id == "kid"
            self._changed(user, profile, role)
            audit(s, actor.user_id, "user.approve", user.id, role_id=role.id)
            await s.commit()
        return await self.get(user_id)

    async def deny(self, actor: Any, user_id: str) -> None:
        async with self._db.session() as s:
            user, _, _ = await self._load(s, user_id)
            if user.status != "pending":
                raise Conflict("This user is not waiting for approval", code="not_pending")
            audit(s, actor.user_id, "user.deny", user.id, display_name=user.display_name, email=user.email)
            await self._remove(s, user)
            await s.commit()

    async def set_status(self, actor: Any, user_id: str, status: str) -> dict[str, Any]:
        """Suspend an active user or reinstate a suspended one."""
        expected, action = ("active", "user.suspend") if status == "suspended" else ("suspended", "user.reinstate")
        async with self._db.session() as s:
            user, profile, role = await self._load(s, user_id)
            self._owner_only(await self._actor_is_owner(s, actor), user)
            if user.status != expected:
                raise Conflict(f"This user is not {expected}", code=f"not_{expected}")
            await self._keep_an_owner(s, user, user.role_id, status)
            user.status = status
            self._changed(user, profile, role)
            audit(s, actor.user_id, action, user.id, before=expected, after=status)
            await s.commit()
        return await self.get(user_id)

    async def delete(self, actor: Any, user_id: str) -> None:
        async with self._db.session() as s:
            user, _, _ = await self._load(s, user_id)
            self._owner_only(await self._actor_is_owner(s, actor), user)
            await self._keep_an_owner(s, user, None, "removed")
            audit(
                s,
                actor.user_id,
                "user.delete",
                user.id,
                display_name=user.display_name,
                kind=user.kind,
                role_id=user.role_id,
            )
            await self._remove(s, user)
            await s.commit()

    @staticmethod
    async def _remove(s: AsyncSession, user: User) -> None:
        # The profile and everything keyed on it (viewer state, sessions, notices) cascade.
        await s.execute(delete(Profile).where(Profile.user_id == user.id))
        await s.delete(user)

    async def set_pin(self, actor: Any, user_id: str, pin: str | None) -> None:
        """Set or clear a user's PIN: one's own, or anyone's with ``users.manage``."""
        if user_id != actor.user_id and "users.manage" not in actor.permissions:
            raise NotFound("user not found")
        pin_hash = await asyncio.to_thread(hash_secret, validate_pin(pin)) if pin else None
        async with self._db.session() as s:
            user, profile, _ = await self._load(s, user_id)
            await self.guard_identity(s, actor, user)
            profile.pin_hash = pin_hash
            # A PIN that just changed must be proven again before the adult category reopens.
            await s.execute(update(AuthSession).where(AuthSession.user_id == user.id).values(adult_unlocked_until=None))
            audit(s, actor.user_id, "user.pin_set" if pin_hash else "user.pin_clear", user.id)
            await s.commit()

    # ------------------------------------------------------------------ roles
    async def roles(self) -> builtins.list[dict[str, Any]]:
        async with self._db.session() as s:
            counts = dict(
                (await s.execute(select(User.role_id, func.count(User.id)).group_by(User.role_id))).tuples().all()
            )
            rows = (await s.execute(select(Role).order_by(Role.builtin.desc(), Role.name))).scalars()
            order = {key: n for n, key in enumerate(BUILTIN_ROLES)}
            return sorted(
                (self.role_out(r, counts.get(r.id, 0)) for r in rows),
                key=lambda r: (order.get(r["id"], len(order)), r["name"].lower()),
            )

    async def create_role(self, actor: Any, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()[:64]
        if not name:
            raise ServiceError("name is required", code="invalid_name")
        permissions = clean_permissions(data.get("permissions") or [])
        limits = clean_limits(data.get("limits") or {})
        async with self._db.session() as s:
            if not await self._actor_is_owner(s, actor) and set(permissions) & OWNER_GRANTED:
                raise Forbidden("Only an owner can grant management access", code="owner_required")
            base = _slug(name)
            role_id, n = base, 1
            while await s.get(Role, role_id) is not None:
                n += 1
                role_id = f"{base}-{n}"
            role = Role(
                id=role_id,
                name=name,
                builtin=False,
                permissions=permissions,
                limits=limits,
                on_shared_devices=bool(data.get("on_shared_devices", True)),
            )
            s.add(role)
            audit(s, actor.user_id, "role.create", None, role_id=role.id, permissions=permissions, limits=limits)
            await s.commit()
        return self.role_out(role)

    async def update_role(self, actor: Any, role_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._db.session() as s:
            role = await self._role(s, role_id)
            if role.id == OWNER_ROLE:
                raise Conflict("The Owner role is fixed", code="role_fixed")
            me = await s.get(User, actor.user_id) if actor.user_id else None
            if me is not None and me.role_id == role.id:
                raise Forbidden("You cannot change your own role, permissions or limits", code="self_edit")
            before = {
                "name": role.name,
                "permissions": sorted(role.permissions or []),
                "limits": dict(role.limits or {}),
            }
            if patch.get("name") is not None:
                name = str(patch["name"]).strip()[:64]
                if not name:
                    raise ServiceError("name is required", code="invalid_name")
                role.name = name
            if "permissions" in patch:
                permissions = clean_permissions(patch["permissions"] or [])
                gained = set(permissions) - set(role.permissions or [])
                if gained & OWNER_GRANTED and not await self._actor_is_owner(s, actor):
                    raise Forbidden("Only an owner can grant management access", code="owner_required")
                role.permissions = permissions
            if "limits" in patch:
                role.limits = clean_limits(patch["limits"] or {})
            if patch.get("on_shared_devices") is not None:
                role.on_shared_devices = bool(patch["on_shared_devices"])
            role.updated_at = utcnow()
            # Everyone holding the role gets the change on their next request.
            members = await self._rows(s, User.role_id == role.id)
            for user, profile, _ in members:
                user.version += 1
                if profile is not None:
                    mirror(profile, user, role)
            after = {
                "name": role.name,
                "permissions": sorted(role.permissions or []),
                "limits": dict(role.limits or {}),
            }
            audit(s, actor.user_id, "role.update", None, role_id=role.id, before=before, after=after)
            await s.commit()
            return self.role_out(role, len(members))

    async def delete_role(self, actor: Any, role_id: str) -> None:
        async with self._db.session() as s:
            role = await self._role(s, role_id)
            if role.builtin:
                raise Conflict("Built-in roles cannot be removed", code="role_builtin")
            in_use = (await s.execute(select(func.count(User.id)).where(User.role_id == role.id))).scalar_one()
            if in_use:
                raise Conflict("Move this role's users to another role first", code="role_in_use")
            audit(s, actor.user_id, "role.delete", None, role_id=role.id, name=role.name)
            await s.delete(role)
            await s.commit()

    # ------------------------------------------------------------------ Google and Plex sign-in
    async def google_sign_in(
        self,
        claims: dict[str, Any],
        *,
        bootstrap_email: str,
        owner_profile_id: str,
        signup_mode: str,
        invite_token: str,
        redeem: Callable[[AsyncSession, str], Awaitable[Any]],
    ) -> GoogleSignIn:
        """Map a verified Google identity onto a user, creating one where the rules allow."""
        email = str(claims.get("email") or "").strip().lower()
        who = ExternalIdentity(
            provider="google",
            subject=str(claims["sub"]),
            email=email,
            name=str(claims.get("name") or "").strip(),
            picture=str(claims.get("picture") or "")[:2048] or None,
        )
        return await self.external_sign_in(
            who,
            bootstrap_email=bootstrap_email,
            owner_profile_id=owner_profile_id,
            signup_mode=signup_mode,
            invite_token=invite_token,
            redeem=redeem,
        )

    @staticmethod
    def _carry(user: User, who: ExternalIdentity) -> None:
        """Keep what the provider says about a known user. The address and picture shown for a
        user are Google's when they have Google, so Plex only fills them in for someone who has not."""
        if who.provider == "google" or not user.google_subject:
            user.email, user.picture = who.email or user.email, who.picture or user.picture
        if who.provider == "plex":
            user.plex_username = who.handle or user.plex_username

    async def external_sign_in(
        self,
        who: ExternalIdentity,
        *,
        signup_mode: str,
        invite_token: str,
        redeem: Callable[[AsyncSession, str], Awaitable[Any]],
        bootstrap_email: str = "",
        owner_profile_id: str = "",
        trusted_role_id: str | None = None,
    ) -> GoogleSignIn:
        """Map an identity a provider vouches for onto a user, creating one where the rules allow.

        Known subject: that user. Otherwise, in order: the bootstrap owner address claims
        the Owner user that has no Google identity yet (Google only); a valid invite activates
        at once with the invite's role; ``trusted_role_id`` (someone the owner's Plex server is
        already shared with) activates at once with that role; approval mode leaves a pending
        user; anything else is refused.

        Identity is the provider's subject and nothing else. An address that matches another
        user's is never a reason to treat two identities as one person: that is linking, and
        linking is something a signed-in user does on purpose.
        """
        subject, email, picture = who.subject, who.email, who.picture
        column = _SUBJECT[who.provider]
        name = (who.name or who.handle or email.split("@", 1)[0] or "User")[:64]
        pending_notice = False
        async with self._db.session() as s:
            user = (await s.execute(select(User).where(column == subject))).scalar_one_or_none()
            created = False
            if user is not None:
                if user.status == "suspended":
                    raise Forbidden("This account is suspended", code="user_suspended")
                profile = (await s.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one()
                self._carry(user, who)
                invited = await redeem(s, invite_token) if user.status == "pending" and invite_token else None
                if invited is not None:
                    role = invited.role
                    user.status, user.role_id = "active", role.id
                    user.approved_at, user.approved_by_user_id = utcnow(), invited.inviter
                    profile.kids = role.id == "kid"
                    self._changed(user, profile, role)
                    audit(s, user.id, "user.invite_accept", user.id, invite_id=invited.invite_id, role_id=role.id)
            else:
                owner = (
                    await self._bootstrap_owner(s, email, bootstrap_email, owner_profile_id)
                    if who.provider == "google"
                    else None
                )
                if owner is not None:
                    user, profile = owner
                    # Compare-and-set: of two sign-ins racing for the claim, exactly one gets it.
                    claimed = await s.execute(
                        update(User)
                        .where(User.id == user.id, User.google_subject.is_(None))
                        .values(kind="google", google_subject=subject, email=email, picture=picture)
                    )
                    if not claimed.rowcount:  # type: ignore[attr-defined]
                        raise Forbidden("This Google account is not authorized")
                    await s.refresh(user)
                    audit(s, user.id, "user.google_link", user.id)
                else:
                    invited = await redeem(s, invite_token) if invite_token else None
                    trusted = await s.get(Role, trusted_role_id) if invited is None and trusted_role_id else None
                    # Never the Owner role, whatever a caller names.
                    trusted = trusted if trusted is not None and trusted.id != OWNER_ROLE else None
                    if invited is None and trusted is None and signup_mode != "approval":
                        raise Forbidden("An invitation is required to sign in", code="invite_required")
                    granted = invited.role if invited else trusted
                    if invited and invited.display_name:
                        name = invited.display_name
                    user = User(
                        id=secrets.token_hex(8),
                        kind=who.provider,
                        email=email or None,
                        display_name=name,
                        picture=picture,
                        status="active" if granted else "pending",
                        role_id=granted.id if granted else None,
                        grants=[],
                        denials=[],
                        limits={},
                        approved_at=utcnow() if granted else None,
                        approved_by_user_id=invited.inviter if invited else None,
                    )
                    setattr(user, column.key, subject)
                    if who.provider == "plex":
                        user.plex_username = who.handle
                    s.add(user)
                    await s.flush()
                    profile = Profile(
                        id=secrets.token_hex(8),
                        name=name,
                        is_admin=False,
                        theme_id="midnight",
                        languages=[],
                        kids=bool(granted and granted.id == "kid"),
                        prefs={},
                        user_id=user.id,
                    )
                    mirror(profile, user, granted)
                    s.add(profile)
                    created = True
                    if invited:
                        audit(
                            s,
                            user.id,
                            "user.invite_accept",
                            user.id,
                            invite_id=invited.invite_id,
                            role_id=invited.role.id,
                        )
                    elif trusted is not None:
                        audit(s, user.id, "user.plex_friend_accept", user.id, role_id=trusted.id)
                    else:
                        audit(s, user.id, "user.signup", user.id, display_name=name, email=email)
                        await self._notify_managers(s, user)
                        pending_notice = True
            await s.commit()
        if pending_notice:
            await self._webhook("user.pending", {"id": user.id, "display_name": user.display_name, "email": user.email})
        return GoogleSignIn(user, profile, created)

    # ------------------------------------------------------------------ linked sign-ins
    async def link_identity(self, user_id: str, who: ExternalIdentity) -> None:
        """Attach a Google or Plex identity to a user who asked for it while signed in.

        An identity names one user: one that already signs someone else in is refused, and so
        is a second identity of the same provider (unlink the first).
        """
        column = _SUBJECT[who.provider]
        async with self._db.session() as s:
            user = await s.get(User, user_id)
            if user is None or user.status != "active":
                raise NotFound("user not found")
            holder = (await s.execute(select(User.id).where(column == who.subject))).scalar_one_or_none()
            if holder is not None and holder != user.id:
                raise Conflict("That account already signs in as someone else here", code="identity_in_use")
            mine = getattr(user, column.key)
            if mine == who.subject:
                return
            if mine:
                raise Conflict("Unlink the one that is linked first", code="already_linked")
            setattr(user, column.key, who.subject)
            self._carry(user, who)
            audit(s, user.id, "user.signin_link", user.id, method=who.provider)
            try:
                await s.commit()
            except IntegrityError as exc:
                # Two users linking one identity at the same moment; the index decided.
                raise Conflict("That account already signs in as someone else here", code="identity_in_use") from exc

    async def unlink_method(self, actor: Any, user_id: str, method: str) -> dict[str, Any]:
        """Remove one way of signing in: one's own, or anyone's with ``users.manage``.

        Never the last one, for anybody: a person with no way in is suspended or removed,
        which says so. Taken away by someone else, it also ends that person's sessions.
        """
        if user_id != actor.user_id and "users.manage" not in actor.permissions:
            raise PermissionDenied(["users.manage"])
        if user_id == actor.user_id and getattr(actor, "shared_device", False):
            raise Forbidden("Sign-in methods cannot be changed on a shared device", code="shared_device")
        async with self._db.session() as s:
            user, _, _ = await self._load(s, user_id)
            await self.guard_identity(s, actor, user)
            held = sign_in_methods(user)
            if method not in held:
                raise NotFound("That sign-in method is not linked")
            if len(held) == 1:
                raise Conflict("The last way of signing in cannot be removed", code="last_method")
            if method == "google":
                user.google_subject = None
            elif method == "plex":
                user.plex_account_id = user.plex_username = None
            else:
                user.username = user.password_hash = None
            if user.id != actor.user_id and self._sessions is not None:
                await self._sessions.revoke_all(s, user.id)
            audit(s, actor.user_id, "user.signin_unlink", user.id, method=method)
            await s.commit()
        return await self.get(user_id)

    @staticmethod
    async def _bootstrap_owner(
        s: AsyncSession, email: str, bootstrap_email: str, owner_profile_id: str
    ) -> tuple[User, Profile] | None:
        """The Owner user the configured address may claim: only while no owner has a Google identity."""
        if not email or not bootstrap_email or email != bootstrap_email:
            return None
        rows = (
            await s.execute(
                select(User, Profile)
                .join(Profile, Profile.user_id == User.id)
                .where(User.role_id == OWNER_ROLE)
                .order_by(User.created_at, User.id)
            )
        ).all()
        if any(u.google_subject for u, _ in rows):
            return None
        open_owners = [(u, p) for u, p in rows if u.status == "active"]
        if owner_profile_id:
            open_owners = [(u, p) for u, p in open_owners if p.id == owner_profile_id]
        return open_owners[0] if open_owners else None

    async def _notify_managers(self, s: AsyncSession, pending: User) -> None:
        for user, profile, role in await self._rows(s, User.status == "active"):
            if profile is not None and "users.manage" in access(user, role)[0]:
                s.add(
                    new_notice(
                        profile.id,
                        "user_pending",
                        f"{pending.display_name} is waiting for approval",
                        pending.email or "",
                        ref_id=pending.id,
                    )
                )

    async def _webhook(self, event: str, user: dict[str, Any]) -> None:
        """Best effort: tell the owner's configured endpoint. A failure never blocks the sign-in."""
        url = self._setting("notify_webhook")
        if not url or self._http is None:
            return
        try:
            async with self._http(timeout=5.0) as client:
                await client.post(str(url), json={"event": event, "user": user})
        except Exception:
            log.warning("notification webhook failed", extra={"event": event})
