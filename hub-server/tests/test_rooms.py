"""Room core: every rule of the room clock, driven by an explicit (fake) clock."""

from __future__ import annotations

from typing import Any

import pytest

from app.services import rooms as R
from app.services.errors import Conflict
from app.services.rooms import Command, CommandRejected, Event, Member, Room, RoomRegistry, RoomState, position_at

T0 = 1_700_000_000_000  # room creation; every step below is an offset from it


def make(live: bool = False) -> tuple[Room, Member, Member]:
    room = Room(
        "ABCDEF", "ch:s:1" if live else "lib:s:1", host_profile_id="host", host_name="Host", now_ms=T0, live=live
    )
    host, _ = room.join("host", "Host", T0)
    guest, _ = room.join("guest", "Guest", T0)
    return room, host, guest


def brief(event: Event) -> tuple[Any, ...]:
    d = event.data
    if event.kind == "state":
        return ("state", d["playing"], d["position_ms"], d["by"])
    if event.kind == "waiting":
        return ("waiting", d["name"])
    if event.kind == "members":
        return ("members", tuple(m["name"] + ("*" if m["buffering"] else "") for m in d["members"]))
    return ("closed", d["reason"], event.to)


COMMANDS = {
    "play": lambda arg: Command("play"),
    "pause": lambda arg: Command("pause"),
    "seek": lambda arg: Command("seek", position_ms=arg),
    "lock": lambda arg: Command("lock", on=arg),
}


def run(room: Room, who: dict[str, Member], steps: list[tuple[Any, ...]], kinds: set[str]) -> list[tuple[Any, ...]]:
    """Play ``(offset_ms, actor, action[, arg])`` steps; return the trace of the event kinds asked for."""
    trace: list[tuple[Any, ...]] = []
    for offset, actor, action, *rest in steps:
        now, arg = T0 + offset, rest[0] if rest else None
        if action == "tick":
            events = room.tick(now)
        elif action == "buffering":
            events = room.member_buffering(who[actor], bool(arg), now)
        elif action == "ping":
            room.touch(who[actor], now)
            events = []
        elif action == "leave":
            events = room.leave(who[actor].id, now)
        else:
            events = room.apply(COMMANDS[action](arg), who[actor], now)
        trace += [brief(e) for e in events if e.kind in kinds]
    return trace


@pytest.mark.parametrize(
    ("state", "now", "expected"),
    [
        (RoomState(False, 5_000, T0), T0 + 60_000, 5_000),  # paused: frozen
        (RoomState(True, 5_000, T0), T0, 5_000),
        (RoomState(True, 5_000, T0), T0 + 1_500, 6_500),  # playing: advances with server time
        (RoomState(True, 0, T0), T0 + 3_600_000, 3_600_000),
        (RoomState(True, 5_000, T0), T0 - 200, 5_000),  # never before the stamped position
    ],
)
def test_position_at(state: RoomState, now: int, expected: int) -> None:
    assert position_at(state, now) == expected


def test_new_room_starts_paused_at_zero_and_live_starts_playing() -> None:
    room, _, _ = make()
    assert (room.state.playing, room.state.position_ms, room.controls_locked) == (False, 0, False)
    live, _, _ = make(live=True)
    assert live.state.playing is True


@pytest.mark.parametrize(
    ("steps", "trace", "final"),
    [
        # any member's command becomes the room state, stamped with who did it
        ([(1_000, "guest", "play")], [("state", True, 0, "Guest")], (True, 0, T0 + 1_000)),
        (
            [(1_000, "host", "play"), (4_000, "guest", "pause")],
            [("state", True, 0, "Host"), ("state", False, 3_000, "Guest")],
            (False, 3_000, T0 + 4_000),
        ),
        # seek keeps the play/pause flag and restamps the clock
        ([(1_000, "guest", "seek", 90_000)], [("state", False, 90_000, "Guest")], (False, 90_000, T0 + 1_000)),
        (
            [(1_000, "host", "play"), (2_000, "guest", "seek", 90_000)],
            [("state", True, 0, "Host"), ("state", True, 90_000, "Guest")],
            (True, 90_000, T0 + 2_000),
        ),
        ([(1_000, "host", "seek", -5)], [("state", False, 0, "Host")], (False, 0, T0 + 1_000)),
        # repeating the current state changes nothing and tells nobody
        ([(1_000, "host", "pause")], [], (False, 0, T0)),
        ([(1_000, "host", "play"), (2_000, "guest", "play")], [("state", True, 0, "Host")], (True, 0, T0 + 1_000)),
    ],
)
def test_play_pause_seek(steps: list[tuple[Any, ...]], trace: list[tuple[Any, ...]], final: tuple[Any, ...]) -> None:
    room, host, guest = make()
    assert run(room, {"host": host, "guest": guest}, steps, {"state"}) == trace
    assert (room.state.playing, room.state.position_ms, room.state.as_of_ms) == final


