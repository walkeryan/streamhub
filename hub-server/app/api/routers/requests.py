"""/requests, /notices, /settings/request_manager."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Response

from app.api import schemas as S
from app.api.deps import Admin, C, P
from app.services import request_policy
from app.services.errors import ServiceError

router = APIRouter()


@router.get("/requests", response_model=S.MediaRequestList, tags=["requests"])
async def list_requests(
    c: C,
    p: P,
    scope: Literal["mine", "all"] = "mine",
    state: str | None = Query(None, max_length=24),
) -> dict[str, Any]:
    rows = await c.requests.list(p, scope=scope, state=state)
    return {"requests": await c.requests.to_api(rows), "can_approve": request_policy.can_approve(p)}


@router.post(
    "/requests",
    response_model=S.MediaRequest,
    status_code=201,
    responses={200: {"model": S.MediaRequest, "description": "an open request for this title already exists"}},
    tags=["requests"],
)
async def create_request(body: S.MediaRequestCreate, response: Response, c: C, p: P) -> dict[str, Any]:
    row, created = await c.requests.create_or_get(p, body.media_type, body.tmdb_id, body.seasons)
    if not created:
        response.status_code = 200
    return (await c.requests.to_api([row]))[0]


@router.post("/requests/{id}/approve", response_model=S.MediaRequest, tags=["requests"])
async def approve_request(id: str, c: C, p: P, body: S.RequestDecision | None = None) -> dict[str, Any]:
    row = await c.requests.decide(p, id, True, body.note if body else None)
    return (await c.requests.to_api([row]))[0]


@router.post("/requests/{id}/decline", response_model=S.MediaRequest, tags=["requests"])
async def decline_request(id: str, c: C, p: P, body: S.RequestDecision | None = None) -> dict[str, Any]:
    row = await c.requests.decide(p, id, False, body.note if body else None)
    return (await c.requests.to_api([row]))[0]


@router.get("/notices", response_model=S.NoticeList, tags=["requests"])
async def notices(c: C, p: P) -> dict[str, Any]:
    return {"notices": await c.notices.list(p.profile_id)}


@router.post("/notices/{id}/read", response_model=S.Notice, tags=["requests"])
async def read_notice(id: str, c: C, p: P) -> Any:
    return await c.notices.mark_read(p.profile_id, id)


@router.get("/settings/request_manager", response_model=S.RequestManagerSettings, tags=["system"])
async def get_request_manager(c: C, p: Admin) -> dict[str, Any]:
    return c.app_settings.request_manager_public()


@router.put("/settings/request_manager", response_model=S.RequestManagerSettings, tags=["system"])
async def put_request_manager(body: S.RequestManagerWrite, c: C, p: Admin) -> dict[str, Any]:
    try:
        return await c.app_settings.set_request_manager(body.url, body.api_key)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc


@router.post("/settings/request_manager/test", response_model=S.TestResult, tags=["system"])
async def test_request_manager(c: C, p: Admin) -> dict[str, Any]:
    ok, message = await c.requests.test_connection()
    return {"ok": ok, "message": message}
