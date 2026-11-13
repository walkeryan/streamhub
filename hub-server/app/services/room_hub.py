"""The live side of watch rooms: member sockets, fan-out, member tokens and the tick loop.

``app.services.rooms`` decides what happens; this module delivers it. Every member socket
has an ordered outbox, filled synchronously as events are produced, so each member sees
the room's events in the order the room applied them however slow another member is.

A room that plays one shared upstream has work to do before a seek is announced (the session is
restarted at the new position). The seek's events, and whatever the room says after them, then
wait their turn in a per-room queue, so the order members see is still the order of the room.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import jwt

from app.infra.crypto import Crypto
from app.services.auth import Principal
from app.services.rooms import Command, CommandRejected, Event, Member, Room, RoomRegistry

log = logging.getLogger(__name__)

ALG = "HS256"
TOKEN_TYPE = "room_member"
MEMBER_TOKEN_TTL_S = 2 * 3600
TICK_INTERVAL_S = 1.0
MAX_MESSAGE_CHARS = 1024
MAX_QUEUED = 64  # a socket this far behind is not reading; it is dropped, not buffered for
MAX_POSITION_MS = 48 * 3600 * 1000

# WebSocket close codes. Every server-initiated close is preceded by a ``closed`` message.
CLOSE_ROOM = 4000  # the room or this member's place in it ended; the message says why
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
# A member who says goodbye leaves at once; any other end of the socket is a lost
# connection, and the member keeps their place until the silence rule drops them.
LEAVE_CODES = (1000, 1001)


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class MemberClaims:
    room: str
    member_id: str
    profile_id: str
    token_version: int
    session_id: str


class RoomTokens:
    """Short-lived tokens for the room socket, so the access token never travels in a URL."""

    def __init__(self, crypto: Crypto, ttl_s: int = MEMBER_TOKEN_TTL_S) -> None:
        self._key = crypto.jwt_key
        self.ttl_s = ttl_s

    def issue(self, room_code: str, member_id: str, principal: Principal) -> str:
        now = int(time.time())
        payload = {
            "typ": TOKEN_TYPE,
            "room": room_code,
            "mid": member_id,
            "sub": principal.profile_id,
            "ver": principal.token_version,
            "sid": principal.session_id,
            "iat": now,
            "exp": now + self.ttl_s,
        }
        return jwt.encode(payload, self._key, algorithm=ALG)

    def verify(self, token: str) -> MemberClaims | None:
        try:
            payload = jwt.decode(token, self._key, algorithms=[ALG])
            if payload.get("typ") != TOKEN_TYPE:
                return None
            return MemberClaims(
                room=str(payload["room"]),
                member_id=str(payload["mid"]),
                profile_id=str(payload["sub"]),
                token_version=int(payload["ver"]),
                session_id=str(payload["sid"]),
            )
        except (jwt.PyJWTError, KeyError, TypeError, ValueError):
            return None


class Connection:
    """One member's socket as the hub sees it: an outbox its writer drains in order."""

    def __init__(self, room: Room, member: Member) -> None:
        self.room = room
        self.member = member
        self.closing = False
        self._outbox: asyncio.Queue[dict[str, Any] | int] = asyncio.Queue()

    def send(self, message: dict[str, Any]) -> None:
        if self.closing:
            return
        if self._outbox.qsize() >= MAX_QUEUED:
            while not self._outbox.empty():
                self._outbox.get_nowait()
            self.close("slow")
            return
        self._outbox.put_nowait(message)

    def close(self, reason: str, code: int = CLOSE_ROOM) -> None:
        if self.closing:
            return
        self.closing = True
        self._outbox.put_nowait({"type": "closed", "reason": reason})
        self._outbox.put_nowait(code)

    async def next(self) -> dict[str, Any] | int:
        """The next message to write, or the close code that ends the socket."""
        return await self._outbox.get()