@pytest.mark.parametrize(
    ("locked", "actor", "action", "arg", "rejected"),
    [
        (False, "guest", "play", None, None),
        (False, "guest", "seek", 10_000, None),
        (False, "guest", "lock", True, "host_only"),  # lock is the host's alone, locked or not
        (True, "guest", "play", None, "controls_locked"),
        (True, "guest", "pause", None, "controls_locked"),
        (True, "guest", "seek", 10_000, "controls_locked"),
        (True, "guest", "lock", False, "host_only"),
        (True, "host", "play", None, None),
        (True, "host", "seek", 10_000, None),
        (True, "host", "lock", False, None),
    ],
)
def test_controls_lock(locked: bool, actor: str, action: str, arg: Any, rejected: str | None) -> None:
    room, host, guest = make()
    who = {"host": host, "guest": guest}
    if locked:
        events = room.apply(Command("lock", on=True), host, T0 + 500)
        assert [e.kind for e in events] == ["state"] and events[0].data["controls_locked"] is True
    before = (room.state.playing, room.state.position_ms, room.state.as_of_ms, room.controls_locked)
    if rejected:
        with pytest.raises(CommandRejected) as exc:
            room.apply(COMMANDS[action](arg), who[actor], T0 + 1_000)
        assert exc.value.code == rejected
        assert (room.state.playing, room.state.position_ms, room.state.as_of_ms, room.controls_locked) == before
    else:
        assert [e.kind for e in room.apply(COMMANDS[action](arg), who[actor], T0 + 1_000)] == ["state"]


def test_lock_is_released_when_the_host_is_gone() -> None:
    room, host, guest = make()
    room.apply(Command("lock", on=True), host, T0)
    events = room.leave(host.id, T0 + 1_000)
    assert room.controls_locked is False
    assert [e.kind for e in events] == ["members", "state"] and events[1].data["controls_locked"] is False
    assert [e.kind for e in room.apply(Command("play"), guest, T0 + 2_000)] == ["state"]


