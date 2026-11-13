"""FastAPI dependencies: container access, current principal, permission checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.container import Container
from app.services.auth import Principal
from app.services.errors import Forbidden, PermissionDenied, Unauthorized
from app.services.network import client_address, is_home
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


def client_ip(request: Request) -> str | None:
    """Where this request comes from: the peer, or what a trusted proxy says it forwarded for."""
    settings = request.app.state.container.settings
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


def require_reachable(request: Request, c: Container, principal: Principal) -> None:
    """Without ``remote.access`` a user is served on the home network and nowhere else."""
    if "remote.access" in principal.permissions:
        return
    if not is_home(client_ip(request), str(c.app_settings.get("home_networks") or "")):
        raise Forbidden("This account can only be used on the home network", code="remote_access_denied")


async def resolve(request: Request, c: Container, token: str, *, owner_entry: bool = True) -> Principal:
    """The caller behind a bearer token: validated, and allowed to be where the request comes from.

    One validation per request, however many dependencies ask. ``GET /auth/me`` does not come
    through here, so a client refused everything else can still say who is signed in and why.
    """
    cached = getattr(request.state, "resolved", None)
    if cached is not None and cached[0] == token:
        principal: Principal = cached[1]
        return principal
    principal = await c.auth.resolve_principal(token, owner_entry=owner_entry)
    require_reachable(request, c, principal)
    request.state.resolved = (token, principal)
    return principal


async def current_principal(
    request: Request, c: C, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> AsyncIterator[Principal]:
    if creds is None or not creds.credentials:
        raise Unauthorized("missing bearer token")
    principal = await resolve(request, c, creds.credentials)
    request.state.principal = principal
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
        yield principal
    finally:
        reset_watch(watch_token)
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
    refs: list[tuple[str, str | None]] = []
    # Channel ids asked for at a past moment (rewind): the policy judges what aired then.
    moments: dict[str, datetime] = {}
    # Channels asked for as the carrier of a past programme: that programme is what is judged.
    carriers: set[str] = set()
    if request.method == "GET":
        refs.append((request.query_params.get("id", ""), request.query_params.get("source_id")))
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
                    source = pane.get("source_id") if isinstance(pane.get("source_id"), str) else None
                    for key in ("id", "catchup_programme_id"):
                        if isinstance(pane.get(key), str):
                            refs.append((pane[key], source))
                    if isinstance(pane.get("id"), str):
                        moment = _moment(pane.get("rewind_at"))
                        if pane.get("catchup_programme_id"):
                            carriers.add(pane["id"])
                        elif moment is not None:
                            moments[pane["id"]] = moment
    policy = c.content_policy
    # Under the legacy flag the resolver makes these checks itself, with the same arguments.
    rated = not policy.enabled and policy.applies_to(principal)
    for ref, source in refs:
        if ref:
            await require_watch(c.db, principal.permissions, ref, source)
            if rated and ref not in carriers:
                await policy.require(principal.profile_id, ref.removeprefix("trailer:"), at=moments.get(ref))


def _moment(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None  # request validation reports the malformed timestamp
    return moment if moment is not None and moment.tzinfo is not None else None
