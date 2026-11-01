"""Owner PIN transport. No public enrollment-approval or recovery endpoint."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.api.deps import C, bearer
from app.api.routers.auth import tokens_out
from app.services.auth import Principal
from app.services.errors import Forbidden, Unauthorized

router = APIRouter(tags=["auth"])


async def pin_principal(c: C, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Principal:
    if creds is None:
        raise Unauthorized("Missing bearer token")
    try:
        return await c.auth.validate_owner_pending(creds.credentials)
    except Unauthorized:
        # Accept an existing session for restricted exits, or pre-activation
        # owner enrollment; never turn a pending login into general API access.
        p = c.auth.principal_from_access(creds.credentials)
        await c.auth.session_valid(p.profile_id, p.token_version, p.session_id)
        return p


PP = Annotated[Principal, Depends(pin_principal)]


class Start(BaseModel):
    profile_id: str
    action: Literal["login", "profile_switch", "admin"]


class Challenge(BaseModel):
    challenge_id: str
    poll_token: str


class Verify(Challenge):
    pin: str = Field(min_length=6, max_length=12, pattern=r"^\d+$", repr=False)


class Setup(BaseModel):
    grant_id: str
    grant_secret: str = Field(repr=False)
    pin: str = Field(min_length=6, max_length=12, pattern=r"^\d+$", repr=False)


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@router.post("/auth/owner-pin/start")
async def start(body: Start, c: C, p: PP, response: Response) -> dict[str, Any]:
    no_store(response)
    if body.action == "login" and (not p.is_admin or body.profile_id != p.profile_id):
        raise Forbidden("Owner action must target the authenticated owner", code="owner_pin_invalid")
    if body.action == "admin" and not p.is_admin:
        raise Forbidden("Owner approval required", code="owner_pin_invalid")
    if body.action == "profile_switch" and body.profile_id == p.profile_id:
        raise Forbidden("Choose another profile", code="owner_pin_invalid")
    return await c.owner_pin.start(p, body.profile_id, body.action)


@router.post("/auth/owner-pin/verify")
async def verify(body: Verify, c: C, p: PP, response: Response) -> dict[str, Any]:
    no_store(response)
    proof = await c.owner_pin.verify(p, body.challenge_id, body.poll_token, body.pin)
    target = await c.auth.get_profile(proof.target_profile_id)
    if target.token_version != proof.target_version:
        raise Forbidden("Target profile changed", code="owner_pin_invalid")
    if proof.action == "admin":
        return {
            "owner_action_token": c.auth.issue_owner_action(p, target, proof.credential_version),
            "profile_id": target.id,
            "action": "content_policy",
            "expires_in": 120,
        }
    await c.auth.consume_session(p.profile_id, p.token_version, p.session_id)
    return await tokens_out(
        c, target, c.auth.issue(target, owner_pin_version=proof.credential_version, google_subject=p.google_subject)
    )


@router.post("/auth/owner-pin/cancel", status_code=204)
async def cancel(body: Challenge, c: C, p: PP) -> Response:
    await c.owner_pin.cancel(p, body.challenge_id, body.poll_token)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/auth/owner-pin/setup-status")
async def setup_status(c: C, p: PP, response: Response) -> dict[str, Any]:
    no_store(response)
    return await c.owner_pin.status(p)


@router.post("/auth/owner-pin/setup")
async def setup(body: Setup, c: C, p: PP, response: Response) -> dict[str, Any]:
    no_store(response)
    proof = await c.owner_pin.setup(p, body.grant_id, body.grant_secret, body.pin, p.google_subject)
    target = await c.auth.get_profile(proof.target_profile_id)
    return await tokens_out(
        c, target, c.auth.issue(target, owner_pin_version=proof.credential_version, google_subject=p.google_subject)
    )