@pytest.mark.parametrize(
    ("playing", "steps", "trace", "final"),
    [
        # under two seconds of buffering is nobody's business
        (True, [(1_000, "guest", "buffering", True), (2_900, "", "tick")], [], (True, 0)),
        (
            True,
            [(1_000, "guest", "buffering", True), (2_500, "guest", "buffering", False), (9_000, "", "tick")],
            [],
            (True, 0),
        ),
        # past two seconds the room pauses for everyone and names the member
        (
            True,
            [(1_000, "guest", "buffering", True), (3_000, "", "tick")],
            [("state", False, 3_000, None), ("waiting", "Guest")],
            (False, 3_000),
        ),
        # recovery resumes from the paused position
        (
            True,
            [(1_000, "guest", "buffering", True), (3_000, "", "tick"), (7_000, "guest", "buffering", False)],
            [("state", False, 3_000, None), ("waiting", "Guest"), ("state", True, 3_000, None)],
            (True, 3_000),
        ),
        # so does fifteen seconds of waiting, and that member does not stall the room a second time
        (
            True,
            [
                (1_000, "guest", "buffering", True),
                (3_000, "", "tick"),
                (17_999, "", "tick"),
                (18_000, "", "tick"),
                (19_000, "host", "ping"),
                (19_000, "guest", "ping"),
                (30_000, "", "tick"),
            ],
            [("state", False, 3_000, None), ("waiting", "Guest"), ("state", True, 3_000, None)],
            (True, 3_000),
        ),
        # ... until they have recovered and buffer again
        (
            True,
            [
                (1_000, "guest", "buffering", True),
                (3_000, "", "tick"),
                (18_000, "", "tick"),
                (19_000, "host", "ping"),
                (19_000, "guest", "buffering", False),
                (20_000, "guest", "buffering", True),
                (22_000, "", "tick"),
            ],
            [
                ("state", False, 3_000, None),
                ("waiting", "Guest"),
                ("state", True, 3_000, None),
                ("state", False, 7_000, None),
                ("waiting", "Guest"),
            ],
            (False, 7_000),
        ),
        # a paused room does not wait for anyone; pressing play does, on the next tick
        (False, [(1_000, "guest", "buffering", True), (9_000, "", "tick")], [], (False, 0)),
        (
            False,
            [(1_000, "guest", "buffering", True), (9_000, "host", "play"), (10_000, "", "tick")],
            [("state", True, 0, "Host"), ("state", False, 1_000, None), ("waiting", "Guest")],
            (False, 1_000),
        ),
        # two members buffering: the wait moves to the one still stuck and keeps its original deadline
        (
            True,
            [
                (1_000, "guest", "buffering", True),
                (2_000, "host", "buffering", True),
                (3_000, "", "tick"),
                (6_000, "guest", "buffering", False),
                (18_000, "", "tick"),
            ],
            [("state", False, 3_000, None), ("waiting", "Guest"), ("waiting", "Host"), ("state", True, 3_000, None)],
            (True, 3_000),
        ),
        # a member can overrule the wait: play resumes now, pause turns it into an ordinary pause
        (
            True,
            [(1_000, "guest", "buffering", True), (3_000, "", "tick"), (5_000, "host", "play"), (9_000, "", "tick")],
            [("state", False, 3_000, None), ("waiting", "Guest"), ("state", True, 3_000, "Host")],
            (True, 3_000),
        ),
        (
            True,
            [
                (1_000, "guest", "buffering", True),
                (3_000, "", "tick"),
                (5_000, "host", "pause"),
                (6_000, "guest", "buffering", False),
                (19_000, "host", "ping"),
                (19_000, "guest", "ping"),
                (30_000, "", "tick"),
            ],
            [("state", False, 3_000, None), ("waiting", "Guest"), ("state", False, 3_000, "Host")],
            (False, 3_000),
        ),
        # a seek during the wait moves the position the room will resume from; the wait is restated
        (
            True,
            [
                (1_000, "guest", "buffering", True),
                (3_000, "", "tick"),
                (4_000, "host", "seek", 60_000),
                (5_000, "guest", "buffering", False),
            ],
            [
                ("state", False, 3_000, None),
                ("waiting", "Guest"),
                ("state", False, 60_000, "Host"),
                ("waiting", "Guest"),
                ("state", True, 60_000, None),
            ],
            (True, 60_000),
        ),
        # the member everyone waits for leaves: nothing left to wait for
        (
            True,
            [(1_000, "guest", "buffering", True), (3_000, "", "tick"), (4_000, "guest", "leave")],
            [("state", False, 3_000, None), ("waiting", "Guest"), ("state", True, 3_000, None)],
            (True, 3_000),
        ),
    ],
)
def test_buffering_wait(
    playing: bool, steps: list[tuple[Any, ...]], trace: list[tuple[Any, ...]], final: tuple[Any, ...]
) -> None:
    room, host, guest = make()
    if playing:
        room.apply(Command("play"), host, T0)
    assert run(room, {"host": host, "guest": guest}, steps, {"state", "waiting"}) == trace
    assert (room.state.playing, room.state.position_ms) == final


def test_buffering_is_shown_on_the_member_strip() -> None:
    room, host, guest = make()
    who = {"host": host, "guest": guest}
    steps = [
        (1_000, "guest", "buffering", True),
        (1_200, "guest", "buffering", True),
        (1_500, "guest", "buffering", False),
    ]
    assert run(room, who, steps, {"members"}) == [("members", ("Host", "Guest*")), ("members", ("Host", "Guest"))]


