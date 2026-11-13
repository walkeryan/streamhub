"""FastAPI dependencies: container access, current principal, permission checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.container import Container
from app.services.auth import Principal
from app.services.errors import Forbidden, PermissionDenied, Unauthorized
from app.services.sessions import SessionMeta

bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


C = Annotated[Container, Depends(get_container)]


def session_meta(request: Request, kind: str | None = None) -> SessionMeta:
    """What to record about the device asking for tokens. Informational: shown to its owner."""
    agent = request.headers.get("user-agent", "")
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return SessionMeta(
        kind=kind or ("browser" if agent.startswith("Mozilla/") else "api"),
        user_agent=agent or None,
        ip=forwarded or (request.client.host if request.client else None),
    )


async def _resolve(request: Request, c: Container, token: str) -> Principal:
    """One validation per request, however many dependencies ask."""
    cached = getattr(request.state, "resolved", None)
    if cached is not None and cached[0] == token:
        principal: Principal = cached[1]
        return principal
    principal = await c.auth.resolve_principal(token)
    request.state.resolved = (token, principal)
    return principal


async def current_principal(
    request: Request, c: C, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> AsyncIterator[Principal]:
    if creds is None or not creds.credentials:
        raise Unauthorized("missing bearer token")
    principal = await _resolve(request, c, creds.credentials)
    request.state.principal = principal
    from app.services.content_policy import active_policy, active_principal

    if c.content_policy is None:
        raise Forbidden("Content policy service is unavailable.", code="content_policy_unavailable")
    profile = await c.content_policy.profile(principal.profile_id) if c.content_policy.enabled else None
    principal_token = active_principal.set(principal)
    token = active_policy.set((c.content_policy, profile) if profile is not None else None)
    try:
        yield principal
    finally:
        active_policy.reset(token)
        active_principal.reset(principal_token)


P = Annotated[Principal, Depends(current_principal)]


def require(*permissions: str) -> Any:
    """Dependency: the caller must hold every named permission, or 403 names the missing ones."""

    def checked(p: P) -> Principal:
        missing = [name for name in permissions if name not in p.permissions]
        if missing:
            raise PermissionDenied(missing)
        return p

    return Depends(checked)


Admin = Annotated[Principal, require("system.manage")]
SourcesAdmin = Annotated[Principal, require("sources.manage")]
UsersAdmin = Annotated[Principal, require("users.manage")]
