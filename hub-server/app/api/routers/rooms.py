"""/rooms: watch rooms (create, join screen, join, close) and the room WebSocket."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response, WebSocket

from app.api import schemas as S
from app.api.deps import C, P, acting_as, require_reachable
from app.container import Container
from app.services import ids, room_policy
from app.services.adult import require_adult
from app.services.auth import Principal
from app.services.context import is_https
from app.services.errors import Forbidden, NotFound, PermissionDenied, ServiceError, Unauthorized
from app.services.room_hub import CLOSE_NOT_FOUND, CLOSE_UNAUTHORIZED, LEAVE_CODES, Connection, RoomHub
from app.services.rooms import MAX_MEMBERS, Member, Room

router = APIRouter()

API_PREFIX = "/api/v1"
TITLE_FIELDS = ("id", "kind", "title", "subtitle", "year", "poster", "backdrop")


def _room(c: Container, code: str) -> Room:
    room = c.rooms.registry.get(code)
    if room is None:
        raise NotFound("This room no longer exists.", code="room_not_found")
    return room


async def _require_rated(c: Container, p: Principal, ref: str) -> None:
    """A room is no way around a rating ceiling or the adult lock: the title must be one this caller may watch."""
    await require_adult(c.db, p, ref)
    if c.content_policy.applies_to(p):
        await c.content_policy.require(p.profile_id, ref)


async def _title(c: Container, p: Principal, ref: str, series: str | None = None) -> dict[str, Any]:
    """What the join screen shows of the title, and only to a profile that may watch it."""
    await _require_rated(c, p, ref)
    detail = await c.catalog.detail(ref, p.profile_id)
    title = {k: detail.get(k) for k in TITLE_FIELDS}
    if series and title["kind"] == "episode" and " · " not in (title["subtitle"] or ""):
        # Not every source says which show an episode belongs to; the room knows where it took it from.
        title["subtitle"] = " · ".join(part for part in (series, title["subtitle"]) if part)
    return title


def _out(c: Container, room: Room, p: Principal, title: dict[str, Any]) -> dict[str, Any]:
    state = room.state_data()
    return {
        "code": room.code,
        "join_path": f"/room/{room.code}",
        "id": room.ref,
        "live": room.live,
        "variant_id": room.variant_id,
        "audio_track": room.audio_track,
        "host_name": room.host_name,
        "is_host": room.is_host(p.profile_id),
        "controls_locked": room.controls_locked,
        "waiting_for": room.waiting_for,
        "max_members": MAX_MEMBERS,
        "members": room.member_list(),
        "state": {k: state[k] for k in ("playing", "position_ms", "as_of_ms")},
        "server_ms": c.rooms.clock(),
        "title": title,
    }


@router.post("/rooms", response_model=S.Room, status_code=201, tags=["rooms"])
async def create_room(body: S.RoomCreate, c: C, p: P) -> dict[str, Any]:
    if not room_policy.can_create(p):
        raise PermissionDenied(["rooms.create"])
    title = await _title(c, p, body.id)
    ref, variant_id, series = body.id, body.variant_id, None
    # A show is watched an episode at a time, and a room is on one thing: the episode its creator
    # would be given now. Members resolving the show for themselves would each get their own.
    pinned = await c.playback.room_episode(p.profile_id, body.id, body.variant_id)
    if pinned is not None:
        show, episode = pinned
        ref = ids.lib_id(episode.source_id, episode.ext_id)
        # The variant named was a show's; of the episode there is the one.
        variant_id = ref if body.variant_id else None
        series = ids.clean_display_title(show.title) if show.kind != "season" else None
        title = await _title(c, p, ref, series)
    host = await c.auth.get_profile(p.profile_id)
    room = c.rooms.create(
        ref,
        host_profile_id=p.profile_id,
        host_name=host.name,
        variant_id=variant_id,
        audio_track=body.audio_track,
        live=ref.startswith("ch:"),
        series=series,
    )
    return _out(c, room, p, title)


@router.get("/rooms/{code}", response_model=S.Room, tags=["rooms"])
async def show_room(code: str, c: C, p: P) -> dict[str, Any]:
    room = _room(c, code)
    if not room_policy.can_join(p):
        raise PermissionDenied(["rooms.join"])
    return _out(c, room, p, await _title(c, p, room.ref, room.series))


@router.post("/rooms/{code}/join", response_model=S.RoomJoined, tags=["rooms"])
async def join_room(code: str, body: S.RoomJoin, c: C, p: P) -> dict[str, Any]:
    room = _room(c, code)
    if not room_policy.can_join(p):
        raise PermissionDenied(["rooms.join"])
    await _require_rated(c, p, room.ref)
    # A caller who presents the token of a seat they hold keeps it; anything else is a new seat.
    seat = _seat(c, room, body.member_token, p) if body.member_token else None
    if seat is None:
        room.require_space()
    # The member token outlives this request, so it is only minted for a session that is valid now.
    profile = await c.auth.session_valid(p.profile_id, p.token_version, p.session_id)
    descriptor = await c.room_playback.descriptor_for(
        room,
        p,
        body.client,
        now_ms=c.rooms.clock(),
        prefer_hls=body.prefer_hls,
        recovery_generation=body.recovery_generation,
        allow_unverified_audio=body.allow_unverified_audio,
        https_site=is_https.get(),
        member_id=seat.id if seat is not None else None,
    )
    if room.closed:  # closed while the title was being resolved
        raise NotFound("This room no longer exists.", code="room_not_found")
    if seat is not None and seat.id in room.members:
        member = seat
        room.touch(member, c.rooms.clock())
    else:
        member = c.rooms.join(room, p.profile_id, profile.name)
    c.room_playback.seated(room, member.id, descriptor)
    return {
        "member_token": c.rooms.tokens.issue(room.code, member.id, p),
        "member_id": member.id,
        "expires_in_seconds": c.rooms.tokens.ttl_s,
        "ws_path": f"{API_PREFIX}/rooms/{room.code}/ws",
        "descriptor": descriptor,
    }


def _seat(c: Container, room: Room, member_token: str, p: Principal) -> Member | None:
    """The seat a join's ``member_token`` names, when it is this caller's own and still in the room."""
    claims = c.rooms.tokens.verify(member_token)
    if claims is None or claims.room != room.code:
        return None
    if (claims.profile_id, claims.session_id) != (p.profile_id, p.session_id):
        return None
    member = room.members.get(claims.member_id)
    return member if member is not None and member.profile_id == p.profile_id else None


@router.get("/rooms/{code}/descriptor", response_model=S.PlaybackDescriptor, tags=["rooms"])
async def room_descriptor(
    code: str,
    request: Request,
    c: C,
    member_token: str = Query(max_length=2048),
    client: Literal["android_tv", "web"] = "web",
    prefer_hls: bool = False,
    recovery_generation: str | None = Query(None, max_length=64),
    allow_unverified_audio: bool = False,
) -> dict[str, Any]:
    """The member's descriptor as it is now. Authorized like the socket: by the member token."""
    claims = c.rooms.tokens.verify(member_token)
    if claims is None or claims.room != code.upper():
        raise Unauthorized("This is not a seat in this room.", code="room_unauthorized")
    # The member's access as it stands now, not as it stood when they joined.
    p = await c.auth.session_principal(claims.profile_id, claims.token_version, claims.session_id)
    require_reachable(request, c, "remote.access" in p.permissions)
    room = _room(c, code)
    member = room.members.get(claims.member_id)
    if member is None or member.profile_id != claims.profile_id:
        raise NotFound("This seat is no longer in the room; join again.", code="not_a_member")
    if not room_policy.can_join(p):
        raise PermissionDenied(["rooms.join"])
    async with acting_as(c, p):
        await _require_rated(c, p, room.ref)
        descriptor = await c.room_playback.descriptor_for(
            room,
            p,
            client,
            now_ms=c.rooms.clock(),
            prefer_hls=prefer_hls,
            recovery_generation=recovery_generation,
            allow_unverified_audio=allow_unverified_audio,
            https_site=is_https.get(),
            member_id=member.id,
        )
    if room.closed or member.id not in room.members:  # ended while the title was being resolved
        raise NotFound("This room no longer exists.", code="room_not_found")
    room.touch(member, c.rooms.clock())
    c.room_playback.seated(room, member.id, descriptor)
    return descriptor


