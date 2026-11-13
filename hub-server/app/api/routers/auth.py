"""/auth, /profiles, /disclaimer."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api import schemas as S
from app.api.deps import C, Handover, P, UsersAdmin, at_home, client_ip, limited, resolve, session_meta
from app.infra.models import Profile as ProfileRow
from app.services.auth import Principal
from app.services.context import is_https, public_base
from app.services.errors import Forbidden, NotFound, PermissionDenied, ServiceError, Unauthorized

router = APIRouter()


async def profile_out(c: C, p: ProfileRow) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "avatar": p.avatar,
        "is_admin": p.is_admin and not c.content_policy.is_restricted(p),
        "theme_id": p.theme_id,
        "languages": list(p.languages or []),
        "has_pin": bool(p.pin_hash),
        "has_password": bool(p.password_hash),
        "content_restricted": c.content_policy.is_restricted(p),
        "kids": p.kids,
        "disclaimer_accepted_version": await c.disclaimer.accepted_version(p.id),
    }


async def tokens_out(c: C, prof: ProfileRow, tokens: dict[str, str]) -> dict[str, Any]:
    return {**tokens, "profile": await profile_out(c, prof)}


# ---------------------------------------------------------------- auth
def household(request: Request, c: C) -> bool:
    """May this request use the household convenience: pick a profile, type its PIN?

    Only on the home network, and never when GOOGLE_AUTH_REQUIRED closes it altogether. From
    anywhere else people sign in with Google, Plex or a username and password, and a TV pairs.
    """
    return not c.settings.google_auth_required and at_home(request, c)


@router.post("/auth/login", response_model=S.AuthTokens, tags=["auth"], dependencies=[limited("login")])
async def login(body: S.LoginRequest, request: Request, c: C) -> dict[str, Any]:
    if c.settings.google_auth_required:
        raise Forbidden("Use Google sign-in or device pairing")
    if not at_home(request, c):
        # Before anything is looked up: the same answer for every profile, real or not.
        raise Forbidden("Profile sign-in is only available on the home network", code="home_network_only")
    prof, tokens = await c.auth.login(
        body.profile_id, body.pin, body.password, session_meta(request), address=client_ip(request)
    )
    return await tokens_out(c, prof, tokens)


@router.post("/auth/refresh", response_model=S.AuthTokens, tags=["auth"], dependencies=[limited("refresh")])
async def refresh(body: S.RefreshRequest, request: Request, c: C) -> dict[str, Any]:
    prof, tokens = await c.auth.refresh(body.refresh_token, session_meta(request))
    return await tokens_out(c, prof, tokens)


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("missing bearer token")
    return token


@router.get("/auth/me", response_model=S.Me, tags=["auth"])
async def me(request: Request, response: Response, c: C) -> dict[str, Any]:
    """Who am I and what may I do. Also the one call a user waiting for approval can make:
    once approved, the answer carries full tokens."""
    response.headers["Cache-Control"] = "private, no-store"
    principal, identity, tokens = await c.auth.me(_bearer(request), session_meta(request))
    out = c.users.me_out(
        identity,
        shared_device=bool(principal and principal.shared_device),
        adult_unlocked=bool(principal and principal.adult_unlocked),
    )
    if tokens is not None:
        out["tokens"] = await tokens_out(c, identity.profile, tokens)
    return out


@router.get("/auth/sessions", response_model=list[S.AuthSession], tags=["auth"])
async def list_sessions(c: C, p: P, user_id: str | None = None) -> list[dict[str, Any]]:
    """The caller's signed-in devices; someone else's with ``users.manage``."""
    if user_id and user_id != p.user_id and not p.can("users.manage"):
        raise PermissionDenied(["users.manage"])
    return await c.sessions.list(user_id or p.user_id, p.session_id)


@router.delete("/auth/sessions/{sid}", status_code=204, tags=["auth"])
async def revoke_session(sid: str, c: C, p: P) -> Response:
    await c.sessions.revoke(p, sid)
    return Response(status_code=204)


@router.patch("/auth/sessions/{sid}", response_model=S.AuthSession, tags=["auth"])
async def update_session(sid: str, body: S.AuthSessionPatch, c: C, p: P) -> dict[str, Any]:
    """Relabel one's own device; with ``users.manage``, mark any device shared or personal."""
    patch = body.model_dump(exclude_unset=True)
    if patch.get("shared_device") is None:
        patch.pop("shared_device", None)
    return await c.sessions.update(p, sid, patch)


