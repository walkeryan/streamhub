"""Watch rooms: one authoritative playback clock per room, and the rules that move it.

Pure logic. Nothing here reads a clock, a socket or the database: every method takes
``now_ms`` (server epoch milliseconds) and returns the events its caller must deliver,
so the rules can be tested with a fake clock. Rooms live in memory because the server
runs a single worker.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.services.errors import Conflict

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_LENGTH = 6
MAX_MEMBERS = 8
MAX_ROOMS = 20
EMPTY_TTL_MS = 30 * 60_000  # a room nobody is in expires
BUFFER_GRACE_MS = 2_000  # buffering shorter than this never stops the room
WAIT_TIMEOUT_MS = 15_000  # the room waits this long for a buffering member, then moves on
SILENT_MS = 20_000  # a member not heard from for this long is dropped


@dataclass
class RoomState:
    playing: bool
    position_ms: int
    as_of_ms: int  # server epoch ms when position was true


def position_at(state: RoomState, now_ms: int) -> int:
    if not state.playing:
        return state.position_ms
    return state.position_ms + max(0, now_ms - state.as_of_ms)


@dataclass(frozen=True)
class Command:
    kind: Literal["play", "pause", "seek", "lock"]
    position_ms: int | None = None  # seek
    on: bool | None = None  # lock


@dataclass(frozen=True)
class Event:
    kind: Literal["state", "members", "waiting", "closed"]
    data: dict[str, Any] = field(default_factory=dict)
    to: str | None = None  # a member id; None means every member of the room

    def message(self) -> dict[str, Any]:
        return {"type": self.kind, **self.data}


@dataclass
class Member:
    id: str
    profile_id: str
    name: str
    last_seen_ms: int
    buffering_since_ms: int | None = None
    # This spell of buffering already cost the room a full wait. It does not get a
    # second one until the member has recovered, or one bad connection stalls everyone.
    wait_spent: bool = False


class CommandRejected(Exception):
    """A command the room refused; only its sender needs to hear about it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class _Wait:
    since_ms: int
    member_id: str