@router.delete("/rooms/{code}", status_code=204, tags=["rooms"])
async def close_room(code: str, c: C, p: P) -> Response:
    room = _room(c, code)
    if not room_policy.can_close(p, room.is_host(p.profile_id)):
        raise Forbidden("Only the host can close this room.")
    c.rooms.close(room.code, "deleted")
    return Response(status_code=204)


async def _admit(c: Container, ws: WebSocket, code: str, member_token: str) -> Connection | tuple[str, int]:
    """The member's connection, or why (and with which close code) the socket is refused."""
    claims = c.rooms.tokens.verify(member_token)
    if claims is None or claims.room != code.upper():
        return "unauthorized", CLOSE_UNAUTHORIZED
    try:
        # The socket carries no bearer, so the member's user is held to the home network here,
        # as the join that seated them was and as their descriptor is.
        p = await c.auth.session_principal(claims.profile_id, claims.token_version, claims.session_id)
        require_reachable(ws, c, "remote.access" in p.permissions)
    except ServiceError as exc:
        return "remote_access_denied" if exc.code == "remote_access_denied" else "unauthorized", CLOSE_UNAUTHORIZED
    # No await between here and attach: the room cannot change under the checks.
    room = c.rooms.registry.get(code)
    if room is None:
        return "not_found", CLOSE_NOT_FOUND
    member = room.members.get(claims.member_id)
    if member is None or member.profile_id != claims.profile_id:
        return "not_a_member", CLOSE_NOT_FOUND
    return c.rooms.attach(room, member)


