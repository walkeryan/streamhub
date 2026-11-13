"""Who is streaming what, and the check every ``/stream/{token}`` request passes.

``StreamRegistry`` keeps one record per (user, signed-in device, title) from the moment playback
is resolved. A record is *active* while it is younger than ``RECENT_S`` or showed activity in the
last ``RECENT_S``: a playlist, segment or bytes served through the proxy, or a progress report from
the player (the only sign of life of a stream the client plays straight from its source). The
user's ``max_streams`` limit counts active records, ``SessionsService.now_playing`` lists them.

A device plays one thing at a time, and players ask for the next title before they report the
previous one stopped. So a resolve is only refused for streams on the user's *other* devices; on
the same device the new stream takes the place of the oldest one when the limit would be exceeded,
and the replaced stream's grant stops being served. Two panes of a multiview are two streams.

``GrantGuard`` re-validates the viewer behind a grant: session not revoked, user active, user
version current (``AuthService.validate``). A positive answer is reused for ``VALID_TTL_S``, so a
revocation or suspension cuts a running stream within that time plus one segment or one second of
passthrough. What the routes need to know about that viewer (may they be served away from home,
are they under a rating ceiling) is remembered with the answer, so no request asks twice.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.infra.models import Profile, utcnow
from app.proxy.stream_proxy import UpstreamRefused
from app.services.content_policy import restricts
from app.services.errors import BadGateway, Conflict, ServiceError, Unauthorized

RECENT_S = 30.0
# A record nobody touched for this long is dropped; its grant re-creates it if it comes back.
FORGET_S = 600.0
VALID_TTL_S = 3.0
GUARD_INTERVAL_S = 1.0
MAX_VALID = 4096

# How a source turns away one connection too many: forbidden, throttled, unavailable, or it just
# hangs up (None). ffmpeg names 429 and 5xx only by their class on some versions.
LIMIT_REFUSALS = {None, "403", "429", "503", "4XX", "5XX"}

Key = tuple[str, str, str]  # user id, auth session id, content ref

# Streams one resolve asks for at once: 2 while the panes of a multiview are being resolved.
_slots: ContextVar[int] = ContextVar("stream_slots", default=1)
# The id the client asked to play; its progress reports name that, not what it resolved to.
requested_ref: ContextVar[str | None] = ContextVar("requested_ref", default=None)


def is_recording(grant: Any) -> bool:
    """A grant for a finished recording (``rec:<id>``): a file of this server's, not a source's stream."""
    return grant.kind == "recording" and str(grant.content_ref or "").startswith("rec:")


@contextmanager
def panes(count: int) -> Iterator[None]:
    """Resolve ``count`` streams that play side by side on one device."""
    token = _slots.set(count)
    try:
        yield
    finally:
        _slots.reset(token)


async def source_busy(
    c: Any, source_id: str, profile_id: str, message: str = "Source connection limit is occupied."
) -> Conflict:
    """``source_busy`` for the profile that was turned away, naming who holds the source's connections.

    ``active`` lists the streams this server is running on the source: the profile's name, what it
    is watching and since when. A title the refused profile may not open itself is left unnamed.
    ``c`` is the container, or anything with its ``db``, ``proxy``, ``sources`` and ``content_policy``.
    """
    source = await c.sources.get(source_id)
    viewers = c.proxy.active_viewers(source_id)
    names: dict[str, str] = {}
    if viewers:
        async with c.db.session() as session:
            wanted = {v.profile_id for v in viewers}
            rows = (await session.execute(select(Profile).where(Profile.id.in_(wanted)))).scalars()
            names = {row.id: row.name for row in rows}
    active = []
    for viewer in viewers:
        named = viewer.title is not None
        if named and viewer.ref and viewer.profile_id != profile_id:
            try:
                await c.content_policy.require(profile_id, viewer.ref)
            except ServiceError:
                named = False
        active.append(
            {
                "profile_name": names.get(viewer.profile_id),
                "title": viewer.title if named else None,
                "kind": viewer.kind if named else None,
                "since": viewer.since.isoformat(),
            }
        )
    limit = c.sources.max_connections(source)
    return Conflict(message, code="source_busy", extra={"active": active, "limit": limit})


async def open_failed(
    c: Any, source_id: str, profile_id: str, exc: Exception, maximum: int | None, message: str
) -> ServiceError:
    """The error for an upstream that did not open: ``source_busy`` when the source's limit explains it."""
    if (
        isinstance(exc, UpstreamRefused)
        and exc.status in LIMIT_REFUSALS
        and c.proxy.refused_at_limit(source_id, maximum)
    ):
        return await source_busy(c, source_id, profile_id, "The source refused another connection.")
    return BadGateway(message)


