"""Where a room member's playback descriptor comes from, and the one upstream a room may use.

A title from a source that limits connections (or is always proxied) is played to the whole room
from one HLS session, keyed ``room:<code>``: the first join starts it at the room's position,
later joins attach, and it is released when the last member leaves or the room closes. It holds
exactly one of the source's connections and uses one pinned upstream session (``StreamProxy.pin``
with the room's key), however many members read it.

A seek restarts that session at the new position before the room's ``state`` event goes out.
Every member's playlist URL stays what it was; the ``state`` event carries ``stream_epoch`` and
``stream_start_ms``, and a client that sees a new epoch reloads its playlist, whose time zero is
now ``stream_start_ms``. A seek that lands on the running session's own start does not restart it.

Titles from other sources cost no connection budget: each member gets their own descriptor.
Live channels use the proxy's existing shared upstream, as for any two viewers of one channel.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.proxy.stream_proxy import HlsSession, HlsSourceBusy, StreamProxy, Viewer, VodPlan
from app.services.auth import Principal
from app.services.errors import BadGateway, Conflict, Forbidden, NotFound, ServiceError
from app.services.playback import PlaybackResolver, RoomScope, vod_start_s
from app.services.rooms import Room, position_at
from app.services.streams import open_failed, source_busy
from app.services.visibility import require_watch

log = logging.getLogger(__name__)

AUDIO_FIELDS = ("audio_languages", "audio_verified", "audio_evidence", "default_audio_language")


@dataclass
class RoomSession:
    """The one upstream of a room, and what every member's descriptor says about it."""

    code: str
    ref: str  # what later members resolve: the title, or the episode the first member was given
    variant_id: str
    source_id: str
    url: str
    headers: dict[str, str]
    max_connections: int | None
    video: str  # "copy" | "transcode"
    codec: str | None
    width: int | None
    audio_track: int | None
    tracks: list[dict[str, Any]]
    audio: dict[str, Any]
    duration_ms: int | None
    title: str | None
    kind: str | None
    host_profile_id: str
    start_s: int
    epoch: int = 1  # counts the session's starts; clients reload their playlist when it changes
    since: datetime = field(default_factory=lambda: datetime.now(UTC))
    joining: int = 0  # joins in flight: the session is theirs although the room has no member yet

    @property
    def key(self) -> str:
        return f"room:{self.code}"

    @property
    def sid(self) -> str:
        return StreamProxy.session_id(f"{self.key}:{self.epoch}")


