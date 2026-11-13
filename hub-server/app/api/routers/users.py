"""/users, /roles, /invites, /audit: the people behind the profiles."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from app.api import schemas as S
from app.api.deps import C, P, UsersAdmin, require
from app.services.auth import Principal

router = APIRouter()
Inviter = Annotated[Principal, require("users.invite")]


# ---------------------------------------------------------------- users
@router.get("/users", response_model=list[S.UserListItem], tags=["users"])
async def list_users(c: C, p: UsersAdmin, status: S.UserStatusL | None = None) -> list[dict[str, Any]]:
    return await c.users.list(status)


@router.post("/users", response_model=S.User, status_code=201, tags=["users"])
async def create_user(body: S.UserCreate, c: C, p: UsersAdmin) -> dict[str, Any]:
    """A managed user: no Google identity, used on the household's shared devices."""
    return await c.users.create_managed(p, body.model_dump())


@router.get("/users/{id}", response_model=S.UserDetail, tags=["users"])
async def get_user(id: str, c: C, p: UsersAdmin) -> dict[str, Any]:
    return await c.users.get(id)


@router.patch("/users/{id}", response_model=S.UserDetail, tags=["users"])
async def update_user(id: str, body: S.UserPatch, c: C, p: UsersAdmin) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if body.limits is not None:
        patch["limits"] = body.limits.model_dump(exclude_unset=True)
    return await c.users.update(p, id, patch)


@router.post("/users/{id}/approve", response_model=S.UserDetail, tags=["users"])
async def approve_user(id: str, body: S.UserApprove, c: C, p: UsersAdmin) -> dict[str, Any]:
    return await c.users.approve(p, id, body.role_id)


@router.post("/users/{id}/deny", status_code=204, tags=["users"])
async def deny_user(id: str, c: C, p: UsersAdmin) -> Response:
    await c.users.deny(p, id)
    return Response(status_code=204)


@router.post("/users/{id}/suspend", response_model=S.UserDetail, tags=["users"])
async def suspend_user(id: str, c: C, p: UsersAdmin) -> dict[str, Any]:
    return await c.users.set_status(p, id, "suspended")


@router.post("/users/{id}/reinstate", response_model=S.UserDetail, tags=["users"])
async def reinstate_user(id: str, c: C, p: UsersAdmin) -> dict[str, Any]:
    return await c.users.set_status(p, id, "active")


@router.delete("/users/{id}", status_code=204, tags=["users"])
async def delete_user(id: str, c: C, p: UsersAdmin) -> Response:
    await c.users.delete(p, id)
    return Response(status_code=204)


@router.put("/users/{id}/pin", status_code=204, tags=["users"])
async def set_user_pin(id: str, body: S.UserPin, c: C, p: P) -> Response:
    """One's own PIN, or anyone's with ``users.manage``. Null clears it."""
    await c.users.set_pin(p, id, body.pin)
    return Response(status_code=204)


# ---------------------------------------------------------------- roles
@router.get("/roles", response_model=list[S.Role], tags=["users"])
async def list_roles(c: C, p: UsersAdmin) -> list[dict[str, Any]]:
    return await c.users.roles()


@router.post("/roles", response_model=S.Role, status_code=201, tags=["users"])
async def create_role(body: S.RoleCreate, c: C, p: UsersAdmin) -> dict[str, Any]:
    data = body.model_dump()
    data["limits"] = body.limits.model_dump(exclude_unset=True)
    return await c.users.create_role(p, data)


@router.patch("/roles/{id}", response_model=S.Role, tags=["users"])
async def update_role(id: str, body: S.RolePatch, c: C, p: UsersAdmin) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if body.limits is not None:
        patch["limits"] = body.limits.model_dump(exclude_unset=True)
    return await c.users.update_role(p, id, patch)


@router.delete("/roles/{id}", status_code=204, tags=["users"])
async def delete_role(id: str, c: C, p: UsersAdmin) -> Response:
    await c.users.delete_role(p, id)
    return Response(status_code=204)


# ---------------------------------------------------------------- invites
@router.post("/invites", response_model=S.InviteCreated, status_code=201, tags=["users"])
async def create_invite(body: S.InviteCreate, response: Response, c: C, p: Inviter) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return await c.invites.create(p, body.role_id, body.expires_in_hours, body.max_uses)


@router.get("/invites", response_model=list[S.Invite], tags=["users"])
async def list_invites(c: C, p: Inviter) -> list[dict[str, Any]]:
    return await c.invites.list(p)


@router.delete("/invites/{id}", status_code=204, tags=["users"])
async def revoke_invite(id: str, c: C, p: Inviter) -> Response:
    await c.invites.revoke(p, id)
    return Response(status_code=204)


# ---------------------------------------------------------------- audit
@router.get("/audit", response_model=list[S.AuditEntry], tags=["users"])
async def audit_log(
    c: C, p: UsersAdmin, user_id: str | None = None, limit: int = Query(100, ge=1, le=500)
) -> list[dict[str, Any]]:
    return await c.users.audit_log(user_id, limit)