class Room:
    def __init__(
        self,
        code: str,
        ref: str,
        *,
        host_profile_id: str,
        host_name: str,
        now_ms: int,
        variant_id: str | None = None,
        audio_track: int | None = None,
        live: bool = False,
    ) -> None:
        self.code = code
        self.ref = ref  # title or channel id
        self.variant_id = variant_id
        self.audio_track = audio_track
        self.host_profile_id = host_profile_id
        self.host_name = host_name
        self.controls_locked = False
        self.live = live
        # Live channels have no position: their state is only ``playing``.
        self.state = RoomState(playing=live, position_ms=0, as_of_ms=now_ms)
        self.members: dict[str, Member] = {}
        self.closed = False
        self._wait: _Wait | None = None
        self._empty_since_ms = now_ms

    # ------------------------------------------------------------------ members
    @property
    def full(self) -> bool:
        return len(self.members) >= MAX_MEMBERS

    def is_host(self, profile_id: str) -> bool:
        return profile_id == self.host_profile_id

    def join(self, profile_id: str, name: str, now_ms: int) -> tuple[Member, list[Event]]:
        if self.full:
            raise Conflict(f"This room already has {MAX_MEMBERS} members.", code="room_full")
        member = Member(id=secrets.token_hex(8), profile_id=profile_id, name=name, last_seen_ms=now_ms)
        self.members[member.id] = member
        return member, [self._members_event()]

    def leave(self, member_id: str, now_ms: int) -> list[Event]:
        if self.members.pop(member_id, None) is None:
            return []
        return self._after_departure(now_ms)

    def touch(self, member: Member, now_ms: int) -> None:
        member.last_seen_ms = max(member.last_seen_ms, now_ms)

    def _after_departure(self, now_ms: int) -> list[Event]:
        if not self.members:
            # Nobody is watching: stop the clock where it is so whoever comes back
            # finds the film where the room left it, not hours further on.
            self._wait = None
            self._empty_since_ms = now_ms
            self.controls_locked = False
            if not self.live:
                self.state = RoomState(False, position_at(self.state, now_ms), now_ms)
            return []
        events = [self._members_event()]
        if self.controls_locked and not any(self.is_host(m.profile_id) for m in self.members.values()):
            # A lock nobody present can lift would strand the room.
            self.controls_locked = False
            events.append(self._state_event(None))
        return events + self._settle(now_ms)

    # ------------------------------------------------------------------ commands
    def apply(self, cmd: Command, by: Member, now_ms: int) -> list[Event]:
        self.touch(by, now_ms)
        if cmd.kind == "lock":
            if not self.is_host(by.profile_id):
                raise CommandRejected("host_only", "Only the host can lock the controls.")
            if self.controls_locked == bool(cmd.on):
                return []
            self.controls_locked = bool(cmd.on)
            return [self._state_event(by.name)]
        if self.controls_locked and not self.is_host(by.profile_id):
            raise CommandRejected("controls_locked", "Only the host can control this room.")
        if cmd.kind == "seek":
            if self.live:
                raise CommandRejected("live_no_seek", "A live channel has no position to seek to.")
            self.state = RoomState(self.state.playing, max(0, int(cmd.position_ms or 0)), now_ms)
            events = [self._state_event(by.name)]
            if self._wait is not None:
                # Clients drop the waiting notice on any state message; the room still waits.
                events.append(self._waiting_event())
            return events
        if cmd.kind == "play":
            if self.state.playing:
                return []
            if self._wait is not None:
                self._end_wait(spend=True)
            self.state = RoomState(True, self.state.position_ms, now_ms)
            return [self._state_event(by.name)]
        # pause
        if not self.state.playing and self._wait is None:
            return []
        # Pausing during a wait turns it into an ordinary pause: nothing resumes by itself.
        self._wait = None
        self.state = RoomState(False, position_at(self.state, now_ms), now_ms)
        return [self._state_event(by.name)]

    def member_buffering(self, member: Member, buffering: bool, now_ms: int) -> list[Event]:
        self.touch(member, now_ms)
        if buffering == (member.buffering_since_ms is not None):
            return []
        member.buffering_since_ms = now_ms if buffering else None
        member.wait_spent = False
        return [self._members_event(), *self._settle(now_ms)]

    # ------------------------------------------------------------------ clock
    def tick(self, now_ms: int) -> list[Event]:
        """Advance the room's timers: resume after the wait, expire, drop silent members."""
        if self.closed:
            return []
        if not self.members:
            if now_ms - self._empty_since_ms >= EMPTY_TTL_MS:
                return self.close("expired")
            return []
        events: list[Event] = []
        silent = [m for m in self.members.values() if now_ms - m.last_seen_ms >= SILENT_MS]
        for member in silent:
            del self.members[member.id]
            events.append(Event("closed", {"reason": "silent"}, to=member.id))
        if silent:
            return events + self._after_departure(now_ms)
        return self._settle(now_ms)

    def close(self, reason: str) -> list[Event]:
        if self.closed:
            return []
        self.closed = True
        self._wait = None
        return [Event("closed", {"reason": reason})]

    def _settle(self, now_ms: int) -> list[Event]:
        """Start, retarget or end the wait for buffering members."""
        blocking = sorted(
            (
                m
                for m in self.members.values()
                if m.buffering_since_ms is not None
                and not m.wait_spent
                and now_ms - m.buffering_since_ms >= BUFFER_GRACE_MS
            ),
            key=lambda m: m.buffering_since_ms or 0,
        )
        if self._wait is None:
            if not (self.state.playing and blocking):
                return []
            self.state = RoomState(False, position_at(self.state, now_ms), now_ms)
            self._wait = _Wait(since_ms=now_ms, member_id=blocking[0].id)
            return [self._state_event(None), self._waiting_event()]
        if blocking and now_ms - self._wait.since_ms < WAIT_TIMEOUT_MS:
            if self._wait.member_id in {m.id for m in blocking}:
                return []
            self._wait.member_id = blocking[0].id  # same wait, same deadline, another name
            return [self._waiting_event()]
        self._end_wait(spend=bool(blocking))
        self.state = RoomState(True, self.state.position_ms, now_ms)
        return [self._state_event(None)]

    def _end_wait(self, *, spend: bool) -> None:
        self._wait = None
        if spend:
            for member in self.members.values():
                member.wait_spent = member.buffering_since_ms is not None

    # ------------------------------------------------------------------ events
    @property
    def waiting_for(self) -> str | None:
        """Name of the member the room is paused for, if it is waiting."""
        if self._wait is None:
            return None
        member = self.members.get(self._wait.member_id)
        return member.name if member else None

    def member_list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": m.id,
                "name": m.name,
                "host": self.is_host(m.profile_id),
                "buffering": m.buffering_since_ms is not None,
            }
            for m in self.members.values()
        ]

    def state_data(self, by: str | None = None) -> dict[str, Any]:
        return {
            "playing": self.state.playing,
            "position_ms": None if self.live else self.state.position_ms,
            "as_of_ms": self.state.as_of_ms,
            "by": by,
            "controls_locked": self.controls_locked,
        }

    def snapshot(self, member: Member) -> list[Event]:
        """Everything a member who has just connected (or reconnected) needs, addressed to them alone."""
        events = [
            Event("members", {"members": self.member_list()}, to=member.id),
            Event("state", self.state_data(), to=member.id),
        ]
        if self._wait is not None:
            events.append(Event("waiting", {"name": self.waiting_for}, to=member.id))
        return events

    def _members_event(self) -> Event:
        return Event("members", {"members": self.member_list()})

    def _state_event(self, by: str | None) -> Event:
        return Event("state", self.state_data(by))

    def _waiting_event(self) -> Event:
        return Event("waiting", {"name": self.waiting_for})


class RoomRegistry:
    def __init__(self, *, max_rooms: int = MAX_ROOMS, choice: Callable[[str], str] = secrets.choice) -> None:
        self._rooms: dict[str, Room] = {}
        self._max_rooms = max_rooms
        self._choice = choice

    def __len__(self) -> int:
        return len(self._rooms)

    def create(
        self,
        ref: str,
        *,
        host_profile_id: str,
        host_name: str,
        now_ms: int,
        variant_id: str | None = None,
        audio_track: int | None = None,
        live: bool = False,
    ) -> Room:
        if len(self._rooms) >= self._max_rooms:
            raise Conflict("Too many watch rooms are open. Try again later.", code="room_limit")
        code = self._new_code()
        room = Room(
            code,
            ref,
            host_profile_id=host_profile_id,
            host_name=host_name,
            now_ms=now_ms,
            variant_id=variant_id,
            audio_track=audio_track,
            live=live,
        )
        self._rooms[code] = room
        return room

    def get(self, code: str) -> Room | None:
        return self._rooms.get(code.upper())

    def remove(self, code: str, reason: str = "deleted") -> list[Event]:
        room = self._rooms.pop(code.upper(), None)
        return room.close(reason) if room else []

    def sweep(self, now_ms: int) -> list[tuple[Room, list[Event]]]:
        """Tick every room and forget the ones that closed. Returns what each room has to say."""
        out: list[tuple[Room, list[Event]]] = []
        for code, room in list(self._rooms.items()):
            events = room.tick(now_ms)
            if room.closed:
                del self._rooms[code]
            if events:
                out.append((room, events))
        return out

    def _new_code(self) -> str:
        while True:
            code = "".join(self._choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._rooms:
                return code