async def _read(ws: WebSocket, hub: RoomHub, conn: Connection) -> int:
    """Feed the member's messages to the hub until the socket ends. Returns the close code."""
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return int(message.get("code") or 1005)
        hub.receive(conn, message.get("text"))


async def _write(ws: WebSocket, conn: Connection) -> None:
    while True:
        item = await conn.next()
        if isinstance(item, int):
            await ws.close(code=item)
            return
        await ws.send_json(item)


@router.websocket("/rooms/{code}/ws")
async def room_socket(ws: WebSocket, code: str, member_token: str = "") -> None:
    c: Container = ws.app.state.container
    # Admitted before the handshake completes, so nothing the room says after the
    # client sees the socket open can be missed.
    admitted = await _admit(c, ws, code, member_token)
    if not isinstance(admitted, Connection):
        # Accept, then close with a reason: a browser cannot read a refused handshake.
        reason, close_code = admitted
        await ws.accept()
        await ws.send_json({"type": "closed", "reason": reason})
        await ws.close(code=close_code)
        return
    conn = admitted
    tasks: list[asyncio.Task[Any]] = []
    reader: asyncio.Task[int] | None = None
    try:
        await ws.accept()
        reader = asyncio.create_task(_read(ws, c.rooms, conn))
        tasks = [reader, asyncio.create_task(_write(ws, conn))]
        # Whichever side ends first ends the socket: the member went away, or the hub closed it.
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        left = False
        for task in tasks:
            if not task.done():
                task.cancel()
            elif not task.cancelled() and task.exception() is None and task is reader:
                left = task.result() in LEAVE_CODES
        c.rooms.detach(conn, left=left)