@dataclass(slots=True)
class StreamRecord:
    user_id: str
    profile_id: str
    session_id: str
    ref: str
    title: str | None  # absent for adult titles, as in the grant
    kind: str | None
    client: str | None
    since: datetime
    started: float
    seen: float
    aliases: set[str] = field(default_factory=set)
    # A newer stream on the same device took this one's place under the user's limit.
    replaced: bool = False

    @property
    def key(self) -> Key:
        return (self.user_id, self.session_id, self.ref)


class StreamRegistry:
    def __init__(self, db: Any = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._db = db
        self.clock = clock
        self._records: dict[Key, StreamRecord] = {}
        # Streams that are not playback, by user id: recordings holding a source connection of their own.
        self.external: list[Callable[[str], list[StreamRecord]]] = []

    def _live(self, record: StreamRecord, now: float) -> bool:
        return not record.replaced and (now - record.started < RECENT_S or now - record.seen < RECENT_S)

    def _prune(self, now: float) -> None:
        if len(self._records) > 256:
            self._records = {k: r for k, r in self._records.items() if now - max(r.started, r.seen) < FORGET_S}

    def active(self, user_id: str) -> list[StreamRecord]:
        """The user's streams with recent activity, longest-running first."""
        now = self.clock()
        found = [r for r in self._records.values() if r.user_id == user_id and self._live(r, now)]
        for source in self.external:
            found += source(user_id)
        return sorted(found, key=lambda r: r.since)

    async def conflict(self, active: list[StreamRecord], limit: int) -> Conflict:
        """``user_stream_limit``, naming what is already playing in the shape ``source_busy`` uses."""
        names: dict[str, str] = {}
        if self._db is not None and active:
            async with self._db.session() as s:
                wanted = {r.profile_id for r in active}
                rows = await s.execute(select(Profile.id, Profile.name).where(Profile.id.in_(wanted)))
                names = {pid: name for pid, name in rows.all()}
        return self._conflict(active, limit, names)

    @staticmethod
    def _conflict(active: list[StreamRecord], limit: int, names: dict[str, str]) -> Conflict:
        listed = [
            {"profile_name": names.get(r.profile_id), "title": r.title, "kind": r.kind, "since": r.since.isoformat()}
            for r in active
        ]
        plural = "stream" if limit == 1 else "streams"
        return Conflict(
            f"This account can play {limit} {plural} at a time. Stop another one first.",
            code="user_stream_limit",
            extra={"active": listed, "limit": limit},
        )

    async def require_slot(self, user_id: str, session_id: str, limit: int | None, *, slots: int = 1) -> None:
        """409 when the user's other devices already use every stream the limit allows."""
        if not limit or not user_id:
            return
        elsewhere = [r for r in self.active(user_id) if r.session_id != session_id]
        if len(elsewhere) + max(slots, _slots.get()) > limit:
            raise await self.conflict(elsewhere, limit)

    def admit(
        self,
        user_id: str,
        profile_id: str,
        session_id: str,
        ref: str,
        *,
        title: str | None,
        kind: str | None,
        client: str | None,
        limit: int | None,
    ) -> StreamRecord:
        """Record a resolved stream. Resolving the same title on the same device again is the same stream."""
        now = self.clock()
        self._prune(now)
        key = (user_id, session_id, ref)
        others = [r for r in self.active(user_id) if r.key != key]
        slots = _slots.get()
        if limit:
            elsewhere = [r for r in others if r.session_id != session_id]
            if len(elsewhere) + slots > limit:  # lost a race since ``require_slot``
                raise self._conflict(elsewhere, limit, {})
        record = self._records.get(key)
        if record is None or not self._live(record, now):
            record = StreamRecord(user_id, profile_id, session_id, ref, title, kind, client, utcnow(), now, now)
            self._records[key] = record
        record.title, record.kind, record.client, record.seen = title, kind, client, now
        alias = requested_ref.get()
        if alias and alias != ref:
            record.aliases.add(alias)
        if limit and slots == 1:
            # What this device was playing before gives way, oldest first.
            here = [r for r in others if r.session_id == session_id and r.key in self._records]
            for old in here[: max(0, len(others) + 1 - limit)]:
                old.replaced = True
        return record

    def touch(self, user_id: str, grant: Any, limit: int | None) -> list[StreamRecord] | None:
        """Note activity on a grant's stream. Returns what blocks it when it may not (re)start, else None.

        A stream that went quiet (paused, or replaced on its device) gave its place up; it only
        comes back while the user is under the limit.
        """
        now = self.clock()
        key = (user_id, str(grant.session_id or ""), str(grant.content_ref or ""))
        record = self._records.get(key)
        if record is not None and self._live(record, now):
            record.seen = now
            return None
        if limit:
            others = [r for r in self.active(user_id) if r.key != key]
            if len(others) >= limit:
                return others
        if record is None:
            record = StreamRecord(
                user_id, grant.profile_id, key[1], key[2], grant.title, grant.kind, grant.client, utcnow(), now, now
            )
            self._records[key] = record
        record.replaced, record.seen = False, now
        return None

    def _named(self, user_id: str, session_id: str, ref: str) -> list[StreamRecord]:
        return [
            r
            for r in self._records.values()
            if r.user_id == user_id and r.session_id == session_id and (r.ref == ref or ref in r.aliases)
        ]

    def heartbeat(self, user_id: str, session_id: str, ref: str) -> None:
        """A player reported progress: its stream is alive even when no bytes pass through this server."""
        now = self.clock()
        for record in self._named(user_id, session_id, ref):
            if not record.replaced:
                record.seen = now

    def release(self, user_id: str, session_id: str, ref: str) -> None:
        """The player stopped: the stream no longer counts."""
        for record in self._named(user_id, session_id, ref):
            self._records.pop(record.key, None)

    def now_playing(self, user_id: str) -> list[dict[str, Any]]:
        return [
            {"id": r.ref, "title": r.title, "kind": r.kind, "since": r.since, "client": r.client}
            for r in self.active(user_id)
        ]


@dataclass(frozen=True, slots=True)
class Validated:
    """A viewer the guard validated, as of ``at``. Any change to these moves the user's version."""

    at: float
    user_id: str
    max_streams: int | None
    remote: bool  # holds ``remote.access``: served away from the home network too
    rated: bool  # under a rating ceiling: every segment is checked, and nothing sent is cacheable


class GrantGuard:
    """The viewer check and the content policy, for every request made with a stream grant."""

    def __init__(
        self, auth: Any, content_policy: Any, streams: StreamRegistry, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._auth = auth
        self._policy = content_policy
        self._streams = streams
        self.clock = clock
        # (profile, profile version, session, user version) -> the last positive answer
        self._valid: dict[tuple[str, int, str, int], Validated] = {}

    async def viewer(self, grant: Any) -> Validated:
        """401 unless the session, the user and the user version the grant was issued to still stand."""
        if not grant.session_id or grant.profile_version is None or grant.user_version is None:
            raise Unauthorized("Start playback again.", code="session_revoked")
        key = (str(grant.profile_id), int(grant.profile_version), str(grant.session_id), int(grant.user_version))
        now = self.clock()
        held = self._valid.get(key)
        if held is None or now - held.at >= VALID_TTL_S:
            self._valid.pop(key, None)
            state = await self._auth.validate(*key)
            user = state.identity.user
            if grant.user_id and grant.user_id != user.id:
                raise Unauthorized("Start playback again.", code="session_revoked")
            if len(self._valid) >= MAX_VALID:
                self._valid = {k: v for k, v in self._valid.items() if now - v.at < VALID_TTL_S}
            identity = state.identity
            held = self._valid[key] = Validated(
                now,
                str(user.id),
                identity.limits.max_streams,
                "remote.access" in identity.permissions,
                restricts(identity.limits),
            )
        blocking = self._streams.touch(held.user_id, grant, held.max_streams)
        if blocking is not None:
            raise await self._streams.conflict(blocking, held.max_streams or 0)
        return held

    async def require_grant(self, grant: Any) -> Validated:
        viewer = await self.viewer(grant)
        if is_recording(grant):
            # No source stands behind a recording. Who may see it was decided when the grant was
            # issued, and a change to that moves the user's version, which was just checked.
            if self._policy.enabled:
                await self._policy.require(grant.profile_id, grant.content_ref)
            return viewer
        await self._policy.require_grant(grant, self._auth)
        return viewer

    async def guard_stream(self, grant: Any, stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """Pass ``stream`` on, checking the grant again about once a second while bytes flow."""
        last_check: float | None = None
        try:
            async for chunk in stream:
                now = self.clock()
                if last_check is None or now - last_check >= GUARD_INTERVAL_S:
                    await self.require_grant(grant)
                    last_check = now
                yield chunk
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    async def require_segment(self, grant: Any, path: Path, manifest: Path) -> Validated:
        if is_recording(grant):
            return await self.require_grant(grant)
        viewer = await self.viewer(grant)
        await self._policy.require_segment(grant, self._auth, path, manifest)
        return viewer
