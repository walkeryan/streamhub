"""/auth, /profiles, /disclaimer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api import schemas as S
from app.api.deps import Admin, C, P
from app.infra.models import Profile as ProfileRow
from app.services.errors import Forbidden, NotFound, ServiceError, Unauthorized

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
@router.post("/auth/login", response_model=S.AuthTokens, tags=["auth"])
async def login(body: S.LoginRequest, c: C) -> dict[str, Any]:
    if c.settings.google_auth_required:
        raise Forbidden("Use Google sign-in or device pairing")
    prof, tokens = await c.auth.login(body.profile_id, body.pin, body.password)
    return await tokens_out(c, prof, tokens)


@router.post("/auth/refresh", response_model=S.AuthTokens, tags=["auth"])
async def refresh(body: S.RefreshRequest, c: C) -> dict[str, Any]:
    prof, tokens = await c.auth.refresh(body.refresh_token)
    return await tokens_out(c, prof, tokens)


@router.post("/auth/device", response_model=S.DeviceCode, tags=["auth"])
async def device_start(c: C) -> dict[str, Any]:
    return await c.auth.start_device()


@router.post(
    "/auth/device/poll",
    response_model=S.AuthTokens,
    responses={428: {"model": S.Error, "description": "pending"}},
    tags=["auth"],
)
async def device_poll(body: S.DevicePoll, c: C) -> dict[str, Any]:
    prof, tokens = await c.auth.poll_device(body.device_code)
    return await tokens_out(c, prof, tokens)


@router.post("/auth/device/approve", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def device_approve(body: S.DeviceApprove, c: C, p: P) -> Response:
    await c.auth.approve_device(p, body.user_code, body.profile_id)
    return Response(status_code=204)


# ---------------------------------------------------------------- profiles
@router.get("/profiles", response_model=list[S.Profile], tags=["profiles"])
async def list_profiles(request: Request, c: C) -> list[dict[str, Any]]:
    if c.settings.google_auth_required:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise Unauthorized("Sign in to list profiles")
        await c.auth.resolve_principal(token)
    return [await profile_out(c, p) for p in await c.auth.list_profiles()]


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
    await c.auth.set_pin(p, id, body.pin)
    return Response(status_code=204)


def _own(p: P, id: str) -> None:
    if id != p.profile_id and not p.is_admin:
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
async def get_content_policy(id: str, c: C, p: Admin) -> dict[str, Any]:
    profile = await c.content_policy.profile(id)
    return {"profile_id": id, "feature_enabled": c.content_policy.enabled, **c.content_policy.values(profile)}


@router.put("/profiles/{id}/content-policy", tags=["profiles"])
async def put_content_policy(id: str, body: S.ContentPolicyWrite, request: Request, c: C, p: Admin) -> dict[str, Any]:
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


# Google owner login deliberately does not change native JWT/device-code routes.
@router.get("/auth/config", tags=["auth"])
async def auth_config(c: C) -> dict[str, bool]:
    return {
        "google_enabled": c.google_auth.enabled,
        "google_required": c.settings.google_auth_required,
        "content_policy_enabled": c.content_policy.enabled,
        "owner_pin_configured": await c.owner_pin.configured(),
        "owner_pin_setup_required": not await c.owner_pin.configured(),
    }


@router.get("/auth/google/start", tags=["auth"])
async def google_start(c: C, return_to: str = "/") -> Response:
    url, browser = c.google_auth.start(return_to)
    response = RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        "__Host-sh-google-state", browser, max_age=600, secure=True, httponly=True, samesite="lax", path="/"
    )
    return response


@router.get("/auth/google/callback", tags=["auth"])
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
    # Always redirect errors to a fixed page; no provider errors or credentials in the URL.
    try:
        handoff, destination = await c.google_auth.finish(
            state, request.cookies.get("__Host-sh-google-state", ""), code
        )
    except ServiceError:
        response = RedirectResponse("/?google_error=1", status_code=303)
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


@router.post("/auth/google/complete", tags=["auth"])
async def google_complete(request: Request, response: Response, c: C) -> dict[str, Any]:
    prof = await c.google_auth.consume(
        request.cookies.get("__Host-sh-google-handoff", ""), request.headers.get("origin", "")
    )
    response.delete_cookie("__Host-sh-google-handoff", secure=True, httponly=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    if c.content_policy.enabled:
        return {
            "owner_pin_required": True,
            "pending_token": c.auth.owner_pending(prof),
            "profile": await profile_out(c, prof),
            "setup_required": not await c.owner_pin.configured(),
        }
    return await tokens_out(c, prof, c.auth.issue(prof))
