"""/auth, /profiles, /disclaimer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from app.api import schemas as S
from app.api.deps import C, P
from app.infra.models import Profile as ProfileRow
from app.services.errors import NotFound, ServiceError

router = APIRouter()


async def profile_out(c: C, p: ProfileRow) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "avatar": p.avatar,
        "is_admin": p.is_admin,
        "theme_id": p.theme_id,
        "languages": list(p.languages or []),
        "has_pin": bool(p.pin_hash),
        "kids": p.kids,
        "disclaimer_accepted_version": await c.disclaimer.accepted_version(p.id),
    }


async def tokens_out(c: C, prof: ProfileRow, tokens: dict[str, str]) -> dict[str, Any]:
    return {**tokens, "profile": await profile_out(c, prof)}


# ---------------------------------------------------------------- auth
@router.post("/auth/login", response_model=S.AuthTokens, tags=["auth"])
async def login(body: S.LoginRequest, c: C) -> dict[str, Any]:
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
async def list_profiles(c: C) -> list[dict[str, Any]]:
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


# ---------------------------------------------------------------- disclaimer
@router.get("/disclaimer", response_model=S.Disclaimer, tags=["legal"])
async def get_disclaimer(c: C) -> dict[str, str]:
    return {"version": c.disclaimer.version, "text": c.disclaimer.text}


@router.post("/disclaimer/accept", status_code=204, tags=["legal"])
async def accept_disclaimer(body: S.DisclaimerAccept, c: C, p: P) -> Response:
    await c.disclaimer.accept(p.profile_id, body.version)
    return Response(status_code=204)
