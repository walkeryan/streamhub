"""RequestService: a viewer asks for a title no source has.

StreamHub owns the request record and its approval state; the request manager is only
the downstream executor.

    pending_approval → approved → sent → available
                    ↘ declined           ↘ declined | failed   (reported by the manager)

``approved → sent`` happens in the same call when the manager accepts the request. A
manager error leaves the request ``approved`` with the error in ``note`` and
``reconcile`` retries. ``reconcile`` also closes any open request once an in-app source
has the title, and tells the requester.

Beside its state every request has a ``progress``: what the manager last said is happening
to the title (``request_progress`` has the rules). Who asks the manager, and how often:

- ``poll_due``, every ``FAST_S`` seconds, asks about the sent requests whose own interval
  has passed: ``FAST_S`` while downloading or importing, ``SLOW_S`` otherwise (``FRESH_S``
  for the first minutes after sending);
- ``reconcile``, every 5 minutes and after each catalog sync, asks about all of them;
- reading ``/requests`` or one request asks about the stale ones among those it returns,
  at most ``READ_REFRESH_MAX`` of them, and never waits longer than
  ``READ_REFRESH_TIMEOUT_S`` for it. Tiles only ever serve what is stored.

All three go through ``_poll``: one pass at a time, ``POLL_CONCURRENCY`` calls at once, and
none at all while the manager is known to be unreachable.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import secrets
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.adapters.seerr import ManagerSnapshot, SeerrError
from app.domain.ports import MetadataProvider
from app.infra.db import Database
from app.infra.models import REQUEST_OPEN_STATES, MediaRequest, Profile, utcnow
from app.services import ids, request_policy
from app.services.auth import Principal
from app.services.availability import AvailabilityService
from app.services.errors import BadGateway, Conflict, Forbidden, NotFound, ServiceError
from app.services.notices import new_notice
from app.services.request_progress import (
    OPEN_POLLED_PHASES,
    STALL_AFTER_S,
    Progress,
    backoff_s,
    derive,
    interval_s,
    safe_detail,
)
from app.services.sources import SourceService

log = logging.getLogger(__name__)

STATES = (*REQUEST_OPEN_STATES, "available", "declined", "failed")
NOTE_MAX = 500
LIST_LIMIT = 200

# At most this many calls to the request manager at once, whoever is asking.
POLL_CONCURRENCY = 4
# One scheduled pass asks about at most this many requests; the others are due on the next one.
POLL_BATCH_MAX = 20
# A read refreshes at most this many of the stale requests it returns, and serves what is
# stored if the manager has not answered within this many seconds.
READ_REFRESH_MAX = 8
READ_REFRESH_TIMEOUT_S = 4.0
# "Check again" reaches the manager at most this often per request, whoever presses it.
MANUAL_REFRESH_MIN_S = 5

_STALLED = {
    "downloading": f"No progress for {STALL_AFTER_S // 60} minutes.",
    "importing": f"Downloaded, but not imported for {STALL_AFTER_S // 60} minutes.",
}


class RequestManager(Protocol):
    async def create_request(
        self,
        media_type: Literal["movie", "tv"],
        tmdb_id: int,
        seasons: list[int] | Literal["all"] | None = None,
    ) -> int: ...

    async def request_snapshot(self, request_id: int) -> ManagerSnapshot: ...

    async def title_snapshot(self, media_type: Literal["movie", "tv"], tmdb_id: int) -> ManagerSnapshot: ...

    async def test(self) -> tuple[bool, str]: ...


# Returns a connection to the configured manager, or None while none is configured.
ManagerFactory = Callable[[], AbstractAsyncContextManager[RequestManager] | None]


def hub_id(row: MediaRequest) -> str:
    return ids.tmdb_id(row.media_type, row.tmdb_id)


def _clean_note(note: str | None) -> str | None:
    return (note or "").strip()[:NOTE_MAX] or None


def _stored(row: MediaRequest) -> dict[str, Any]:
    return row.progress if isinstance(row.progress, dict) else {}


def _phase(row: MediaRequest) -> str:
    """The phase a request is in: its state says it, except while it is with the manager."""
    if row.state == "pending_approval":
        return "requested"
    if row.state != "sent":
        return row.state
    phase = _stored(row).get("phase")
    # Sent and not asked about yet: the manager took it, and nothing more is known.
    return phase if phase in OPEN_POLLED_PHASES else "searching"


def _last_phase(row: MediaRequest) -> str:
    """What to remember as the last known phase when the next answer does not say."""
    stored = _stored(row)
    last = stored.get("last_phase") if stored.get("phase") == "unknown" else _phase(row)
    return last if last in OPEN_POLLED_PHASES and last != "unknown" else "searching"


def progress_of(row: MediaRequest) -> dict[str, Any]:
    """``MediaRequest.progress`` of the API."""
    phase = _phase(row)
    out: dict[str, Any] = {"phase": phase, "last_phase": None, "percent": None, "eta": None, "detail": None}
    if row.state == "sent":
        stored = _stored(row)
        out.update({key: stored.get(key) for key in ("last_phase", "percent", "eta", "detail")})
        # When the manager last answered; never, for a request it has not been asked about.
        out["checked_at"] = row.progress_checked_at or row.updated_at
    else:
        if row.state == "approved":
            out["detail"] = safe_detail(row.note, "Not sent to the request manager yet.")
        elif row.state == "failed":
            out["detail"] = safe_detail(row.note, "The request manager could not complete this request.")
        elif row.state == "declined":
            # The decider's own words, or ours when the manager declined it.
            out["detail"] = row.note
        out["checked_at"] = row.updated_at
    return out


def tile_progress_of(row: MediaRequest) -> dict[str, Any]:
    """``Tile.request_progress``: enough for a chip and a bar, nothing a note could carry."""
    return {"phase": _phase(row), "percent": _stored(row).get("percent") if row.state == "sent" else None}


def _clean_seasons(media_type: str, seasons: list[int] | None) -> list[int] | None:
    if media_type != "tv" or not seasons:
        return None
    if any(not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 999 for n in seasons):
        raise ServiceError("seasons must be season numbers", code="invalid_request")
    return sorted(set(seasons))


class RequestService:
    def __init__(
        self,
        db: Database,
        availability: AvailabilityService,
        sources: SourceService,
        meta: MetadataProvider,
        manager: ManagerFactory,
    ) -> None:
        self._db = db
        self._availability = availability
        self._sources = sources
        self._meta = meta
        self._manager = manager
        self.content_policy: Any = None
        self.now: Callable[[], datetime] = utcnow
        # Serialises everything that talks to the manager so a request is sent once.
        self._lock = asyncio.Lock()
        # One pass of status questions at a time; see the module docstring.
        self._poll_lock = asyncio.Lock()
        # Set while the manager does not answer at all: nothing is asked before ``_down_until``.
        self._down_failures = 0
        self._down_until: datetime | None = None
        self._manual: dict[str, datetime] = {}
        self._background: set[asyncio.Task[Any]] = set()

    @property
    def configured(self) -> bool:
        return self._manager() is not None

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[RequestManager | None]:
        connection = self._manager()
        if connection is None:
            yield None
            return
        async with connection as manager:
            yield manager

    # ------------------------------------------------------------------ reads
    async def _get(self, request_id: str) -> MediaRequest:
        async with self._db.session() as s:
            row = await s.get(MediaRequest, request_id)
        if row is None:
            raise NotFound("request not found")
        return row

    async def _open(self, media_type: str, tmdb_id: int) -> MediaRequest | None:
        async with self._db.session() as s:
            return (
                await s.execute(
                    select(MediaRequest).where(
                        MediaRequest.media_type == media_type,
                        MediaRequest.tmdb_id == tmdb_id,
                        MediaRequest.state.in_(REQUEST_OPEN_STATES),
                    )
                )
            ).scalar_one_or_none()

    async def list(
        self, p: Principal, *, scope: Literal["mine", "all"], state: str | None
    ) -> builtins.list[MediaRequest]:
        if scope not in ("mine", "all"):
            raise ServiceError("unknown scope", code="invalid_request")
        if scope == "all" and not request_policy.can_approve(p):
            raise Forbidden("You cannot see other profiles' requests.", code="request_forbidden")
        if state is not None and state not in STATES:
            raise ServiceError("unknown state", code="invalid_request")
        query = select(MediaRequest).order_by(MediaRequest.created_at.desc(), MediaRequest.id).limit(LIST_LIMIT)
        if scope == "mine":
            query = query.where(MediaRequest.profile_id == p.profile_id)
        if state is not None:
            query = query.where(MediaRequest.state == state)
        async with self._db.session() as s:
            return list((await s.execute(query)).scalars())

    async def current_for(self, titles: set[tuple[str, int]]) -> dict[tuple[str, int], MediaRequest]:
        """The open request for each title, else its most recent closed one."""
        if not titles:
            return {}
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(MediaRequest)
                    .where(MediaRequest.tmdb_id.in_({tmdb_id for _, tmdb_id in titles}))
                    .order_by(MediaRequest.created_at, MediaRequest.id)
                )
            ).scalars()
            out: dict[tuple[str, int], MediaRequest] = {}
            for row in rows:
                key = (row.media_type, row.tmdb_id)
                kept = out.get(key)
                if key in titles and not (
                    kept is not None and kept.state in REQUEST_OPEN_STATES and row.state not in REQUEST_OPEN_STATES
                ):
                    out[key] = row
        return out

    async def states_for(self, titles: set[tuple[str, int]]) -> dict[tuple[str, int], str]:
        return {key: row.state for key, row in (await self.current_for(titles)).items()}

    async def state_for(self, media_type: str, tmdb_id: int) -> str | None:
        return (await self.states_for({(media_type, tmdb_id)})).get((media_type, tmdb_id))

    async def to_api(self, rows: builtins.list[MediaRequest]) -> builtins.list[dict[str, Any]]:
        async with self._db.session() as s:
            found = (await s.execute(select(Profile.id, Profile.name))).all() if rows else []
        names = {profile_id: name for profile_id, name in found}
        return [
            {
                "id": r.id,
                "profile_id": r.profile_id,
                "profile_name": names.get(r.profile_id, ""),
                "media_type": r.media_type,
                "tmdb_id": r.tmdb_id,
                "title_id": hub_id(r),
                "title": r.title,
                "year": r.year,
                "poster": r.poster,
                "seasons": r.seasons,
                "state": r.state,
                "progress": progress_of(r),
                "note": r.note,
                "decided_by_profile_id": r.decided_by_profile_id,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]

    async def test_connection(self) -> tuple[bool, str]:
        async with self._connection() as manager:
            if manager is None:
                return False, "The request manager is not configured."
            return await manager.test()

    # ------------------------------------------------------------------ tiles
    async def annotate(self, p: Principal, payload: Any) -> Any:
        """Stamp ``request_state`` / ``request_progress`` / ``requestable`` on every TMDB-identified tile.

        Done on the finished payload, after any listing cache, because request state changes
        independently of the catalog. The progress is the stored one: no tile asks the manager.
        """
        tiles: list[tuple[dict[str, Any], tuple[str, int]]] = []

        def collect(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                parts = str(value.get("id") or "").split(":")
                if (
                    len(parts) == 3
                    and parts[0] == "tmdb"
                    and parts[1] in ("movie", "tv")
                    and parts[2].isdigit()
                    and isinstance(value.get("sources"), list)
                ):
                    tiles.append((value, (parts[1], int(parts[2]))))
                for item in value.values():
                    collect(item)

        collect(payload)
        if not tiles:
            return payload
        current = await self.current_for({key for _, key in tiles})
        allowed = self.configured and request_policy.can_request(p)
        for tile, key in tiles:
            row = current.get(key)
            tile["request_state"] = row.state if row else None
            tile["request_progress"] = tile_progress_of(row) if row else None
            tile["requestable"] = allowed and not any(b.get("playable_in_app") for b in tile["sources"])
        return payload

    async def annotate_detail(self, p: Principal, detail: dict[str, Any]) -> dict[str, Any]:
        """``annotate`` plus, for a requestable series, the seasons a viewer may pick from."""
        await self.annotate(p, detail)
        if detail.get("requestable") and detail.get("kind") == "show":
            try:
                async with asyncio.timeout(8):
                    meta = await self._meta.details("tv", int(str(detail["id"]).rsplit(":", 1)[1]))
            except Exception:
                meta = None
            detail["request_seasons"] = list(meta.season_numbers) if meta else []
        return detail

    # ------------------------------------------------------------------ library
    async def _in_library(self, media_type: str, tmdb_id: int, title: str | None, year: int | None) -> bool:
        from app.services.content_policy import active_policy

        # "Does any source have it" is a household fact: a restricted profile's catalog
        # scope must not make a title look absent and therefore requestable.
        reset = active_policy.set(None)
        try:
            matches = await self._availability.library_matches(
                media_type, tmdb_id=tmdb_id, imdb_id=None, title=title, year=year
            )
            sources = await self._sources.rows_by_id() if matches else {}
        finally:
            active_policy.reset(reset)
        return any(
            (src := sources.get(m.source_id)) is not None and src.enabled and self._sources.registry.is_in_app(src.type)
            for m in matches
        )

    # ------------------------------------------------------------------ transitions
    async def _transition(
        self, row: MediaRequest, expect: tuple[str, ...], state: str, *, notice: str | None = None, **values: Any
    ) -> MediaRequest | None:
        """Compare-and-set on the state so concurrent callers cannot both move a request."""
        async with self._db.session() as s:
            res = await s.execute(
                update(MediaRequest)
                .where(MediaRequest.id == row.id, MediaRequest.state.in_(expect))
                .values(state=state, updated_at=self.now(), **values)
            )
            if not int(getattr(res, "rowcount", 0) or 0):
                return None
            fresh = await s.get(MediaRequest, row.id, populate_existing=True)
            if fresh is None:
                return None
            if notice == "request_available":
                s.add(
                    new_notice(
                        fresh.profile_id, notice, f"{fresh.title} is now available", "Ready to watch.", hub_id(fresh)
                    )
                )
            elif notice == "request_declined":
                s.add(
                    new_notice(
                        fresh.profile_id, notice, f"{fresh.title} was not approved", fresh.note or "", hub_id(fresh)
                    )
                )
            await s.commit()
            return fresh

    async def _send(
        self, row: MediaRequest, manager: RequestManager | None, *, retry: bool
    ) -> tuple[MediaRequest, bool]:
        """approved → sent. Any failure keeps the request approved with the reason in ``note``.

        The flag is False when the manager did not answer at all.
        """
        values: dict[str, Any] = {"note": None} if retry else {}
        try:
            if manager is None:
                raise SeerrError("No request manager is configured.")
            values["manager_request_id"] = await manager.create_request(
                cast(Literal["movie", "tv"], row.media_type), row.tmdb_id, cast("list[int] | None", row.seasons)
            )
        except SeerrError as exc:
            if exc.status != 409:
                log.warning("request not sent", extra={"request": row.id, "status": exc.status})
                kept = await self._transition(row, ("approved",), "approved", note=exc.message[:NOTE_MAX])
                return kept or row, exc.status is not None
            # The manager already tracks this title without telling us which request it is:
            # nothing is left to send, and the library check still closes it.
            values["note"] = "The request manager already had a request for this title."
        return await self._transition(row, ("approved",), "sent", **values) or row, True

    async def _dispatch(self, request_id: str, *, retry: bool = False) -> MediaRequest:
        async with self._lock:
            row = await self._get(request_id)
            if row.state != "approved":
                return row
            async with self._connection() as manager:
                return (await self._send(row, manager, retry=retry))[0]

    # ------------------------------------------------------------------ commands
    async def _details(self, media_type: str, tmdb_id: int) -> Any:
        if not self._meta.enabled:
            raise Conflict("Title metadata is not configured.", code="metadata_unavailable")
        try:
            async with asyncio.timeout(8):
                meta = await self._meta.details(media_type, tmdb_id)
        except Exception as exc:
            raise BadGateway("Title metadata is unavailable.", code="metadata_unavailable") from exc
        if meta is None or meta.media_type != media_type or meta.tmdb_id != tmdb_id:
            raise NotFound("title not found")
        return meta

    async def create(
        self, p: Principal, media_type: str, tmdb_id: int, seasons: builtins.list[int] | None
    ) -> MediaRequest:
        return (await self.create_or_get(p, media_type, tmdb_id, seasons))[0]

    async def create_or_get(
        self, p: Principal, media_type: str, tmdb_id: int, seasons: builtins.list[int] | None
    ) -> tuple[MediaRequest, bool]:
        """The request for this title and whether this call opened it (False: joined an open one)."""
        if not request_policy.can_request(p):
            raise Forbidden("You cannot request titles.", code="request_forbidden")
        if media_type not in ("movie", "tv") or tmdb_id <= 0:
            raise ServiceError("unknown title", code="invalid_request")
        seasons = _clean_seasons(media_type, seasons)
        meta = await self._details(media_type, tmdb_id)
        # Checked before anything about the title is echoed back, including an existing request.
        if self.content_policy is not None and self.content_policy.applies_to(p):
            await self.content_policy.require(p.profile_id, ids.tmdb_id(media_type, tmdb_id))
        existing = await self._open(media_type, tmdb_id)
        if existing is not None:
            return existing, False
        if not self.configured:
            raise Conflict("No request manager is configured.", code="request_manager_unconfigured")
        if await self._in_library(media_type, tmdb_id, meta.title, meta.year):
            raise Conflict("This title is already available.", code="already_available")
        approved = request_policy.auto_approve(p)
        row = MediaRequest(
            id=secrets.token_hex(8),
            profile_id=p.profile_id,
            media_type=media_type,
            tmdb_id=tmdb_id,
            title=meta.title,
            year=meta.year,
            poster=meta.poster,
            seasons=seasons,
            state="approved" if approved else "pending_approval",
            decided_by_profile_id=p.profile_id if approved else None,
        )
        try:
            async with self._db.session() as s:
                s.add(row)
                await s.commit()
        except IntegrityError:
            # Lost a race for the one open request per title.
            winner = await self._open(media_type, tmdb_id)
            if winner is None:
                raise
            return winner, False
        return (await self._dispatch(row.id) if approved else row), True

    async def decide(self, p: Principal, request_id: str, approve: bool, note: str | None) -> MediaRequest:
        if not request_policy.can_approve(p):
            raise Forbidden("You cannot decide requests.", code="request_forbidden")
        row = await self._get(request_id)
        decided = await self._transition(
            row,
            ("pending_approval",),
            "approved" if approve else "declined",
            notice=None if approve else "request_declined",
            decided_by_profile_id=p.profile_id,
            note=_clean_note(note),
        )
        if decided is None:
            raise Conflict("This request was already decided.", code="request_already_decided")
        return await self._dispatch(decided.id) if approve else decided

    # ------------------------------------------------------------------ progress
    def _due(self, row: MediaRequest, now: datetime) -> bool:
        return row.state == "sent" and (row.poll_after is None or row.poll_after <= now)

    def _answered(self, row: MediaRequest, progress: Progress, now: datetime) -> dict[str, Any]:
        """Column values after an answer that leaves the request open."""
        phase, percent, eta, detail = progress.phase, progress.percent, progress.eta, progress.detail
        moved = progress.mark is None or _stored(row).get("mark") != progress.mark
        changed_at = now if moved or row.progress_changed_at is None else row.progress_changed_at
        if progress.stallable and (now - changed_at).total_seconds() >= STALL_AFTER_S:
            phase, percent, eta, detail = "stalled", None, None, _STALLED[progress.phase]
        last = None
        if phase == "unknown":
            last, detail = _last_phase(row), "The request manager's answer could not be read."
        return {
            "progress": {
                "phase": phase,
                "last_phase": last,
                "percent": percent,
                "eta": eta.isoformat() if eta else None,
                "detail": detail,
                "mark": progress.mark,
            },
            "progress_checked_at": now,
            "progress_changed_at": changed_at,
            # ``updated_at`` is when it was sent: nothing else touches a sent request's.
            "poll_after": now + timedelta(seconds=interval_s(phase, (now - row.updated_at).total_seconds())),
            "poll_failures": 0,
        }

    def _unanswered(self, row: MediaRequest, now: datetime, detail: str) -> dict[str, Any]:
        """Column values when the manager did not answer: unknown, remembering the last phase, asked again later."""
        failures = row.poll_failures + 1
        return {
            # The mark stays, so a short outage does not restart the stall clock.
            "progress": {
                "phase": "unknown",
                "last_phase": _last_phase(row),
                "percent": None,
                "eta": None,
                "detail": detail,
                "mark": _stored(row).get("mark"),
            },
            "poll_after": now + timedelta(seconds=backoff_s(failures)),
            "poll_failures": failures,
        }

    async def _store(self, row: MediaRequest, values: dict[str, Any]) -> None:
        async with self._db.session() as s:
            await s.execute(
                update(MediaRequest)
                .where(MediaRequest.id == row.id, MediaRequest.state == "sent")
                # Being asked about is not a change to the request: ``updated_at`` stays.
                .values(updated_at=MediaRequest.updated_at, **values)
            )
            await s.commit()

    async def _poll_one(self, row: MediaRequest, manager: RequestManager) -> tuple[bool, bool]:
        """Ask the manager about one sent request: (its state changed, the manager answered at all)."""
        now = self.now()
        try:
            if row.manager_request_id is not None:
                snapshot = await manager.request_snapshot(row.manager_request_id)
            else:
                # The manager already had the title and named no request: follow its media record.
                snapshot = await manager.title_snapshot(cast(Literal["movie", "tv"], row.media_type), row.tmdb_id)
        except SeerrError as exc:
            if exc.status == 404 and row.manager_request_id is not None:
                note = "The request manager no longer has this request."
                return await self._close(row, "failed", now, note=note), True
            log.info("request status unavailable", extra={"request": row.id, "status": exc.status})
            await self._store(row, self._unanswered(row, now, "The request manager did not answer."))
            return False, exc.status is not None
        progress = derive(snapshot, media_type=row.media_type, seasons=cast("list[int] | None", row.seasons), now=now)
        if progress.phase == "available":
            return await self._close(row, "available", now, notice="request_available"), True
        if progress.phase == "declined":
            note = "Declined in the request manager."
            return await self._close(row, "declined", now, notice="request_declined", note=note), True
        if progress.phase == "failed":
            note = "The request manager could not complete this request."
            return await self._close(row, "failed", now, note=note), True
        await self._store(row, self._answered(row, progress, now))
        return False, True

    async def _close(self, row: MediaRequest, state: str, now: datetime, **values: Any) -> bool:
        """sent → a closed state on the manager's word; from here on the state is the phase."""
        closed: dict[str, Any] = {"progress": None, "progress_checked_at": now, "poll_after": None, "poll_failures": 0}
        return await self._transition(row, ("sent",), state, **closed, **values) is not None

    async def _poll_rows(self, rows: Sequence[MediaRequest], manager: RequestManager) -> int:
        """Ask about each of ``rows``, ``POLL_CONCURRENCY`` at a time. Returns how many changed state."""
        gate = asyncio.Semaphore(POLL_CONCURRENCY)
        changed = 0
        reachable = True

        async def one(row: MediaRequest) -> None:
            nonlocal changed, reachable
            async with gate:
                if not reachable:
                    # Unreachable: do not spend a timeout on every remaining request.
                    await self._store(row, self._unanswered(row, self.now(), "The request manager did not answer."))
                    return
                moved, answered = await self._poll_one(row, manager)
                changed += moved
                reachable = reachable and answered

        # The first goes alone, so a manager that is down costs one timeout and not a batch of them.
        await one(rows[0])
        await asyncio.gather(*(one(row) for row in rows[1:]))
        if reachable:
            self._down_failures, self._down_until = 0, None
        else:
            self._down_failures += 1
            self._down_until = self.now() + timedelta(seconds=backoff_s(self._down_failures))
        return changed

    async def _poll(self, ids: Sequence[str], *, force: bool) -> int:
        """One pass over the sent requests among ``ids``; only the due ones unless ``force``."""
        async with self._poll_lock:
            now = self.now()
            async with self._db.session() as s:
                # Read again under the lock: a pass that just ended may have answered for some of them.
                rows = list(
                    (
                        await s.execute(
                            select(MediaRequest)
                            .where(MediaRequest.id.in_(ids), MediaRequest.state == "sent")
                            .order_by(MediaRequest.created_at, MediaRequest.id)
                        )
                    ).scalars()
                )
            if not force:
                rows = [row for row in rows if self._due(row, now)]
            if not rows:
                return 0
            async with self._connection() as manager:
                if manager is None:
                    for row in rows:
                        await self._store(row, self._unanswered(row, now, "No request manager is configured."))
                    return 0
                return await self._poll_rows(rows, manager)

    def _down(self, now: datetime) -> bool:
        return self._down_until is not None and now < self._down_until

    async def poll_due(self) -> int:
        """The scheduled pass: ask about the sent requests whose interval has passed. Returns the number changed."""
        now = self.now()
        if self._down(now):
            return 0
        async with self._db.session() as s:
            rows = list((await s.execute(select(MediaRequest).where(MediaRequest.state == "sent"))).scalars())
        due = sorted((row for row in rows if self._due(row, now)), key=lambda row: row.poll_after or row.created_at)
        if not due:
            return 0
        asked = {row.id for row in due[:POLL_BATCH_MAX]}
        changed = await self._poll(sorted(asked), force=False)
        if self._down(self.now()):
            # It is down for every request, also for the ones this pass did not ask about.
            for row in rows:
                if row.id not in asked and _phase(row) != "unknown":
                    await self._store(row, self._unanswered(row, now, "The request manager did not answer."))
        return changed

    async def refreshed(self, rows: builtins.list[MediaRequest]) -> builtins.list[MediaRequest]:
        """``rows`` for a reader, after one bounded refresh of the stale ones among them.

        Never one call per row: at most ``READ_REFRESH_MAX`` requests are asked about, downloads
        first, and whatever the manager has not said within ``READ_REFRESH_TIMEOUT_S`` is served
        as stored. A pass that is already running is not waited for either.
        """
        now = self.now()
        stale = [row for row in rows if self._due(row, now)]
        if not stale or self._down(now) or self._poll_lock.locked():
            return rows
        stale.sort(key=lambda row: (interval_s(_phase(row)), row.poll_after or row.created_at))
        ids = [row.id for row in stale[:READ_REFRESH_MAX]]
        task = asyncio.create_task(self._poll(ids, force=False))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        # Waited for, not awaited: a slow manager holds up the pass, which still finishes, and not the reader.
        done, _ = await asyncio.wait({task}, timeout=READ_REFRESH_TIMEOUT_S)
        if task in done:
            task.result()
        async with self._db.session() as s:
            fresh = {
                row.id: row for row in (await s.execute(select(MediaRequest).where(MediaRequest.id.in_(ids)))).scalars()
            }
        return [fresh.get(row.id, row) for row in rows]

    async def get(self, p: Principal, request_id: str) -> MediaRequest:
        """One request, for its requester or for whoever may approve requests."""
        row = await self._get(request_id)
        if row.profile_id != p.profile_id and not request_policy.can_approve(p):
            raise Forbidden("You cannot see other profiles' requests.", code="request_forbidden")
        return row

    async def refresh(self, p: Principal, request_id: str) -> MediaRequest:
        """Ask the manager about one request now ("Check again"), or retry sending an approved one."""
        row = await self.get(p, request_id)
        now = self.now()
        last = self._manual.get(row.id)
        if row.state not in ("approved", "sent") or (
            last is not None and (now - last).total_seconds() < MANUAL_REFRESH_MIN_S
        ):
            return row
        if len(self._manual) > 500:
            self._manual.clear()
        self._manual[row.id] = now
        if row.state == "approved":
            return await self._dispatch(row.id, retry=True)
        await self._poll([row.id], force=True)
        return await self._get(row.id)

    # ------------------------------------------------------------------ job
    async def reconcile(self) -> int:
        """Push approved requests, close the ones that arrived, ask about the rest. Returns the number changed."""
        async with self._lock:
            async with self._db.session() as s:
                rows = list(
                    (
                        await s.execute(
                            select(MediaRequest)
                            .where(MediaRequest.state.in_(REQUEST_OPEN_STATES))
                            .order_by(MediaRequest.created_at, MediaRequest.id)
                        )
                    ).scalars()
                )
            if not rows:
                return 0
            changed = 0
            reachable = True
            sent: list[MediaRequest] = []
            async with self._connection() as manager:
                for row in rows:
                    if await self._in_library(row.media_type, row.tmdb_id, row.title, row.year):
                        closed = await self._transition(
                            row, REQUEST_OPEN_STATES, "available", notice="request_available"
                        )
                        changed += closed is not None
                    elif row.state == "sent":
                        sent.append(row)
                    elif row.state == "approved" and manager is not None and reachable:
                        # Unreachable: do not spend a timeout on every remaining request.
                        pushed, reachable = await self._send(row, manager, retry=True)
                        changed += pushed.state == "sent"
        if not sent:
            return changed
        if not reachable:
            now = self.now()
            for row in sent:
                await self._store(row, self._unanswered(row, now, "The request manager did not answer."))
            return changed
        # Every sent request, due or not: this sweep is the slowest pace anything is asked at.
        return changed + await self._poll([row.id for row in sent], force=True)