@pytest.mark.parametrize(
    ("steps", "trace", "left"),
    [
        # a member heard from within twenty seconds stays
        ([(19_999, "", "tick")], [], ["Host", "Guest"]),
        ([(15_000, "host", "ping"), (15_000, "guest", "ping"), (34_999, "", "tick")], [], ["Host", "Guest"]),
        # twenty seconds of silence: told why, dropped, and the others see the new member list
        (
            [(15_000, "host", "ping"), (20_000, "", "tick")],
            [("closed", "silent", "guest"), ("members", ("Host",))],
            ["Host"],
        ),
        # commands and buffering reports count as being heard from
        (
            [(15_000, "host", "play"), (15_000, "guest", "buffering", False), (34_999, "", "tick")],
            [],
            ["Host", "Guest"],
        ),
        ([(20_000, "", "tick")], [("closed", "silent", "host"), ("closed", "silent", "guest")], []),
    ],
)
def test_silent_members_are_dropped(
    steps: list[tuple[Any, ...]], trace: list[tuple[Any, ...]], left: list[str]
) -> None:
    room, host, guest = make()
    host.id, guest.id = "host", "guest"
    room.members = {"host": host, "guest": guest}
    assert run(room, {"host": host, "guest": guest}, steps, {"closed", "members"}) == trace
    assert [m.name for m in room.members.values()] == left


def test_room_pauses_when_the_last_member_leaves() -> None:
    room, host, guest = make()
    room.apply(Command("play"), host, T0)
    room.leave(guest.id, T0 + 1_000)
    assert room.state.playing is True
    room.leave(host.id, T0 + 5_000)
    assert (room.state.playing, room.state.position_ms) == (False, 5_000)


@pytest.mark.parametrize(
    ("steps", "closed"),
    [
        ([(R.EMPTY_TTL_MS - 1, "tick")], False),  # never joined: counted from creation
        ([(R.EMPTY_TTL_MS, "tick")], True),
        # counted from the moment the last member left, not from creation
        ([(600_000, "join"), (900_000, "leave"), (900_000 + R.EMPTY_TTL_MS - 1, "tick")], False),
        ([(600_000, "join"), (900_000, "leave"), (900_000 + R.EMPTY_TTL_MS, "tick")], True),
        # an occupied room never expires, however long it runs
        ([(0, "join"), (10 * R.EMPTY_TTL_MS, "ping"), (10 * R.EMPTY_TTL_MS, "tick")], False),
    ],
)
def test_empty_room_expires_after_thirty_minutes(steps: list[tuple[int, str]], closed: bool) -> None:
    room = Room("ABCDEF", "lib:s:1", host_profile_id="host", host_name="Host", now_ms=T0)
    member: Member | None = None
    events: list[Event] = []
    for offset, action in steps:
        now = T0 + offset
        if action == "join":
            member, _ = room.join("host", "Host", now)
        elif action == "tick":
            events = room.tick(now)
        else:
            assert member is not None
            if action == "leave":
                room.leave(member.id, now)
            else:
                room.touch(member, now)
    assert room.closed is closed
    assert [brief(e) for e in events if e.kind == "closed"] == ([("closed", "expired", None)] if closed else [])
    if closed:
        assert room.tick(T0 + 99 * R.EMPTY_TTL_MS) == []  # a closed room says nothing more


@pytest.mark.parametrize(
    ("action", "arg", "expected"),
    [
        ("pause", None, ("state", False, None, "Guest")),
        ("seek", 30_000, "live_no_seek"),
    ],
)
def test_live_rooms_have_no_position(action: str, arg: Any, expected: Any) -> None:
    room, host, guest = make(live=True)
    who = {"host": host, "guest": guest}
    if isinstance(expected, str):
        with pytest.raises(CommandRejected) as exc:
            run(room, who, [(1_000, "guest", action, arg)], {"state"})
        assert exc.value.code == expected
    else:
        assert run(room, who, [(1_000, "guest", action, arg)], {"state"}) == [expected]
    # the buffering wait still applies: the flag flips, there is still no position
    room.apply(Command("play"), host, T0 + 2_000)
    steps = [(3_000, "guest", "buffering", True), (5_000, "", "tick"), (6_000, "guest", "buffering", False)]
    assert run(room, who, steps, {"state", "waiting"}) == [
        ("state", False, None, None),
        ("waiting", "Guest"),
        ("state", True, None, None),
    ]


