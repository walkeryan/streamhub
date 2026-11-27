"""A paired TV resolves every title itself. Only its current profile can offer to control it."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import C, P
from app.api.routers.rooms import _require_rated
from app.container import Container
from app.infra.models import AuthSession, CastConnection, CastTarget, utcnow
from app.services.auth import Principal
from app.services.errors import Conflict, Forbidden, NotFound
from app.services.visibility import require_watch

router = APIRouter(prefix="/cast", tags=["cast"])
Ref = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^\s/?#\\]+$")]
Millis = Annotated[int, Field(ge=0, le=604800000)]
Label = Annotated[str, Field(max_length=128)]


class CastBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Playback(CastBody):
    id: Ref | None = None
    title: str = Field(default="", max_length=512)
    poster: str | None = Field(default=None, max_length=2048)
    playing: bool = False
    position_ms: Millis = 0
    duration_ms: Millis | None = None
    live: bool = False


class Connect(CastBody):
    target_id: UUID


class Decision(CastBody):
    session_id: UUID
    accept: bool


class Command(CastBody):
    session_id: UUID
    request_id: UUID
    action: Literal["load", "play", "pause", "seek", "stop"]
    id: Ref | None = None
    variant_id: Ref | None = None
    source_id: Ref | None = None
    position_ms: Millis | None = None
    audio_track: int | None = Field(default=None, ge=0, le=1024)
    catchup_programme_id: Ref | None = None
    rewind_at: AwareDatetime | None = None
    buffer_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid(self) -> "Command":
        if (self.action == "load" and not self.id) or (self.action == "seek" and self.position_ms is None):
            raise ValueError("A load needs a title and a seek needs a position.")
        if sum(x is not None for x in (self.catchup_programme_id, self.rewind_at, self.buffer_at)) > 1:
            raise ValueError("Choose one playback start.")
        return self


class Sync(CastBody):
    enabled: bool
    name: Label | None = None
    playback: Playback | None = None
    ack: int | None = Field(default=None, ge=1)
    error: str | None = Field(default=None, max_length=512)
    session_id: UUID | None = None


class CastDisplay(BaseModel):
    id: UUID
    name: str


class CastTargets(BaseModel):
    targets: list[CastDisplay]


class CastSession(BaseModel):
    id: UUID
    target_id: UUID
    target_name: str
    status: Literal["pending", "connected"]
    playback: Playback
    last_ack: int
    last_error: str | None


class CastState(BaseModel):
    session: CastSession | None


class CastSequence(BaseModel):
    seq: int


class CastDelivery(Command):
    seq: int


class ReceiverState(CastState):
    controller_name: str | None
    command: CastDelivery | None


@asynccontextmanager
async def transaction(c: Container) -> AsyncIterator[AsyncSession]:
    async with c.db.session() as s:
        await s.execute(text("BEGIN IMMEDIATE"))
        now = utcnow()
        await s.execute(delete(CastTarget).where(CastTarget.seen < now - timedelta(seconds=30)))
        await s.execute(
            delete(CastConnection).where(
                CastConnection.connected.is_(False), CastConnection.created < now - timedelta(seconds=90)
            )
        )
        yield s
        await s.commit()


async def device(s: AsyncSession, p: Principal, *, tv: bool = False) -> AuthSession:
    row = await s.get(AuthSession, p.session_id)
    if (
        not row
        or row.revoked_at
        or row.profile_id != p.profile_id
        or row.user_id != p.user_id
        or (tv and row.kind != "tv")
    ):
        raise Forbidden("This device cannot use the receiver.")
    return row


async def current(s: AsyncSession, target: CastTarget, p: Principal, *, clean_up: bool = True) -> CastConnection | None:
    receiver = await s.get(AuthSession, target.sid)
    if (
        not receiver
        or receiver.revoked_at
        or receiver.kind != "tv"
        or receiver.generation != target.generation
        or receiver.user_id != p.user_id
        or receiver.profile_id != p.profile_id
        or target.profile_id != p.profile_id
    ):
        raise NotFound("The TV is unavailable.")
    link = await s.scalar(select(CastConnection).where(CastConnection.target_id == target.id))
    if link:
        sender = await s.get(AuthSession, link.sid)
        if (
            not sender
            or sender.revoked_at
            or sender.generation != link.generation
            or sender.user_id != p.user_id
            or sender.profile_id != p.profile_id
        ):
            if clean_up:
                await s.delete(link)
                await s.flush()
            return None
    return link


def session_data(link: CastConnection | None, target: CastTarget) -> dict[str, Any] | None:
    if not link:
        return None
    return dict(
        id=link.id,
        target_id=target.id,
        target_name=target.name,
        status="connected" if link.connected else "pending",
        playback=link.playback,
        last_ack=link.last_ack,
        last_error=link.last_error,
    )


@router.get("/targets", response_model=CastTargets, response_model_exclude_unset=True)
async def targets(c: C, p: P) -> dict[str, Any]:
    async with c.db.session() as s:
        rows = await s.scalars(
            select(CastTarget)
            .join(AuthSession, AuthSession.sid == CastTarget.sid)
            .where(
                CastTarget.profile_id == p.profile_id,
                AuthSession.user_id == p.user_id,
                AuthSession.profile_id == p.profile_id,
                CastTarget.generation == AuthSession.generation,
                AuthSession.revoked_at.is_(None),
                AuthSession.kind == "tv",
                CastTarget.sid != p.session_id,
                CastTarget.seen >= utcnow() - timedelta(seconds=30),
            )
            .order_by(CastTarget.name)
        )
        return {"targets": [{"id": row.id, "name": row.name} for row in rows]}


@router.post("/connect", response_model=CastState, response_model_exclude_unset=True)
async def connect(body: Connect, c: C, p: P) -> dict[str, Any]:
    async with transaction(c) as s:
        sender = await device(s, p)
        target = await s.get(CastTarget, str(body.target_id))
        if not target or target.sid == sender.sid:
            raise NotFound("The TV is unavailable.")
        link = await current(s, target, p)
        if link and link.sid != sender.sid:
            raise Conflict("This TV already has a controller.")
        if not link:
            if await s.scalar(select(CastConnection.id).where(CastConnection.sid == sender.sid)):
                raise Conflict("Disconnect your current TV first.")
            link = CastConnection(
                id=str(uuid4()),
                target_id=target.id,
                sid=sender.sid,
                generation=sender.generation,
                created=utcnow(),
                connected=False,
                last_ack=0,
                playback=Playback().model_dump(),
                commands=[],
            )
            s.add(link)
        return {"session": session_data(link, target)}


@router.get("/session", response_model=CastState, response_model_exclude_unset=True)
async def get_session(c: C, p: P) -> dict[str, Any]:
    async with c.db.session() as s:
        target = await s.scalar(select(CastTarget).join(CastConnection).where(CastConnection.sid == p.session_id))
        if not target or target.profile_id != p.profile_id or target.seen < utcnow() - timedelta(seconds=30):
            return {"session": None}
        try:
            link = await current(s, target, p, clean_up=False)
            if link and not link.connected and link.created < utcnow() - timedelta(seconds=90):
                return {"session": None}
        except NotFound:
            return {"session": None}
        return {"session": session_data(link, target)}


async def allowed_load(c: Container, p: Principal, ref: str, source: str | None, variant: str | None) -> None:
    await require_watch(c.db, p.permissions, ref, source)
    if ref.startswith("rec:"):
        await c.recorder.playable(p, ref[4:])
        return
    await _require_rated(c, p, ref)
    result = await c.playback.options(p.profile_id, ref, source, probe=False)
    if not result["options"] or (variant and not any(o["variant_id"] == variant for o in result["options"])):
        raise Conflict("This source is no longer available to play on the TV.")


@router.post("/commands", response_model=CastSequence, response_model_exclude_unset=True)
async def command(body: Command, c: C, p: P) -> dict[str, int]:
    if body.action == "load" and body.id:
        await allowed_load(c, p, body.id, body.source_id, body.variant_id)
    async with transaction(c) as s:
        link = await s.get(CastConnection, str(body.session_id))
        if not link or link.sid != p.session_id or not link.connected:
            raise NotFound("The controller is disconnected.")
        target = await s.get(CastTarget, link.target_id)
        if not target or not await current(s, target, p):
            raise NotFound("The TV is unavailable.")
        payload = body.model_dump(mode="json", exclude_none=True)
        for queued in link.commands:
            if queued["request_id"] == payload["request_id"]:
                if any(queued.get(k) != v for k, v in payload.items()):
                    raise Conflict("This command has already been used.")
                return {"seq": queued["seq"]}
        seq = link.commands[-1]["seq"] + 1 if link.commands else 1
        if seq - link.last_ack > 10:
            raise Conflict("The TV is processing earlier commands.")
        if body.action != "load":
            if not link.playback.get("id") or (body.id and body.id != link.playback["id"]):
                raise Conflict("Playback has changed on the TV.")
            if body.action == "seek" and link.playback.get("live"):
                raise Conflict("This live stream cannot seek.")
            payload["id"] = link.playback["id"]
        link.commands = [*link.commands[-63:], {**payload, "seq": seq}]
        return {"seq": seq}


@router.post("/receiver/sync", response_model=ReceiverState, response_model_exclude_unset=True)
async def sync(body: Sync, c: C, p: P) -> dict[str, Any]:
    empty = {"session": None, "controller_name": None, "command": None}
    async with transaction(c) as s:
        receiver = await device(s, p, tv=True)
        target = await s.scalar(select(CastTarget).where(CastTarget.sid == receiver.sid))
        if target and (
            not body.enabled or target.profile_id != p.profile_id or target.generation != receiver.generation
        ):
            await s.delete(target)
            await s.flush()
            target = None
        if not body.enabled:
            return empty
        if not target:
            target = CastTarget(
                id=str(uuid4()), sid=receiver.sid, profile_id=p.profile_id, generation=receiver.generation
            )
            s.add(target)
        target.name = (receiver.label or body.name or "Paired TV").strip()[:128] or "Paired TV"
        target.seen = utcnow()
        link = await current(s, target, p)
        if not link:
            return empty
        acknowledged_session = str(body.session_id) == link.id
        if link.connected and acknowledged_session:
            if body.playback:
                playback = body.playback.model_dump()
                # Signed provider artwork may include the receiver's private access grant.
                if playback["poster"] and (
                    "?" in playback["poster"] or not playback["poster"].startswith(("/images/", "/api/v1/images/"))
                ):
                    playback["poster"] = None
                link.playback = playback
            if body.ack == link.last_ack + 1 and any(q["seq"] == body.ack for q in link.commands):
                link.last_ack = body.ack
                link.last_error = "The TV could not complete this command." if body.error else None
        sender = await s.get(AuthSession, link.sid)
        next_command = next((q for q in link.commands if q["seq"] > link.last_ack), None) if link.connected else None
        return {
            "session": session_data(link, target),
            "controller_name": sender.label if sender else "Phone",
            "command": next_command,
        }


@router.post("/receiver/decision", status_code=204)
async def decide(body: Decision, c: C, p: P) -> Response:
    async with transaction(c) as s:
        link = await s.get(CastConnection, str(body.session_id))
        target = await s.get(CastTarget, link.target_id) if link else None
        if not target or target.sid != p.session_id or not link or not await current(s, target, p):
            raise NotFound("The request expired.")
        if body.accept:
            link.connected = True
        else:
            await s.delete(link)
    return Response(status_code=204)


@router.delete("/session/{session_id}", status_code=204)
async def disconnect(session_id: UUID, c: C, p: P) -> Response:
    async with transaction(c) as s:
        link = await s.scalar(
            select(CastConnection)
            .join(CastTarget)
            .where(
                CastConnection.id == str(session_id),
                CastTarget.profile_id == p.profile_id,
                or_(CastConnection.sid == p.session_id, CastTarget.sid == p.session_id),
            )
        )
        if link:
            await s.delete(link)
    return Response(status_code=204)
