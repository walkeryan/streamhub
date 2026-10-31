"""FastAPI dependencies: container access, current principal."""

from __future__ import annotations

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


def current_principal(c: C, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Principal:
    if creds is None or not creds.credentials:
        raise Unauthorized("missing bearer token")
    return c.auth.principal_from_access(creds.credentials)


P = Annotated[Principal, Depends(current_principal)]


def admin_principal(p: P) -> Principal:
    if not p.is_admin:
        raise Forbidden("admin only")
    return p


Admin = Annotated[Principal, Depends(admin_principal)]