def test_snapshot_tells_a_newcomer_everything() -> None:
    room, host, guest = make()
    steps = [(0, "host", "play"), (1_000, "guest", "buffering", True), (3_000, "", "tick")]
    run(room, {"host": host, "guest": guest}, steps, set())
    events = room.snapshot(host)
    assert [(e.kind, e.to) for e in events] == [("members", host.id), ("state", host.id), ("waiting", host.id)]
    assert events[1].message() == {
        "type": "state",
        "playing": False,
        "position_ms": 3_000,
        "as_of_ms": T0 + 3_000,
        "by": None,
        "controls_locked": False,
    }
    assert events[0].message()["members"] == [
        {"id": host.id, "name": "Host", "host": True, "buffering": False},
        {"id": guest.id, "name": "Guest", "host": False, "buffering": True},
    ]


def test_room_holds_at_most_eight_members() -> None:
    room = Room("ABCDEF", "lib:s:1", host_profile_id="host", host_name="Host", now_ms=T0)
    for n in range(R.MAX_MEMBERS):
        member, events = room.join(f"p{n}", f"Member {n}", T0)
        assert [e.kind for e in events] == ["members"] and len(events[0].data["members"]) == n + 1
    assert R.MAX_MEMBERS == 8 and room.full
    with pytest.raises(Conflict) as exc:
        room.join("late", "Late", T0)
    assert exc.value.code == "room_full" and len(room.members) == 8
    room.leave(member.id, T0)
    room.join("late", "Late", T0)


def test_registry_codes_caps_and_removal() -> None:
    reg = RoomRegistry()
    rooms = [reg.create(f"lib:s:{n}", host_profile_id="host", host_name="Host", now_ms=T0) for n in range(R.MAX_ROOMS)]
    assert R.MAX_ROOMS == 20 and len(reg) == 20 and len({r.code for r in rooms}) == 20
    for r in rooms:
        assert len(r.code) == 6 and set(r.code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    with pytest.raises(Conflict) as exc:
        reg.create("lib:s:x", host_profile_id="host", host_name="Host", now_ms=T0)
    assert exc.value.code == "room_limit"
    first = rooms[0]
    assert reg.get(first.code) is first and reg.get(first.code.lower()) is first and reg.get("ZZZZZZ9") is None
    events = reg.remove(first.code, "deleted")
    assert [brief(e) for e in events] == [("closed", "deleted", None)] and first.closed
    assert reg.get(first.code) is None and reg.remove(first.code) == [] and len(reg) == 19
    reg.create("lib:s:x", host_profile_id="host", host_name="Host", now_ms=T0)


def test_registry_never_hands_out_a_code_in_use() -> None:
    picks = iter("AAAAAA" + "AAAAAA" + "BBBBBB")
    reg = RoomRegistry(choice=lambda alphabet: next(picks))
    a = reg.create("lib:s:1", host_profile_id="host", host_name="Host", now_ms=T0)
    b = reg.create("lib:s:2", host_profile_id="host", host_name="Host", now_ms=T0)
    assert (a.code, b.code) == ("AAAAAA", "BBBBBB")


def test_registry_sweep_ticks_every_room_and_forgets_closed_ones() -> None:
    reg = RoomRegistry()
    idle = reg.create("lib:s:1", host_profile_id="host", host_name="Host", now_ms=T0)
    busy = reg.create("lib:s:2", host_profile_id="host", host_name="Host", now_ms=T0)
    host, _ = busy.join("host", "Host", T0)
    guest, _ = busy.join("guest", "Guest", T0)
    busy.apply(Command("play"), host, T0)
    busy.member_buffering(guest, True, T0 + 1_000)
    swept = reg.sweep(T0 + 3_000)
    assert [(r.code, [e.kind for e in events]) for r, events in swept] == [(busy.code, ["state", "waiting"])]
    busy.touch(host, T0 + R.EMPTY_TTL_MS)
    busy.touch(guest, T0 + R.EMPTY_TTL_MS)
    swept = reg.sweep(T0 + R.EMPTY_TTL_MS)
    assert (idle.code, ["closed"]) in [(r.code, [e.kind for e in events]) for r, events in swept]
    assert reg.get(idle.code) is None and reg.get(busy.code) is busy and len(reg) == 1
