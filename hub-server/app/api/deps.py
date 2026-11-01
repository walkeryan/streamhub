"""FastAPI dependencies: container access, current principal."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.container import Container
from app.services.auth import Principal
from app.services.errors import Forbidden, Unauthorized

bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


C = Annotated[Container, Depends(get_container)]


async def current_principal(
    request: Request, c: C, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> AsyncIterator[Principal]:
    if creds is None or not creds.credentials:
        raise Unauthorized("missing bearer token")
    principal = await c.auth.resolve_principal(creds.credentials)
    request.state.principal = principal
    from app.services.content_policy import active_policy, active_principal

    profile = await c.content_policy.profile(principal.profile_id) if c.content_policy.enabled else None
    principal_token = active_principal.set(principal)
    token = active_policy.set((c.content_policy, profile) if profile is not None else None)
    try:
        yield principal
    finally:
        active_policy.reset(token)
        active_principal.reset(principal_token)


P = Annotated[Principal, Depends(current_principal)]


def admin_principal(p: P) -> Principal:
    if not p.is_admin:
        raise Forbidden("admin only")
    return p


Admin = Annotated[Principal, Depends(admin_principal)]
