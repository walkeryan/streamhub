"""Invite links: a role and an expiry behind one unguessable token.

Only the SHA-256 of a token is stored; the token itself exists in the create response
and in the link the inviter passes on. Signing in through a valid invite activates
the new user at once with the invite's role.
"""

from __future__ import annotations

import builtins
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db import Database
from app.infra.models import Invite, Role, User, utcnow
from app.services.errors import Forbidden, NotFound, ServiceError
from app.services.permissions import OWNER_ROLE, effective_limits, is_wider
from app.services.users import access, audit

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
MAX_HOURS = 30 * 24
MAX_USES = 50


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Redeemed:
    """One claimed use of an invite: the role it confers, who sent it, and who it was meant for."""

    role: Role
    inviter: str | None
    invite_id: str
    display_name: str | None = None


def _may_confer(role: Role, permissions: frozenset[str], limits: Any) -> bool:
    """An invite never hands out the Owner role, nor more access than its creator holds."""
    if role.id == OWNER_ROLE:
        return False
    return not is_wider(frozenset(role.permissions or []), effective_limits(role.limits or {}, {}), permissions, limits)


class InvitesService:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def to_api(row: Invite, role: Role | None, creator: str | None = None) -> dict[str, Any]:
        now = utcnow()
        state = (
            "revoked"
            if row.revoked_at is not None
            else "expired"
            if row.expires_at <= now
            else "used"
            if row.uses >= row.max_uses
            else "open"
        )
        return {
            "id": row.id,
            "role_id": row.role_id,
            "role_name": role.name if role else None,
            "created_by_user_id": row.created_by_user_id,
            "created_by_name": creator,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
            "max_uses": row.max_uses,
            "uses": row.uses,
            "state": state,
            "display_name": row.display_name,
            "user_id": row.user_id,
        }

    async def create(
        self, actor: Any, role_id: str, expires_in_hours: int = 72, max_uses: int = 1, name: str | None = None
    ) -> dict[str, Any]:
        """An invite; with ``name`` it is for one person, who starts out under that name."""
        if not 1 <= expires_in_hours <= MAX_HOURS or not 1 <= max_uses <= MAX_USES:
            raise ServiceError("invalid expiry or use count", code="invalid_invite")
        name = (name or "").strip()[:64] or None
        if name and max_uses != 1:
            raise ServiceError("An invite for one person is used once", code="invalid_invite")
        token = secrets.token_urlsafe(32)
        async with self._db.session() as s:
            role = await s.get(Role, role_id)
            if role is None:
                raise NotFound("role not found")
            if not _may_confer(role, actor.permissions, actor.limits):
                raise Forbidden("You can only invite to a role no wider than your own", code="invite_role_too_wide")
            row = Invite(
                id=secrets.token_hex(8),
                token_hash=token_hash(token),
                role_id=role.id,
                created_by_user_id=actor.user_id or None,
                expires_at=utcnow() + timedelta(hours=expires_in_hours),
                max_uses=max_uses,
                uses=0,
                display_name=name,
            )
            s.add(row)
            audit(
                s,
                actor.user_id,
                "invite.create",
                None,
                invite_id=row.id,
                role_id=role.id,
                expires_in_hours=expires_in_hours,
                max_uses=max_uses,
                named=bool(name),
            )
            await s.commit()
        # The only time the token is ever returned.
        return {**self.to_api(row, role), "url": f"/invite/{token}"}

    async def list(self, actor: Any) -> builtins.list[dict[str, Any]]:
        """Every invite for a user manager; otherwise only the caller's own."""
        query = (
            select(Invite, Role, User.display_name)
            .outerjoin(Role, Role.id == Invite.role_id)
            .outerjoin(User, User.id == Invite.created_by_user_id)
            .order_by(Invite.created_at.desc(), Invite.id)
        )
        if "users.manage" not in actor.permissions:
            query = query.where(Invite.created_by_user_id == actor.user_id)
        async with self._db.session() as s:
            return [self.to_api(row, role, name) for row, role, name in (await s.execute(query)).all()]

    async def revoke(self, actor: Any, invite_id: str) -> None:
        async with self._db.session() as s:
            row = await s.get(Invite, invite_id)
            mine = row is not None and row.created_by_user_id == actor.user_id
            if row is None or not (mine or "users.manage" in actor.permissions):
                raise NotFound("invite not found")
            if row.revoked_at is None:
                row.revoked_at = utcnow()
                audit(s, actor.user_id, "invite.revoke", None, invite_id=row.id, role_id=row.role_id)
            await s.commit()

    @staticmethod
    async def _confers(s: AsyncSession, row: Invite) -> Role | None:
        """The role this invite still hands out: none once its creator may no longer invite to it."""
        role = await s.get(Role, row.role_id)
        creator = await s.get(User, row.created_by_user_id) if row.created_by_user_id else None
        creator_role = await s.get(Role, creator.role_id) if creator is not None and creator.role_id else None
        permissions, limits = access(creator, creator_role)
        if role is None or "users.invite" not in permissions or not _may_confer(role, permissions, limits):
            return None
        return role

    @staticmethod
    async def usable(s: AsyncSession, row: Invite) -> bool:
        """Would this invitation admit someone right now? Claims nothing."""
        if row.user_id is not None or row.revoked_at is not None or row.expires_at <= utcnow():
            return False
        return row.uses < row.max_uses and await InvitesService._confers(s, row) is not None

    @staticmethod
    async def redeem_in(s: AsyncSession, token: str) -> Redeemed | None:
        """Claim one use inside the caller's transaction, or None.

        The use count moves in a single guarded UPDATE, so two sign-ins racing for the last
        use cannot both win. An invite is only as good as its creator still is. A password
        setup link (one that names a user) is not an invitation and admits nobody here.
        """
        if not TOKEN_RE.fullmatch(token or ""):
            return None
        now = utcnow()
        row = (await s.execute(select(Invite).where(Invite.token_hash == token_hash(token)))).scalar_one_or_none()
        if row is None or row.user_id is not None:
            return None
        role = await InvitesService._confers(s, row)
        if role is None:
            return None
        claimed = (
            await s.execute(
                update(Invite)
                .where(
                    Invite.id == row.id,
                    Invite.revoked_at.is_(None),
                    Invite.expires_at > now,
                    Invite.uses < Invite.max_uses,
                )
                .values(uses=Invite.uses + 1)
                .returning(Invite.id)
            )
        ).scalar_one_or_none()
        if claimed is None:
            return None
        return Redeemed(role, row.created_by_user_id, row.id, row.display_name)
