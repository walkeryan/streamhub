"""RequestService: a viewer asks for a title no source has.

StreamHub owns the request record and its approval state; the request manager is only
the downstream executor.

    pending_approval → approved → sent → available
                    ↘ declined           ↘ declined | failed   (reported by the manager)

``approved → sent`` happens in the same call when the manager accepts the request. A
manager error leaves the request ``approved`` with the error in ``note`` and
``reconcile`` retries. ``reconcile`` also closes any open request once an in-app source
has the title, and tells the requester.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Literal, Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.adapters.seerr import SeerrError
from app.domain.ports import MetadataProvider
from app.infra.db import Database
from app.infra.models import REQUEST_OPEN_STATES, MediaRequest, Profile, utcnow
from app.services import ids, request_policy
from app.services.auth import Principal
from app.services.availability import AvailabilityService
from app.services.errors import BadGateway, Conflict, Forbidden, NotFound, ServiceError
from app.services.notices import new_notice
from app.services.sources import SourceService

log = logging.getLogger(__name__)

STATES = (*REQUEST_OPEN_STATES, "available", "declined", "failed")
NOTE_MAX = 500
LIST_LIMIT = 200


class RequestManager(Protocol):
    async def create_request(
        self,
        media_type: Literal["movie", "tv"],
        tmdb_id: int,
        seasons: list[int] | Literal["all"] | None = None,
    ) -> int: ...

    async def request_status(self, request_id: int) -> str: ...

    async def test(self) -> tuple[bool, str]: ...


# Returns a connection to the configured manager, or None while none is configured.
ManagerFactory = Callable[[], AbstractAsyncContextManager[RequestManager] | None]


def hub_id(row: MediaRequest) -> str:
    return ids.tmdb_id(row.media_type, row.tmdb_id)


def _clean_note(note: str | None) -> str | None:
    return (note or "").strip()[:NOTE_MAX] or None


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
        # Serialises everything that talks to the manager so a request is sent once.
        self._lock = asyncio.Lock()

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

    async def states_for(self, titles: set[tuple[str, int]]) -> dict[tuple[str, int], str]:
        """State of the open request for each title, else of its most recent closed one."""
        if not titles:
            return {}
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(MediaRequest.media_type, MediaRequest.tmdb_id, MediaRequest.state)
                    .where(MediaRequest.tmdb_id.in_({tmdb_id for _, tmdb_id in titles}))
                    .order_by(MediaRequest.created_at, MediaRequest.id)
                )
            ).all()
        out: dict[tuple[str, int], str] = {}
        for media_type, tmdb_id, state in rows:
            key = (media_type, tmdb_id)
            if key in titles and not (out.get(key) in REQUEST_OPEN_STATES and state not in REQUEST_OPEN_STATES):
                out[key] = state
        return out

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
        """Stamp ``request_state`` / ``requestable`` on every TMDB-identified tile of an API payload.

        Done on the finished payload, after any listing cache, because request state changes
        independently of the catalog.
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
        states = await self.states_for({key for _, key in tiles})
        allowed = self.configured and request_policy.can_request(p)
        for tile, key in tiles:
            tile["request_state"] = states.get(key)
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
                .values(state=state, updated_at=utcnow(), **values)
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

    async def _dispatch(self, request_id: str) -> MediaRequest:
        async with self._lock:
            row = await self._get(request_id)
            if row.state != "approved":
                return row
            async with self._connection() as manager:
                return (await self._send(row, manager, retry=False))[0]

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

    # ------------------------------------------------------------------ job
    async def _follow(self, row: MediaRequest, manager_request_id: int, manager: RequestManager) -> bool:
        try:
            status = await manager.request_status(manager_request_id)
        except SeerrError as exc:
            if exc.status != 404:
                raise
            note = "The request manager no longer has this request."
            return await self._transition(row, ("sent",), "failed", note=note) is not None
        if status == "available":
            return await self._transition(row, ("sent",), "available", notice="request_available") is not None
        if status == "declined":
            note = "Declined in the request manager."
            return await self._transition(row, ("sent",), "declined", notice="request_declined", note=note) is not None
        if status == "failed":
            note = "The request manager could not complete this request."
            return await self._transition(row, ("sent",), "failed", note=note) is not None
        return False

    async def reconcile(self) -> int:
        """Push approved requests, close the ones that arrived. Returns the number changed."""
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
            async with self._connection() as manager:
                for row in rows:
                    if await self._in_library(row.media_type, row.tmdb_id, row.title, row.year):
                        closed = await self._transition(
                            row, REQUEST_OPEN_STATES, "available", notice="request_available"
                        )
                        changed += closed is not None
                    elif manager is None:
                        continue
                    elif row.state == "approved":
                        sent, reachable = await self._send(row, manager, retry=True)
                        changed += sent.state == "sent"
                    elif row.state == "sent" and row.manager_request_id is not None:
                        try:
                            changed += await self._follow(row, row.manager_request_id, manager)
                        except SeerrError as exc:
                            log.info("request status unavailable", extra={"request": row.id, "status": exc.status})
                            reachable = exc.status is not None
                    if not reachable:
                        # Unreachable: do not spend a timeout on every remaining request.
                        manager = None
            return changed
