# Phase 5: Watch Rooms — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two or more signed-in people watch the same title or channel in sync from different places, and a room never uses more than one connection of a limited source.

**Architecture:** A room is a server-side object with an authoritative playback clock. Members hold a WebSocket; any member's play, pause or seek becomes the room state, which every client steers to. For sources with a connection limit the room owns one HLS session that all members read, so the upstream is opened once; library titles stream per member because they cost no source budget. Rooms live in memory (the server runs a single worker) and disappear when empty or idle.

**Tech Stack:** FastAPI WebSockets, the existing HLS session manager in `proxy/stream_proxy.py`, hls.js on web, Media3 on TV.

## Global Constraints

- One upstream connection per room for limited sources, regardless of member count; a seek restarts that one session for everyone.
- Server time is the only clock. Clients measure their offset with ping/pong and never trust each other.
- A member more than 1 000 ms from the room position seeks; smaller errors are left alone (no speed nudging in this release).
- Rooms hold at most 8 members, expire after 30 minutes with no members, and are capped at 20 concurrent rooms.
- Creating needs `rooms.create`, joining `rooms.join`; the title must be one every member is allowed to watch (rating ceiling, adult, `watch.*`): a member who may not watch it is refused with the normal 403.
- No chat, voice or reactions.
- Tests first; all server and web checks pass; contract updated.

---

### Task 1: Room core (pure logic)
**Files:** create `hub-server/app/services/rooms.py`, `tests/test_rooms.py`.

```python
@dataclass
class RoomState: playing: bool; position_ms: int; as_of_ms: int      # server epoch ms when position was true
def position_at(state: RoomState, now_ms: int) -> int
class Room:
    code: str            # 6 chars, unambiguous alphabet (no 0/O/1/I)
    ref: str             # title or channel id
    variant_id: str | None; audio_track: int | None
    host_profile_id: str; controls_locked: bool
    def apply(self, cmd: Command, by: Member, now_ms: int) -> list[Event]
    def member_buffering(self, member, buffering: bool, now_ms: int) -> list[Event]
    def tick(self, now_ms: int) -> list[Event]      # resume after the 15 s wait, expire, drop silent members
class RoomRegistry: create(...), get(code), remove(code), sweep(now_ms)
```
Commands: `play`, `pause`, `seek(position_ms)`, `lock(bool)` (host only). Events: `state`, `members`, `waiting(member_name)`, `closed(reason)`.
Rules: while playing, a member buffering for more than 2 s pauses the room for everyone (`waiting`); when they recover, or after 15 s, the room resumes from the paused position. With `controls_locked`, only the host's commands apply. A member silent for 20 s is dropped. Live channels have no position: state is only `playing`.
- [ ] Table-driven tests with a fake clock for every rule.

### Task 2: API and WebSocket
**Files:** create `hub-server/app/api/routers/rooms.py`; modify router registration, `container.py`, `api/schemas.py`, `contracts/openapi.yaml`; tests `tests/test_api_rooms.py`.

- `POST /rooms {id, variant_id?, audio_track?}` → `{code, join_path: "/room/<code>", ...room}`.
- `GET /rooms/{code}` → title summary, host name, member names, state (for the join screen).
- `POST /rooms/{code}/join {client}` → `{member_token, descriptor}`: a playback descriptor valid only for this room and member.
- `DELETE /rooms/{code}` (host or `users.manage`).
- `WS /rooms/{code}/ws?member_token=…`: client → `ping{t}`, `play`, `pause`, `seek{position_ms}`, `buffering{on}`, `lock{on}`; server → `pong{t, server_ms}`, `state{playing, position_ms, as_of_ms, by}`, `members[...]`, `waiting{name}`, `closed{reason}`.
- Authorization through one module `services/room_policy.py` (`can_create(p)`, `can_join(p)`) so the permission system can back it.
- [ ] Tests with Starlette's WebSocket test client: two members, play/pause/seek fan-out, lock, buffering wait and timeout, expiry, refusal for a member not allowed to watch the title.

### Task 3: One upstream per room
**Files:** `hub-server/app/proxy/stream_proxy.py`, `services/playback.py`, `api/routers/playback.py`; tests.

- For a source with a connection limit (or `proxy: always`), `join` returns an HLS descriptor whose session key is `room:<code>`; the first member starts it at the room position, later members attach to the same session. A room seek stops the session and starts a new one at the new position; clients reload the playlist on the `state` event that follows.
- The room session counts as one admission against the source and is tagged so `source_busy` reports "Watch room: <title>".
- Library sources: `join` returns each member's normal descriptor.
- Live channels: `join` returns the existing shared-upstream descriptor.
- When the last member leaves, the session is released.
- [ ] Tests: two joins → one admission; seek → one restart; last leave → released; library title → no admission.

### Task 4: Web
- Title page and player menu: **Watch together** → creates a room, shows the link, a QR code and the code; **Copy link**.
- `/room/:code` join screen (title, who is in the room, Join); requires sign-in, then returns to the room.
- Room player: the normal player with transport controls wired to the socket; a member strip (names, who is buffering); "Waiting for <name>…" overlay; host toggle "Only I can control"; leaving returns to the title.
- Drift correction per Global Constraints; reconnect with backoff and state resync.
- Playwright: two browser contexts against a deterministic test stream, asserting both positions stay within one second across play, pause and seek.

### Task 5: TV
- Details screen: **Watch together** (create; shows code and QR). Rail or Search: **Join a room** with a six-character D-pad entry.
- Player in room mode sends commands to the socket instead of acting locally and applies room state; a "Waiting for <name>…" banner; Back leaves the room after confirmation.
- Unit tests for the room client state machine (offset calculation, drift decision, reconnect).

### Verification
- [ ] Acceptance scenario 3 of the design on the dev instance: one browser on the LAN and one remote browser on an IPTV film, with the source showing a single active connection.
