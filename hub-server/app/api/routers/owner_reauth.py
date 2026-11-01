"""Owner reauthentication: public verification, private initiator-only token claim."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.api import schemas as S
from app.api.deps import C, P
from app.api.routers.auth import tokens_out
from app.services.errors import Forbidden

router = APIRouter()


class Start(BaseModel):
    profile_id: str = Field(min_length=1, max_length=128)


class Poll(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=128)
    poll_token: str = Field(min_length=1, max_length=128)


@router.post("/auth/owner-reauth/start", tags=["auth"])
async def start(body: Start, c: C, p: P, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    source = await c.content_policy.profile(p.profile_id)
    if not c.content_policy.enabled or not c.content_policy.is_restricted(source) or body.profile_id == p.profile_id:
        raise Forbidden("Owner verification requires a restricted source and a different target")
    return await c.owner_reauth.start(p, body.profile_id)


@router.post("/auth/owner-reauth/poll", response_model=S.AuthTokens, tags=["auth"])
async def poll(body: Poll, c: C, p: P, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    prof, tokens = await c.owner_reauth.poll(p, body.challenge_id, body.poll_token)
    return await tokens_out(c, prof, tokens)


@router.post("/auth/owner-reauth/cancel", status_code=204, tags=["auth"])
async def cancel(body: Poll, c: C, p: P) -> Response:
    await c.owner_reauth.cancel(p, body.challenge_id, body.poll_token)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/auth/owner-reauth/verify", tags=["auth"])
async def verify(c: C, challenge_id: str) -> Response:
    url, browser = c.owner_reauth.verify(challenge_id)
    response = RedirectResponse(
        url, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )
    response.set_cookie(
        "__Host-sh-google-state", browser, max_age=300, secure=True, httponly=True, samesite="lax", path="/"
    )
    return response


@router.get("/auth/owner-reauth/confirmed", tags=["auth"])
async def confirmed() -> Response:
    return HTMLResponse(
        "<!doctype html><title>Owner verification</title>"
        "<h1>Return to your original device</h1>"
        "<p>Your original device will show whether owner verification succeeded. "
        "You may close this page.</p>",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        },
    )