class RoomPlayback:
    def __init__(
        self,
        playback: PlaybackResolver,
        proxy: StreamProxy,
        *,
        db: Any = None,
        sources: Any = None,
        content_policy: Any = None,
        settings: Any = None,
        hub: Any = None,
    ) -> None:
        self._playback = playback
        self.proxy = proxy
        self.db = db
        self.sources = sources
        self.content_policy = content_policy
        self._settings = settings
        self._hub = hub
        self._sessions: dict[str, RoomSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Future[None]] = set()
        if hub is not None:
            hub.before_seek = self.before_seek
            hub.state_extra = self.state_extra
            hub.on_change = self.room_changed

    def session(self, code: str) -> RoomSession | None:
        return self._sessions.get(code)

    def _lock(self, code: str) -> asyncio.Lock:
        return self._locks.setdefault(code, asyncio.Lock())

    async def descriptor_for(
        self,
        room: Room,
        principal: Principal,
        client: str,
        *,
        now_ms: int,
        prefer_hls: bool = False,
        recovery_generation: str | None = None,
        allow_unverified_audio: bool = False,
        https_site: bool = False,
    ) -> dict[str, Any]:
        """The descriptor this member plays the room's title with.

        Every member is resolved as ``POST /playback/resolve`` would resolve them, so each refusal
        (watch permission, rating ceiling, adult, audio selection, unknown variant, stream limit)
        reaches a member who may not watch the title unchanged, and nobody else is disturbed.
        What the resolution yields is the member's own stream, or a seat on the room's one session.
        """
        self.proxy.ensure_running()
        if client == "web":
            if recovery_generation is None:
                raise Forbidden(
                    "Reload Streamhub to update playback recovery support.", code="playback_reload_required"
                )
            if recovery_generation != self.proxy.recovery_epoch:
                raise Forbidden(
                    "This playback attempt was stopped. Start playback again manually.", code="playback_stopped"
                )
        generation = self.proxy.generation
        if self.db is not None:
            await require_watch(self.db, principal.permissions, room.ref)
        position = 0 if room.live else position_at(room.state, now_ms)
        scope = None if room.live else RoomScope(room.code, position)
        # Once the room has its session, everyone resolves the file that session plays.
        held = self._sessions.get(room.code)
        result = await self._playback.resolve(
            principal.profile_id,
            held.ref if held is not None else room.ref,
            variant_id=held.variant_id if held is not None else room.variant_id,
            allow_unverified_audio=allow_unverified_audio,
            client="web_hls" if client == "web" and prefer_hls else client,
            https_site=https_site,
            audio_track=None if room.live else room.audio_track,
            start_position_ms=None if room.live else position,
            room=scope,
        )
        self.proxy.ensure_running(generation)
        if result.get("mode") == "handoff":
            # Another app plays it; there is no player here for the room to steer.
            raise Conflict("This title opens in another app and cannot be watched in a room.", code="room_unsupported")
        result["buffer_available"] = False
        if room.live:
            return result
        # The room's clock decides where a member starts, not their own history.
        result["resume_position_ms"] = position
        if scope is None or not scope.shared or scope.vod is None:
            return result
        return await self._seat(room, principal, scope, result)

    async def _seat(self, room: Room, principal: Principal, scope: RoomScope, result: dict[str, Any]) -> dict[str, Any]:
        """Attach a resolved member to the room's session, starting it for the first one."""
        vod = scope.vod
        assert vod is not None
        async with self._lock(room.code):
            session = self._sessions.get(room.code)
            created = session is None
            if session is None:
                session = self._sessions[room.code] = RoomSession(
                    code=room.code,
                    ref=str(result["id"]),
                    variant_id=scope.variant_id,
                    source_id=scope.source_id,
                    url=scope.url,
                    headers=scope.headers,
                    max_connections=scope.max_connections,
                    video=vod.video or "copy",
                    codec=vod.codec,
                    width=vod.width,
                    audio_track=vod.audio_track,
                    tracks=vod.tracks,
                    audio={k: result[k] for k in AUDIO_FIELDS if k in result},
                    duration_ms=vod.duration_ms,
                    title=scope.title,
                    kind=scope.kind,
                    host_profile_id=room.host_profile_id,
                    start_s=vod.start_s,
                )
                self.proxy.keep(session.key)
            session.joining += 1
            try:
                await self._start(session)
            except BaseException as exc:
                session.joining -= 1
                if created:
                    await asyncio.shield(self._drop(session))
                else:
                    self.room_changed(room)  # whoever is left decides whether the session stays
                raise await self._refusal(session, principal, exc) from exc
            session.joining -= 1
            if room.closed:  # closed while the title was being resolved: nobody will come for it
                self.room_changed(room)
        result.update(session.audio)
        result.update(
            audio_tracks=session.tracks,
            audio_track=session.audio_track,
            start_position_ms=session.start_s * 1000,
            duration_ms=session.duration_ms,
            stream_epoch=session.epoch,
        )
        return result

    async def _refusal(self, session: RoomSession, principal: Principal, exc: BaseException) -> BaseException:
        """What a member is told when the room's session did not start: the answers of ``/stream/{token}``."""
        source, profile = session.source_id, principal.profile_id
        if isinstance(exc, HlsSourceBusy):
            return await source_busy(self, source, profile, "Another stream is using this source. Stop it and retry.")
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return await open_failed(self, source, profile, exc, session.max_connections, "could not start HLS session")
        if isinstance(exc, ServiceError):
            return await source_busy(self, source, profile, exc.message) if exc.code == "source_busy" else exc
        if not isinstance(exc, Exception):
            return exc  # cancellation
        log.warning("room session failed", exc_info=exc, extra={"err": type(exc).__name__})
        return BadGateway("could not start HLS session")

    def _viewer(self, session: RoomSession) -> Viewer:
        """The room as the source's ``source_busy`` answer names it."""
        title = f"Watch room: {session.title}" if session.title else None
        return Viewer(session.host_profile_id, title, session.kind, session.variant_id, since=session.since)

    async def _start(self, session: RoomSession) -> HlsSession:
        """The room's session, started at its current offset if it is not running. Caller holds the room's lock."""
        hw = str(self._settings.get("transcode_hw") or "none") if self._settings is not None else "none"
        plan = VodPlan(
            video=session.video,
            hw=hw if session.video == "transcode" else "none",
            codec=session.codec,
            width=session.width,
            audio_track=session.audio_track,
            start_s=session.start_s,
            family=session.key,
        )
        return await self.proxy.hls_session(
            session.key,
            session.url,
            session.headers,
            live=False,
            transcode="none",
            source_id=session.source_id,
            max_connections=session.max_connections,
            vod=plan,
            viewer=self._viewer(session),
            pin_key=session.key,
            sid=session.sid,
        )

    # ------------------------------------------------------------------ the stream routes
    def _granted(self, grant: Any) -> RoomSession:
        code = str(grant.room or "")
        room = self._hub.registry.get(code) if self._hub is not None else None
        session = self._sessions.get(code)
        if room is None or room.closed or session is None:
            raise NotFound("This room no longer exists.", code="room_not_found")
        return session

    async def open(self, grant: Any) -> HlsSession:
        """The session a member's playlist request reads; started again if ffmpeg died under the room."""
        session = self._granted(grant)
        async with self._lock(session.code):
            return await self._start(self._granted(grant))

    def sid(self, grant: Any) -> str:
        return self._granted(grant).sid

    # ------------------------------------------------------------------ the room's hooks
    def state_extra(self, room: Room) -> dict[str, Any]:
        session = self._sessions.get(room.code)
        if session is None:
            return {}
        return {"stream_epoch": session.epoch, "stream_start_ms": session.start_s * 1000}

    def before_seek(self, room: Room) -> Awaitable[None] | None:
        return self._reposition(room) if room.code in self._sessions else None

    async def _reposition(self, room: Room) -> None:
        async with self._lock(room.code):
            session = self._sessions.get(room.code)
            if session is None:
                return
            now_ms = self._hub.clock() if self._hub is not None else room.state.as_of_ms
            start_s = vod_start_s(position_at(room.state, now_ms), session.duration_ms)
            if start_s == session.start_s and self.proxy.hls_running(session.key) is not None:
                return
            # The new offset first: a playlist request that slips in must not restart the old one.
            session.start_s, session.epoch = start_s, session.epoch + 1
            await self.proxy.stop_hls(session.key)
            await self._start(session)

    def room_changed(self, room: Room) -> None:
        session = self._sessions.get(room.code)
        if session is not None and not session.joining and (room.closed or not room.members):
            self._spawn(self._release(room))

    def _spawn(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _release(self, room: Room) -> None:
        async with self._lock(room.code):
            session = self._sessions.get(room.code)
            # Looked at again under the lock: a join may have taken the session over meanwhile.
            if session is None or session.joining or not (room.closed or not room.members):
                return
            await self._drop(session)

    async def _drop(self, session: RoomSession) -> None:
        if self._sessions.get(session.code) is session:
            del self._sessions[session.code]
        try:
            await self.proxy.stop_hls(session.key, forget=True)
        except Exception:
            log.warning("room session not stopped", exc_info=True, extra={"room": session.code})

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