@router.get("/auth/switchable", response_model=S.SwitchableUsers, tags=["auth"])
async def switchable(response: Response, c: C, p: Handover) -> dict[str, Any]:
    """ "Who's watching?": the users a shared device can be handed to."""
    response.headers["Cache-Control"] = "private, no-store"
    return {"users": await c.sessions.switchable(p)}


@router.post("/auth/switch", response_model=S.Switched, tags=["auth"])
async def switch_user(body: S.SwitchRequest, response: Response, c: C, p: Handover) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    profile, tokens, user = await c.auth.switch_user(p, body.user_id, body.pin)
    return {**await tokens_out(c, profile, tokens), "user": user}


@router.post("/auth/adult-unlock", response_model=S.AdultUnlock, tags=["auth"])
async def adult_unlock(body: S.AdultUnlockRequest, response: Response, c: C, p: P) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return {"adult_unlocked_until": await c.sessions.adult_unlock(p, body.pin)}


@router.post("/auth/adult-lock", status_code=204, tags=["auth"])
async def adult_lock(c: C, p: P) -> Response:
    await c.sessions.adult_lock(p)
    return Response(status_code=204)


@router.post("/auth/device", response_model=S.DeviceCode, tags=["auth"], dependencies=[limited("device")])
async def device_start(request: Request, c: C) -> dict[str, Any]:
    return await c.auth.start_device(session_meta(request, "tv"))


@router.post(
    "/auth/device/lookup", response_model=S.DeviceLookup, tags=["auth"], dependencies=[limited("device_lookup")]
)
async def device_lookup(body: S.DeviceLookupRequest, response: Response, c: C, p: P) -> dict[str, Any]:
    """What is asking to pair under this code, shown before it is approved."""
    response.headers["Cache-Control"] = "private, no-store"
    return await c.auth.describe_device(body.user_code)


@router.post(
    "/auth/device/poll",
    response_model=S.AuthTokens,
    responses={428: {"model": S.Error, "description": "pending"}},
    tags=["auth"],
    dependencies=[limited("device_poll")],
)
async def device_poll(body: S.DevicePoll, request: Request, c: C) -> dict[str, Any]:
    prof, tokens = await c.auth.poll_device(body.device_code, session_meta(request, "tv"))
    return await tokens_out(c, prof, tokens)


