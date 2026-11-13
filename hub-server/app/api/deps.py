"""FastAPI dependencies: container access, current principal, permission checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import HTTPConnection

from app.container import Container
from app.services.auth import Principal
from app.services.errors import Forbidden, PermissionDenied, Unauthorized
from app.services.network import FORWARDING_HEADERS, client_address, from_home, vouched_for
from app.services.sessions import SessionMeta
from app.services.visibility import require_watch, reset_watch, set_watch

bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


C = Annotated[Container, Depends(get_container)]


_BROWSERS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
_SYSTEMS = (
    ("Android", "Android"),
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


def device_label(kind: str, agent: str) -> str | None:
    """A name a person recognizes their device by in a session list: "Chrome on Linux", "TV"."""
    if kind == "tv":
        return "TV"
    browser = next((name for mark, name in _BROWSERS if mark in agent), None)
    system = next((name for mark, name in _SYSTEMS if mark in agent), None)
    if kind != "browser" or not browser:
        return None
    return f"{browser} on {system}" if system else browser


def client_ip(request: HTTPConnection, settings: Any = None) -> str | None:
    """Where this request (or socket) comes from: the peer, or what a trusted proxy says it forwarded for."""
    settings = settings or request.app.state.container.settings
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    return client_address(request.client.host if request.client else None, forwarded, settings.forwarded_allow_ips)


def session_meta(request: Request, kind: str | None = None) -> SessionMeta:
    """What to record about the device asking for tokens. Informational: shown to its owner."""
    agent = request.headers.get("user-agent", "")
    kind = kind or ("browser" if agent.startswith("Mozilla/") else "api")
    return SessionMeta(
        kind=kind,
        label=device_label(kind, agent),
        user_agent=agent or None,
        ip=client_ip(request),
    )


def at_home(request: HTTPConnection, c: Container) -> bool:
    """Does this request (or socket) come from the household's own network? See ``from_home``."""
    # Repeated header lines are one list, as a proxy chain writes them.
    headers = {name: ",".join(request.headers.getlist(name)) for name in FORWARDING_HEADERS}
    return from_home(
        request.client.host if request.client else None,
        headers,
        c.settings.forwarded_allow_ips,
        str(c.app_settings.get("home_networks") or ""),
        vouched=vouched_for(request.client.port if request.client else None),
    )


def require_reachable(request: HTTPConnection, c: Container, remote: bool) -> None:
    """Without ``remote.access`` (``remote``) a user is served on the home network and nowhere else.

    Asked for a bearer token's principal and, where there is none, for the user a signed stream
    or image URL was signed for and the user a room member token was issued to.
    """
    if remote:
        return
    if not at_home(request, c):
        raise Forbidden("This account can only be used on the home network", code="remote_access_denied")


async def resolve(
    request: Request, c: Container, token: str, *, owner_entry: bool = True, handover: bool = False
) -> Principal:
    """The caller behind a bearer token: validated, and allowed to be where the request comes from.

    One validation per request, however many dependencies ask. ``GET /auth/me`` does not come
    through here, so a client refused everything else can still say who is signed in and why.
    """
    cached = getattr(request.state, "resolved", None)
    if cached is not None and cached[0] == token:
        principal: Principal = cached[1]
        return principal
    principal = await c.auth.resolve_principal(token, owner_entry=owner_entry, handover=handover)
    require_reachable(request, c, "remote.access" in principal.permissions)
    request.state.resolved = (token, principal)
    return principal


@asynccontextmanager
async def acting_as(c: Container, principal: Principal) -> AsyncIterator[None]:
    """Run a block as this caller: the content policy and the watch scope every catalog read follows."""
    from app.services.content_policy import active_policy, active_principal

    policy = c.content_policy
    if policy is None:
        raise Forbidden("Content policy service is unavailable.", code="content_policy_unavailable")
    # The engine scopes every catalog read of this request, for a caller it applies to.
    profile = await policy.profile(principal.profile_id, principal.limits) if policy.applies_to(principal) else None
    principal_token = active_principal.set(principal)
    token = active_policy.set((policy, profile) if profile is not None else None)
    watch_token = set_watch(principal.permissions)
    try:
        yield
    finally:
        reset_watch(watch_token)
        active_policy.reset(token)
        active_principal.reset(principal_token)


async def current_principal(
    request: Request, c: C, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> AsyncIterator[Principal]:
    if creds is None or not creds.credentials:
        raise Unauthorized("missing bearer token")
    principal = await resolve(request, c, creds.credentials)
    request.state.principal = principal
    async with acting_as(c, principal):
        yield principal


P = Annotated[Principal, Depends(current_principal)]


async def handover_principal(
    request: Request, c: C, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> AsyncIterator[Principal]:
    """``current_principal`` for "Who's watching?": a shared device whose user has been suspended
    may still ask who it can be handed to, and be handed over. Nothing else is open to it."""
    if creds is None or not creds.credentials:
        raise Unauthorized("missing bearer token")
    principal = await resolve(request, c, creds.credentials, handover=True)
    request.state.principal = principal
    async with acting_as(c, principal):
        yield principal


Handover = Annotated[Principal, Depends(handover_principal)]


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

# Playback entry points that name what to play: (method, path) -> where the ids are.
_PLAYBACK_REFS = {
    ("POST", "/playback/resolve"),
    ("POST", "/playback/multiview"),
    ("GET", "/playback/options"),
    ("GET", "/playback/rewind"),
}


async def playback_watch_gate(request: Request, c: C) -> None:
    """Refuse playback of anything the caller's watch.* permissions or rating limits do not cover.

    Mounted on the playback router as a whole, so the resolver itself stays unaware of
    permissions. Routes that carry no bearer (signed stream and image URLs) pass through;
    their grants are checked by the stream path.
    """
    path = request.url.path.rsplit("/api/v1", 1)[-1]
    if (request.method, path) not in _PLAYBACK_REFS:
        return
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return  # the route's own principal dependency answers 401
    principal = await resolve(request, c, token)
    # (id, source, judged by the rating policy?, the moment it is asked for)
    refs: list[tuple[str, str | None, bool, datetime | None]] = []
    if request.method == "GET":
        refs.append((request.query_params.get("id", ""), request.query_params.get("source_id"), True, None))
    else:
        try:
            body = await request.json()
        except ValueError:
            return  # request validation reports the malformed body
        if isinstance(body, dict):
            listed = body.get("panes")
            panes = listed if isinstance(listed, list) else [body]
            for pane in panes:
                if isinstance(pane, dict):
                    source: str | None = pane.get("source_id") if isinstance(pane.get("source_id"), str) else None
                    ref = str(pane["id"]) if isinstance(pane.get("id"), str) else ""
                    programme = pane.get("catchup_programme_id")
                    if isinstance(programme, str) and programme:
                        refs.append((programme, source, True, None))
                    # A channel carrying a past programme is judged by that programme, just added;
                    # a channel rewound is judged by what aired then; anything else as it is now.
                    carrier = bool(programme) and ref.startswith("ch:")
                    refs.append((ref, source, not carrier, _moment(pane.get("rewind_at"))))
    policy = c.content_policy
    # Under the legacy flag the resolver makes these checks itself, with the same arguments.
    rated = not policy.enabled and policy.applies_to(principal)
    for ref, source, judged, moment in refs:
        if ref:
            await require_watch(c.db, principal.permissions, ref, source)
            if rated and judged:
                await policy.require(principal.profile_id, ref.removeprefix("trailer:"), at=moment)


def _moment(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None  # request validation reports the malformed timestamp
    return moment if moment is not None and moment.tzinfo is not None else None
