from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.api.deps import C, P, acting_as
from app.api.routers.cast import allowed_load
from app.api.routers.rooms import _room, _seat, _title
from app.api.schemas import RoomMember
from app.container import Container
from app.services import room_policy
from app.services.auth import Principal
from app.services.errors import Conflict, Forbidden, ServiceError
from app.services.rooms import Event, Member, Room
from app.services.visibility import require_watch

router = APIRouter(prefix="/rooms", tags=["rooms"])
SeatHeader = Annotated[str, Header(alias="X-Room-Seat", max_length=2048)]


class Action(BaseModel):
    action: Literal["suggest", "remove", "advance", "offer", "accept", "decline"]
    id: str = Field(default="", max_length=256)


class QueueItem(BaseModel):
    id: str
    title: str


class RoomControls(BaseModel):
    queue: list[QueueItem]
    members: list[RoomMember]
    member_id: str
    is_host: bool
    offered_to: str | None
    revision: int


def viewer(c: Container, code: str, token: str, p: Principal) -> tuple[Room, Member]:
    room = _room(c, code)
    seat = _seat(c, room, token, p)
    if not seat or not room_policy.can_join(p):
        raise Forbidden("Join this room to manage its queue.")
    return room, seat


async def state(c: Container, room: Room, p: Principal, seat: Member) -> dict[str, Any]:
    items = []
    for ref in list(room.suggestions):
        try:
            await require_watch(c.db, p.permissions, ref)
            tile = await _title(c, p, ref)
            items.append({"id": ref, "title": tile["title"]})
        except ServiceError:
            continue
    return {
        "queue": items,
        "members": room.member_list(),
        "member_id": seat.id,
        "is_host": room.is_host(p.profile_id),
        "offered_to": room.host_offer,
        "revision": room.title_revision,
    }


@router.get("/{code}/controls", response_model=RoomControls)
async def controls(code: str, c: C, p: P, x_room_seat: SeatHeader) -> dict[str, Any]:
    room, seat = viewer(c, code, x_room_seat, p)
    return await state(c, room, p, seat)


@router.post("/{code}/controls", response_model=RoomControls)
async def control(code: str, body: Action, c: C, p: P, x_room_seat: SeatHeader) -> dict[str, Any]:
    room, seat = viewer(c, code, x_room_seat, p)
    host = room.is_host(p.profile_id)
    if room.switching:
        raise Conflict("The room is already changing titles.")
    if body.action in ("remove", "advance", "offer") and not host:
        raise Forbidden("Only the current host can do that.")
    if body.action == "suggest":
        await allowed_load(c, p, body.id, None, None)
        episode = await c.playback.room_episode(p.profile_id, body.id, None)
        ref = f"lib:{episode[1].source_id}:{episode[1].ext_id}" if episode else body.id
        await _title(c, p, ref)
        viewer(c, code, x_room_seat, p)
        if ref not in room.suggestions:
            if len(room.suggestions) >= 20:
                raise Conflict("The room queue is full.")
            room.suggestions.append(ref)
    elif body.action == "remove":
        if body.id in room.suggestions:
            room.suggestions.remove(body.id)
    elif body.action == "offer":
        other = room.members.get(body.id)
        if not other or other.profile_id == p.profile_id:
            raise Conflict("Choose another member who is present.")
        room.host_offer = other.id
    elif body.action in ("accept", "decline"):
        if room.host_offer != seat.id:
            raise Forbidden("This host invitation is no longer yours.")
        if body.action == "accept":
            if not room_policy.can_create(p):
                raise Forbidden("Hosting requires permission to create rooms.")
            room.host_profile_id, room.host_name = p.profile_id, seat.name
        room.host_offer = None
        c.rooms.deliver(room, [Event("members", {"members": room.member_list()}), Event("state", room.state_data())])
    elif body.action == "advance":
        ref = body.id
        if ref not in room.suggestions:
            raise Conflict("Choose a title from the queue.")
        roster = dict(room.members)
        room.switching = True
        try:
            for person in roster.values():
                try:
                    principal = await c.auth.session_principal(
                        person.profile_id, person.token_version, person.session_id
                    )
                    async with acting_as(c, principal):
                        if not room_policy.can_join(principal):
                            raise Forbidden("Member cannot join.")
                        await allowed_load(c, principal, ref, None, None)
                except ServiceError as exc:
                    raise Conflict("Everyone needs access before this title can start.") from exc
            viewer(c, code, x_room_seat, p)
            if roster != room.members or not room.is_host(p.profile_id) or ref not in room.suggestions:
                raise Conflict("The room changed. Check the queue again.")
            await c.room_playback.release_title(room)
            if room.closed:
                raise Conflict("The room has ended.")
            c.rooms.deliver(room, room.start_suggestion(ref, c.rooms.clock()))
        finally:
            room.switching = False
    return await state(c, room, p, seat)