@router.post("/auth/device/approve", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def device_approve(body: S.DeviceApprove, c: C, p: P) -> Response:
    await c.auth.approve_device(p, body.user_code, body.profile_id, body.shared_device)
    return Response(status_code=204)


# ---------------------------------------------------------------- profiles
@router.get("/profiles", response_model=list[S.Profile], tags=["profiles"], dependencies=[limited("profiles")])
async def list_profiles(request: Request, c: C) -> list[dict[str, Any]]:
    """The household's profiles, for the picker on the home network. Anywhere else, and under
    GOOGLE_AUTH_REQUIRED, a signed-in caller gets their own profile and nobody else gets a list."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    signed_in = scheme.lower() == "bearer" and bool(token)
    if household(request, c):
        if signed_in:
            await resolve(request, c, token)
        return [await profile_out(c, p) for p in await c.auth.list_profiles()]
    if not signed_in:
        if c.settings.google_auth_required:
            raise Unauthorized("Sign in to list profiles")
        # Not a refusal that says there is something here to be refused.
        raise NotFound("no such endpoint")
    # One person, one user: there is nobody else's profile to pick from here.
    principal = await resolve(request, c, token)
    return [await profile_out(c, await c.auth.get_profile(principal.profile_id))]


@router.post("/profiles", response_model=S.Profile, status_code=201, tags=["profiles"])
async def create_profile(body: S.ProfileWrite, c: C, p: P) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if data.get("theme_id") and not await c.themes.exists(data["theme_id"]):
        raise ServiceError("unknown theme")
    data["languages"] = [c.language.normalise(x) or x for x in data.get("languages") or []]
    prof = await c.auth.create_profile(p, data)
    return await profile_out(c, prof)


@router.patch("/profiles/{id}", response_model=S.Profile, tags=["profiles"])
async def update_profile(id: str, body: S.ProfileWrite, c: C, p: P) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if data.get("theme_id") and not await c.themes.exists(data["theme_id"]):
        raise ServiceError("unknown theme")
    if data.get("languages") is not None:
        data["languages"] = [c.language.normalise(x) or x for x in data["languages"]]
    prof = await c.auth.update_profile(p, id, data)
    return await profile_out(c, prof)


@router.delete("/profiles/{id}", status_code=204, tags=["profiles"])
async def delete_profile(id: str, c: C, p: P) -> Response:
    await c.auth.delete_profile(p, id)
    return Response(status_code=204)


@router.put("/profiles/{id}/pin", status_code=204, tags=["profiles"])
async def set_pin(id: str, body: S.PinBody, c: C, p: P) -> Response:
    await c.auth.set_pin(p, id, body.pin, body.current_pin, body.current_password)
    return Response(status_code=204)


def _own(p: P, id: str) -> None:
    if id != p.profile_id and not p.can("users.manage"):
        raise NotFound("profile not found")


@router.get("/profiles/{id}/home_layout", response_model=S.HomeLayout, tags=["profiles"])
async def get_home_layout(id: str, c: C, p: P) -> dict[str, Any]:
    _own(p, id)
    sources = [f"source:{s.id}" for s in await c.sources.list() if s.type != "streaming_service"]
    return {"rows": await c.layout.get(id, sources)}


@router.put("/profiles/{id}/home_layout", response_model=S.HomeLayout, tags=["profiles"])
async def put_home_layout(id: str, body: S.HomeLayout, c: C, p: P) -> dict[str, Any]:
    _own(p, id)
    await c.layout.put(id, [r.model_dump() for r in body.rows])
    sources = [f"source:{s.id}" for s in await c.sources.list() if s.type != "streaming_service"]
    return {"rows": await c.layout.get(id, sources)}


@router.get("/profiles/{id}/prefs", tags=["profiles"])
async def get_prefs(id: str, c: C, p: P) -> dict[str, Any]:
    _own(p, id)
    return await c.client_prefs.get(id)


@router.put("/profiles/{id}/prefs", tags=["profiles"])
async def put_prefs(id: str, body: dict[str, Any], c: C, p: P) -> dict[str, Any]:
    _own(p, id)
    return await c.client_prefs.put(id, body)


@router.post("/auth/profile-switch", response_model=S.AuthTokens, tags=["auth"])
async def profile_switch(body: S.ProfileSwitch, c: C, p: P) -> dict[str, Any]:
    profile, tokens = await c.auth.switch_profile(p, body.profile_id, body.pin, body.password)
    return await tokens_out(c, profile, tokens)


@router.get("/profiles/{id}/content-policy", tags=["profiles"])
async def get_content_policy(id: str, c: C, p: UsersAdmin) -> dict[str, Any]:
    profile = await c.content_policy.profile(id)
    return {"profile_id": id, "feature_enabled": c.content_policy.enabled, **c.content_policy.values(profile)}


@router.put("/profiles/{id}/content-policy", tags=["profiles"])
async def put_content_policy(
    id: str, body: S.ContentPolicyWrite, request: Request, c: C, p: UsersAdmin
) -> dict[str, Any]:
    authorization = await c.auth.validate_owner_action(request.headers.get("X-Owner-Action", ""), p, id)
    profile = await c.content_policy.update(id, body.model_dump(), authorization=authorization)
    return {"profile_id": id, "feature_enabled": c.content_policy.enabled, **c.content_policy.values(profile)}


# ---------------------------------------------------------------- disclaimer
@router.get("/disclaimer", response_model=S.Disclaimer, tags=["legal"])
async def get_disclaimer(c: C) -> dict[str, str]:
    return {"version": c.disclaimer.version, "text": c.disclaimer.text}


@router.post("/disclaimer/accept", status_code=204, tags=["legal"])
async def accept_disclaimer(body: S.DisclaimerAccept, c: C, p: P) -> Response:
    await c.disclaimer.accept(p.profile_id, body.version)
    return Response(status_code=204)


# Google sign-in deliberately does not change native JWT/device-code routes.
@router.get("/auth/config", tags=["auth"])
async def auth_config(request: Request, c: C) -> dict[str, Any]:
    """What a sign-in screen has to know before anyone is signed in: which ways in are on, and
    nothing about who exists."""
    return {
        "google_enabled": c.google_auth.enabled,
        "google_required": c.settings.google_auth_required,
        "plex_enabled": c.plex_auth.enabled,
        "password_enabled": c.passwords.enabled,
        # The profile picker: for this very request, which is to say on the home network.
        "profiles_enabled": household(request, c),
        "signup_mode": c.app_settings.get("signup_mode"),
        "content_policy_enabled": c.content_policy.enabled,
        "owner_pin_configured": await c.owner_pin.configured(),
        "owner_pin_setup_required": not await c.owner_pin.configured(),
    }


@router.get("/auth/google/start", tags=["auth"], dependencies=[limited("oauth")])
async def google_start(c: C, return_to: str = "/", invite: str = Query("", max_length=64)) -> Response:
    url, browser = c.google_auth.start(return_to, invite)
    response = RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        "__Host-sh-google-state", browser, max_age=600, secure=True, httponly=True, samesite="lax", path="/"
    )
    return response


@router.get("/auth/google/callback", tags=["auth"], dependencies=[limited("oauth")])
async def google_callback(request: Request, c: C, state: str = "", code: str = "") -> Response:
    if c.google_auth.is_owner_reauth(state):
        try:
            await c.owner_reauth.finish(state, request.cookies.get("__Host-sh-google-state", ""), code)
        except ServiceError:
            pass  # Polling initiator receives the bound denial, never an owner JWT.
        response = RedirectResponse("/api/v1/auth/owner-reauth/confirmed", status_code=303)
        response.delete_cookie("__Host-sh-google-state", secure=True, httponly=True, samesite="lax", path="/")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    if c.google_auth.is_link(state):
        try:
            await c.google_auth.finish_link(state, request.cookies.get("__Host-sh-google-state", ""), code)
            outcome = "linked=google"
        except ServiceError as exc:
            outcome = "link_error=" + (exc.code if exc.code in ("identity_in_use", "already_linked") else "failed")
        response = RedirectResponse(f"/settings?{outcome}", status_code=303)
        response.delete_cookie("__Host-sh-google-state", secure=True, httponly=True, samesite="lax", path="/")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    # Always redirect errors to a fixed page; no provider errors or credentials in the URL.
    try:
        handoff, destination = await c.google_auth.finish(
            state, request.cookies.get("__Host-sh-google-state", ""), code
        )
    except ServiceError as exc:
        # Two refusals are worth telling apart on the sign-in page; neither reveals anything.
        reason = exc.code if exc.code in ("invite_required", "user_suspended") else "1"
        response = RedirectResponse(f"/?google_error={reason}", status_code=303)
    else:
        separator = "&" if "?" in destination else "?"
        response = RedirectResponse(destination + separator + "google_complete=1", status_code=303)
        response.set_cookie(
            "__Host-sh-google-handoff", handoff, max_age=60, secure=True, httponly=True, samesite="strict", path="/"
        )
    response.delete_cookie("__Host-sh-google-state", secure=True, httponly=True, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/auth/google/complete", tags=["auth"], dependencies=[limited("oauth")])
async def google_complete(request: Request, response: Response, c: C) -> dict[str, Any]:
    prof = await c.google_auth.consume(
        request.cookies.get("__Host-sh-google-handoff", ""), request.headers.get("origin", "")
    )
    response.delete_cookie("__Host-sh-google-handoff", secure=True, httponly=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return await _signed_in(request, c, prof, google=True)


async def _signed_in(request: Request, c: C, prof: ProfileRow, *, google: bool = False) -> dict[str, Any]:
    """What a finished Google or Plex sign-in answers: tokens, or the token a waiting user polls with."""
    identity = await c.users.identity(prof.id)
    if identity is None:
        raise Unauthorized("Sign-in completion expired; please start again")
    if identity.user.status == "pending":
        # Not signed in yet: this token opens GET /auth/me and nothing else.
        return {
            "status": "pending",
            "pending_token": await c.auth.issue_pending(prof, session_meta(request, "browser")),
            "user": {"display_name": identity.user.display_name, "email": identity.user.email},
        }
    if c.content_policy.enabled:
        # Legacy regime: Google proves the owner, the owner PIN completes the sign-in.
        if not google or "system.manage" not in identity.permissions:
            raise Forbidden("Only the owner signs in with Google here", code="owner_pin_required")
        return {
            "owner_pin_required": True,
            "pending_token": c.auth.owner_pending(prof),
            "profile": await profile_out(c, prof),
            "setup_required": not await c.owner_pin.configured(),
        }
    tokens = await c.auth.issue(prof, meta=session_meta(request, "browser"))
    return {"status": "active", **await tokens_out(c, prof, tokens)}


# ---------------------------------------------------------------- Plex sign-in
def _plex_cookie() -> tuple[str, bool]:
    """The cookie that ties a Plex sign-in to the browser that started it. ``__Host-`` needs
    HTTPS; on a plain-HTTP home network the same cookie goes without the prefix."""
    secure = is_https.get()
    return ("__Host-sh-plex-state" if secure else "sh-plex-state"), secure


def require_same_origin(request: Request, c: C) -> None:
    """A cookie-bound completion is only ever asked for by our own page."""
    origin = request.headers.get("origin")
    parts = urlsplit(public_base.get())
    if (
        origin is not None
        and origin != f"{parts.scheme}://{parts.netloc}"
        and origin not in c.settings.cors_origin_list
    ):
        raise Forbidden("Same-origin sign-in completion required")


@router.post("/auth/plex/start", response_model=S.PlexSignInUrl, tags=["auth"], dependencies=[limited("plex")])
async def plex_start(body: S.PlexSignInStart, request: Request, response: Response, c: C) -> dict[str, Any]:
    """Begin "Continue with Plex": answers Plex's sign-in page for the browser to open."""
    require_same_origin(request, c)
    linking = await _linker(request, c) if body.link else None
    url, browser = await c.plex_auth.start(
        body.return_to, "" if linking else body.invite, link_user_id=linking.user_id if linking else ""
    )
    name, secure = _plex_cookie()
    response.set_cookie(name, browser, max_age=600, secure=secure, httponly=True, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"auth_url": url}


@router.post("/auth/plex/complete", tags=["auth"], dependencies=[limited("plex")])
async def plex_complete(body: S.PlexSignInComplete, request: Request, response: Response, c: C) -> dict[str, Any]:
    """Finish it, for the browser Plex sent back. Answers as ``/auth/google/complete`` does."""
    require_same_origin(request, c)
    name, secure = _plex_cookie()
    response.delete_cookie(name, secure=secure, httponly=True, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    token = _bearer(request) if request.headers.get("authorization") else ""
    tx = c.plex_auth.claim(body.state, request.cookies.get(name, ""))
    if tx.link_user_id:
        # Started by a signed-in user to link Plex to themselves: it signs nobody in.
        await c.plex_auth.link(tx, (await resolve(request, c, token)).user_id if token else "")
        return {"status": "linked"}
    account = await c.plex_auth.account(tx)
    return await _signed_in(request, c, await c.plex_auth.sign_in(account, tx.invite))


# ---------------------------------------------------------------- linked sign-ins
async def _linker(request: Request, c: C) -> Principal:
    """The signed-in user asking to change how they sign in. Never from a shared device:
    whoever holds the household's remote must not gain a way in as that user from anywhere."""
    principal = await resolve(request, c, _bearer(request))
    if principal.shared_device:
        raise Forbidden("Sign-in methods cannot be changed on a shared device", code="shared_device")
    return principal


@router.post("/auth/google/link", response_model=S.PlexSignInUrl, tags=["auth"])
async def google_link(request: Request, response: Response, c: C) -> dict[str, Any]:
    """Begin linking a Google account to one's own user. Google sends the browser back to
    ``/auth/google/callback``, which lands on ``/settings?linked=google`` or ``?link_error=<code>``."""
    require_same_origin(request, c)
    principal = await _linker(request, c)
    url, browser = c.google_auth.start_link(principal.user_id)
    response.set_cookie(
        "__Host-sh-google-state", browser, max_age=600, secure=True, httponly=True, samesite="lax", path="/"
    )
    response.headers["Cache-Control"] = "no-store"
    return {"auth_url": url}


@router.delete("/auth/methods/{method}", status_code=204, tags=["auth"])
async def unlink_own_method(method: S.SignInMethodL, c: C, p: P) -> Response:
    """Remove one of one's own ways of signing in. Never the last one (409 last_method)."""
    await c.users.unlink_method(p, p.user_id, method)
    return Response(status_code=204)


# ---------------------------------------------------------------- username and password
@router.post("/auth/password", response_model=S.AuthTokens, tags=["auth"], dependencies=[limited("password")])
async def password_sign_in(body: S.PasswordSignIn, request: Request, response: Response, c: C) -> dict[str, Any]:
    """Sign in with a username and a password. A wrong name and a wrong password are one answer."""
    response.headers["Cache-Control"] = "no-store"
    prof, tokens = await c.passwords.sign_in(body.username, body.password, client_ip(request), session_meta(request))
    return await tokens_out(c, prof, tokens)


@router.put("/auth/password", status_code=204, tags=["auth"])
async def password_change(body: S.PasswordChange, request: Request, c: C, p: P) -> Response:
    """Change one's own password (the current one is needed; other devices are signed out), or set a first one."""
    await c.passwords.change(p, body.current_password, body.new_password, body.username, client_ip(request))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/auth/invite/lookup", response_model=S.InviteLookup, tags=["auth"], dependencies=[limited("invite")])
async def invite_lookup(body: S.InviteLookupRequest, response: Response, c: C) -> dict[str, Any]:
    """What the page behind an invite or setup link shows. The token travels in the body, not the URL."""
    response.headers["Cache-Control"] = "no-store"
    return await c.passwords.lookup(body.token)


@router.post("/auth/invite/accept", response_model=S.AuthTokens, tags=["auth"], dependencies=[limited("invite")])
async def invite_accept(body: S.InviteAccept, request: Request, response: Response, c: C) -> dict[str, Any]:
    """Accept an invite, or use a setup link, by choosing a username and a password."""
    response.headers["Cache-Control"] = "no-store"
    prof, tokens = await c.passwords.accept(body.token, body.username, body.password, session_meta(request))
    return await tokens_out(c, prof, tokens)
