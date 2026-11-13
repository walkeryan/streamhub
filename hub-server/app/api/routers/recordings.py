"""/recordings, /recording-rules."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response

from app.api import schemas as S
from app.api.deps import C, P
from app.services import recording_policy

router = APIRouter()


@router.get("/recordings", response_model=S.RecordingList, tags=["recordings"])
async def list_recordings(c: C, p: P, state: str | None = Query(None, max_length=16)) -> dict[str, Any]:
    rows = await c.recorder.list(p, state=state)
    return {
        "enabled": c.recorder.enabled,
        "can_schedule": recording_policy.can_schedule(p),
        "recordings": await c.recorder.to_api(rows),
    }


@router.post(
    "/recordings",
    response_model=S.Recording,
    status_code=201,
    responses={200: {"model": S.Recording, "description": "this programme was already scheduled"}},
    tags=["recordings"],
)
async def create_recording(body: S.RecordingCreate, response: Response, c: C, p: P) -> dict[str, Any]:
    row, created = await c.recorder.schedule_or_get(p, body.programme_id, body.channel_id, body.start)
    if not created:
        response.status_code = 200
    return (await c.recorder.to_api([row]))[0]


@router.delete("/recordings/{id}", status_code=204, tags=["recordings"])
async def delete_recording(id: str, c: C, p: P) -> Response:
    await c.recorder.cancel(p, id)
    return Response(status_code=204)


@router.get("/recording-rules", response_model=S.RecordingRuleList, tags=["recordings"])
async def list_recording_rules(c: C, p: P) -> dict[str, Any]:
    rows = await c.recorder.list_rules(p)
    return {
        "enabled": c.recorder.enabled,
        "can_schedule": recording_policy.can_schedule(p),
        "rules": await c.recorder.rules_to_api(rows),
    }


@router.post(
    "/recording-rules",
    response_model=S.RecordingRule,
    status_code=201,
    responses={200: {"model": S.RecordingRule, "description": "the profile already has this rule"}},
    tags=["recordings"],
)
async def create_recording_rule(body: S.RecordingRuleCreate, response: Response, c: C, p: P) -> dict[str, Any]:
    row, created = await c.recorder.add_or_get_rule(p, body.kind, body.match, body.channel_id, body.new_only, body.keep)
    if not created:
        response.status_code = 200
    return (await c.recorder.rules_to_api([row]))[0]


@router.patch("/recording-rules/{id}", response_model=S.RecordingRule, tags=["recordings"])
async def patch_recording_rule(id: str, body: S.RecordingRulePatch, c: C, p: P) -> dict[str, Any]:
    row = await c.recorder.update_rule(p, id, body.model_dump(exclude_unset=True))
    return (await c.recorder.rules_to_api([row]))[0]


@router.delete("/recording-rules/{id}", status_code=204, tags=["recordings"])
async def delete_recording_rule(id: str, c: C, p: P) -> Response:
    await c.recorder.delete_rule(p, id)
    return Response(status_code=204)