class RoomHub:
    def __init__(
        self,
        registry: RoomRegistry,
        tokens: RoomTokens,
        *,
        clock: Callable[[], int] = now_ms,
        interval_s: float = TICK_INTERVAL_S,
    ) -> None:
        self.registry = registry
        self.tokens = tokens
        self.clock = clock
        self._interval_s = interval_s
        self._conns: dict[str, dict[str, Connection]] = {}
        self._ticker: asyncio.Task[None] | None = None
        self._running = False
        # Hooks of the room's shared playback (``RoomPlayback``); a hub without them only relays.
        # Work to finish before a seek is announced, or None when the room has none to do.
        self.before_seek: Callable[[Room], Awaitable[None] | None] | None = None
        # Fields added to every ``state`` message as it is sent.
        self.state_extra: Callable[[Room], dict[str, Any]] | None = None
        # Told whenever a room's members or lifetime may have changed.
        self.on_change: Callable[[Room], None] | None = None
        self._queued: dict[str, deque[tuple[list[Event], Awaitable[None] | None]]] = {}
        self._drains: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Allow the tick loop. It only runs while a room exists."""
        self._running = True
        self._ensure_ticker()

    async def shutdown(self) -> None:
        self._running = False
        ticker, self._ticker = self._ticker, None
        if ticker is not None:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
        for drain in list(self._drains):
            drain.cancel()
        await asyncio.gather(*self._drains, return_exceptions=True)
        for room in self.registry.rooms():
            self.close(room.code, "shutdown")

    @property
    def ticking(self) -> bool:
        return self._ticker is not None and not self._ticker.done()

    def _ensure_ticker(self) -> None:
        if self._running and not self.ticking and len(self.registry):
            self._ticker = asyncio.create_task(self._tick_loop())

    async def _tick_loop(self) -> None:
        try:
            while len(self.registry):
                await asyncio.sleep(self._interval_s)
                try:
                    self.tick()
                except Exception:
                    log.exception("room tick failed")
        finally:
            if self._ticker is asyncio.current_task():
                self._ticker = None

    def tick(self) -> None:
        for room, events in self.registry.sweep(self.clock()):
            self.deliver(room, events)

    # ------------------------------------------------------------------ rooms
    def create(
        self,
        ref: str,
        *,
        host_profile_id: str,
        host_name: str,
        variant_id: str | None = None,
        audio_track: int | None = None,
        live: bool = False,
        series: str | None = None,
    ) -> Room:
        room = self.registry.create(
            ref,
            host_profile_id=host_profile_id,
            host_name=host_name,
            now_ms=self.clock(),
            variant_id=variant_id,
            audio_track=audio_track,
            live=live,
            series=series,
        )
        self._ensure_ticker()
        return room

    def join(self, room: Room, profile_id: str, name: str) -> Member:
        member, events = room.join(profile_id, name, self.clock())
        self.deliver(room, events)
        return member

    def close(self, code: str, reason: str) -> None:
        room = self.registry.get(code)
        if room is not None:
            self.deliver(room, self.registry.remove(code, reason))

    # ------------------------------------------------------------------ sockets
    def attach(self, room: Room, member: Member) -> Connection:
        """Register a member's socket and queue what it needs to catch up. A newer socket replaces an older one."""
        conns = self._conns.setdefault(room.code, {})
        previous = conns.get(member.id)
        if previous is not None:
            previous.close("replaced")
        conn = conns[member.id] = Connection(room, member)
        room.touch(member, self.clock())
        for event in room.snapshot(member):
            conn.send(self._message(room, event))
        return conn

    def detach(self, conn: Connection, *, left: bool) -> None:
        conns = self._conns.get(conn.room.code, {})
        if conns.get(conn.member.id) is not conn:
            return  # replaced, dropped, or the room closed: nothing of this socket remains
        del conns[conn.member.id]
        if not conns:
            self._conns.pop(conn.room.code, None)
        if left:
            self.deliver(conn.room, conn.room.leave(conn.member.id, self.clock()))

    def _message(self, room: Room, event: Event) -> dict[str, Any]:
        message = event.message()
        if event.kind == "state" and self.state_extra is not None:
            message.update(self.state_extra(room))
        return message

    def deliver(self, room: Room, events: list[Event], before: Awaitable[None] | None = None) -> None:
        """Send ``events`` to the room's sockets, after ``before`` and after anything already waiting."""
        if self.on_change is not None:
            self.on_change(room)
        queue = self._queued.get(room.code)
        if queue is None:
            if before is None:
                self._send(room, events)
                return
            queue = self._queued[room.code] = deque()
            drain = asyncio.create_task(self._drain(room, queue))
            self._drains.add(drain)
            drain.add_done_callback(self._drains.discard)
        queue.append((events, before))

    async def _drain(self, room: Room, queue: deque[tuple[list[Event], Awaitable[None] | None]]) -> None:
        try:
            while queue:
                events, before = queue[0]
                if before is not None:
                    try:
                        await before
                    except Exception:
                        # The members are told of the seek all the same; the stream is retried on demand.
                        log.exception("room seek preparation failed", extra={"room": room.code})
                queue.popleft()
                self._send(room, events)
        finally:
            # No await since the queue was last seen empty: nothing can have been added behind it.
            self._queued.pop(room.code, None)

    def _send(self, room: Room, events: list[Event]) -> None:
        for event in events:
            conns = self._conns.get(room.code, {})
            for conn in [c for c in conns.values() if event.to in (None, c.member.id)]:
                if event.kind == "closed":
                    conn.close(str(event.data["reason"]))
                    del conns[conn.member.id]
                else:
                    conn.send(self._message(room, event))
            if not conns:
                self._conns.pop(room.code, None)

    def receive(self, conn: Connection, text: str | None) -> None:
        """Handle one message from a member's socket."""
        room, member = conn.room, conn.member
        if conn.closing or room.closed or member.id not in room.members:
            return
        now = self.clock()
        message = _parse(text)
        if message is None:
            conn.send(_error("bad_message", "Unrecognised message."))
            return
        kind = message["type"]
        if kind == "ping":
            room.touch(member, now)
            t = message.get("t")
            echo = t if isinstance(t, (int, float)) and not isinstance(t, bool) else None
            conn.send({"type": "pong", "t": echo, "server_ms": now})
            return
        try:
            if kind == "buffering":
                events = room.member_buffering(member, message["on"], now)
            elif kind == "seek":
                events = room.apply(Command("seek", position_ms=message["position_ms"]), member, now)
                # A room on one shared upstream restarts it at the new position before anyone is told.
                before = self.before_seek(room) if self.before_seek is not None else None
                self.deliver(room, events, before)
                return
            elif kind == "lock":
                events = room.apply(Command("lock", on=message["on"]), member, now)
            else:
                events = room.apply(Command(kind), member, now)
        except CommandRejected as exc:
            conn.send(_error(exc.code, exc.message))
            return
        self.deliver(room, events)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


def _parse(text: str | None) -> dict[str, Any] | None:
    """A well-formed client message, or None."""
    if text is None or len(text) > MAX_MESSAGE_CHARS:
        return None
    try:
        message = json.loads(text)
    except ValueError:
        return None
    if not isinstance(message, dict):
        return None
    kind = message.get("type")
    if kind in ("ping", "play", "pause"):
        return message
    if kind in ("buffering", "lock"):
        return message if isinstance(message.get("on"), bool) else None
    if kind == "seek":
        position = message.get("position_ms")
        ok = isinstance(position, int) and not isinstance(position, bool) and 0 <= position <= MAX_POSITION_MS
        return message if ok else None
    return None
