from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.adapters.seerr import SeerrError
from app.domain.entities import MetaTitle
from app.infra.models import MediaRequest, Notice, Profile, Source, Title
from app.services import request_policy
from app.services.auth import Principal
from app.services.errors import Conflict, Forbidden, NotFound, ServiceError
from app.services.requests import RequestService
from tests.conftest import Api

TITLES = {("movie", 603): ("The Matrix", 1999), ("tv", 1399): ("Game of Thrones", 2011)}


class FakeManager:
    def __init__(self) -> None:
        self.configured = True
        self.created: list[tuple[str, int, Any]] = []
        self.statuses: dict[int, str] = {}
        self.fail: SeerrError | None = None
        self.next_id = 100

    def open(self) -> Any:
        if not self.configured:
            return None

        @asynccontextmanager
        async def connection() -> Any:
            yield self

        return connection()

    async def create_request(self, media_type: str, tmdb_id: int, seasons: Any = None) -> int:
        if self.fail is not None:
            raise self.fail
        self.created.append((media_type, tmdb_id, seasons))
        self.next_id += 1
        return self.next_id

    async def request_status(self, request_id: int) -> str:
        status = self.statuses.get(request_id, "approved")
        if isinstance(status, Exception):
            raise status
        return status


class Env:
    def __init__(self, api: Api, viewer: Principal, other: Principal) -> None:
        self.api = api
        self.manager = FakeManager()
        self.meta = AsyncMock()
        self.meta.enabled = True
        self.meta.details.side_effect = self.details
        self.service = RequestService(api.c.db, api.c.availability, api.c.sources, self.meta, self.manager.open)
        self.admin = Principal(profile_id=api.profile_id, is_admin=True, kids=False)
        self.viewer = viewer
        self.other = other

    @staticmethod
    async def details(media_type: str, tmdb_id: int) -> MetaTitle | None:
        known = TITLES.get((media_type, tmdb_id))
        if known is None:
            return None
        return MetaTitle(tmdb_id, media_type, known[0], year=known[1], poster="https://img.example.test/p.jpg")

    async def notices(self, profile_id: str) -> list[Notice]:
        async with self.api.c.db.session() as s:
            return list((await s.execute(select(Notice).where(Notice.profile_id == profile_id))).scalars())

    async def add_library_title(self, tmdb_id: int = 603, *, source_type: str = "plex") -> None:
        async with self.api.c.db.session() as s:
            if await s.get(Source, "lib1") is None:
                s.add(Source(id="lib1", type=source_type, name="Library", config_enc="unused"))
                await s.flush()
            s.add(
                Title(
                    source_id="lib1",
                    ext_id=str(tmdb_id),
                    kind="movie",
                    title="The Matrix",
                    norm_title="matrix",
                    year=1999,
                    tmdb_id=tmdb_id,
                )
            )
            await s.commit()


@pytest.fixture
async def env(api: Api) -> Env:
    async with api.c.db.session() as s:
        s.add_all([Profile(id="viewer1", name="Viewer"), Profile(id="viewer2", name="Other viewer")])
        await s.commit()
    return Env(
        api,
        Principal(profile_id="viewer1", is_admin=False, kids=False),
        Principal(profile_id="viewer2", is_admin=False, kids=False),
    )


def test_policy_is_the_single_authority(env: Env) -> None:
    assert request_policy.can_request(env.viewer) and request_policy.can_request(env.admin)
    assert request_policy.auto_approve(env.admin) and not request_policy.auto_approve(env.viewer)
    assert request_policy.can_approve(env.admin) and not request_policy.can_approve(env.viewer)


async def test_viewer_request_waits_for_approval(env: Env) -> None:
    row = await env.service.create(env.viewer, "movie", 603, None)
    assert (row.state, row.title, row.year, row.profile_id) == ("pending_approval", "The Matrix", 1999, "viewer1")
    assert row.poster == "https://img.example.test/p.jpg" and len(row.id) == 16 and row.seasons is None
    assert row.manager_request_id is None and not env.manager.created
    assert await env.service.state_for("movie", 603) == "pending_approval"
    assert await env.service.state_for("movie", 604) is None


async def test_admin_request_is_auto_approved_and_sent(env: Env) -> None:
    row = await env.service.create(env.admin, "tv", 1399, [2, 1, 2])
    assert row.state == "sent" and row.manager_request_id == 101 and row.seasons == [1, 2]
    assert row.decided_by_profile_id == env.admin.profile_id
    assert env.manager.created == [("tv", 1399, [1, 2])]


async def test_movie_requests_ignore_seasons_and_bad_seasons_are_rejected(env: Env) -> None:
    assert (await env.service.create(env.viewer, "movie", 603, [1])).seasons is None
    with pytest.raises(ServiceError):
        await env.service.create(env.viewer, "tv", 1399, [-1])
    with pytest.raises(ServiceError):
        await env.service.create(env.viewer, "episode", 1399, None)


async def test_duplicate_open_request_returns_the_existing_one(env: Env) -> None:
    first = await env.service.create(env.viewer, "movie", 603, None)
    again = await env.service.create(env.other, "movie", 603, None)
    assert again.id == first.id and again.profile_id == "viewer1"
    # Even an auto-approving profile joins the open request instead of opening a second one.
    assert (await env.service.create(env.admin, "movie", 603, None)).id == first.id
    assert not env.manager.created
    async with env.api.c.db.session() as s:
        s.add(MediaRequest(id="f" * 16, profile_id="viewer2", media_type="movie", tmdb_id=603, title="x", state="sent"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_closed_requests_do_not_block_a_new_one(env: Env) -> None:
    first = await env.service.create(env.viewer, "movie", 603, None)
    await env.service.decide(env.admin, first.id, False, "Not this one")
    second = await env.service.create(env.viewer, "movie", 603, None)
    assert second.id != first.id and second.state == "pending_approval"
    assert await env.service.state_for("movie", 603) == "pending_approval"


async def test_already_available_title_is_rejected(env: Env) -> None:
    await env.add_library_title()
    with pytest.raises(Conflict) as err:
        await env.service.create(env.viewer, "movie", 603, None)
    assert err.value.code == "already_available"


async def test_unknown_title_and_unconfigured_manager(env: Env) -> None:
    with pytest.raises(NotFound):
        await env.service.create(env.viewer, "movie", 999, None)
    env.manager.configured = False
    assert not env.service.configured
    with pytest.raises(Conflict) as err:
        await env.service.create(env.viewer, "movie", 603, None)
    assert err.value.code == "request_manager_unconfigured"


async def test_approve_sends_to_the_manager(env: Env) -> None:
    row = await env.service.create(env.viewer, "movie", 603, None)
    decided = await env.service.decide(env.admin, row.id, True, None)
    assert decided.state == "sent" and decided.manager_request_id == 101
    assert decided.decided_by_profile_id == env.admin.profile_id
    assert env.manager.created == [("movie", 603, None)]
    with pytest.raises(Conflict):
        await env.service.decide(env.admin, row.id, False, None)
    with pytest.raises(NotFound):
        await env.service.decide(env.admin, "missing", True, None)


async def test_decline_records_note_and_notifies_requester(env: Env) -> None:
    row = await env.service.create(env.viewer, "movie", 603, None)
    declined = await env.service.decide(env.admin, row.id, False, "  Already on a service  ")
    assert declined.state == "declined" and declined.note == "Already on a service"
    notices = await env.notices("viewer1")
    assert [(n.kind, n.ref_id, n.read_at) for n in notices] == [("request_declined", "tmdb:movie:603", None)]
    assert "The Matrix" in notices[0].title and notices[0].body == "Already on a service"
    assert not env.manager.created


async def test_permission_failures(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    row = await env.service.create(env.viewer, "movie", 603, None)
    with pytest.raises(Forbidden):
        await env.service.decide(env.viewer, row.id, True, None)
    with pytest.raises(Forbidden):
        await env.service.list(env.viewer, scope="all", state=None)
    monkeypatch.setattr(request_policy, "can_request", lambda p: False)
    with pytest.raises(Forbidden):
        await env.service.create(env.other, "tv", 1399, None)


async def test_list_scopes_and_state_filter(env: Env) -> None:
    mine = await env.service.create(env.viewer, "movie", 603, None)
    theirs = await env.service.create(env.other, "tv", 1399, None)
    assert [r.id for r in await env.service.list(env.viewer, scope="mine", state=None)] == [mine.id]
    assert {r.id for r in await env.service.list(env.admin, scope="all", state=None)} == {mine.id, theirs.id}
    await env.service.decide(env.admin, theirs.id, False, None)
    assert [r.id for r in await env.service.list(env.admin, scope="all", state="declined")] == [theirs.id]
    assert await env.service.list(env.admin, scope="mine", state=None) == []
    with pytest.raises(ServiceError):
        await env.service.list(env.admin, scope="all", state="bogus")


async def test_manager_error_leaves_approved_and_reconcile_retries(env: Env) -> None:
    env.manager.fail = SeerrError("The request manager answered 500.", 500)
    row = await env.service.create(env.viewer, "movie", 603, None)
    approved = await env.service.decide(env.admin, row.id, True, None)
    assert approved.state == "approved" and approved.manager_request_id is None
    assert approved.note == "The request manager answered 500."
    assert await env.service.reconcile() == 0
    env.manager.fail = None
    assert await env.service.reconcile() == 1
    sent = (await env.service.list(env.viewer, scope="mine", state=None))[0]
    assert sent.state == "sent" and sent.manager_request_id == 101 and sent.note is None
    assert await env.service.reconcile() == 0


async def test_manager_conflict_without_id_still_counts_as_sent(env: Env) -> None:
    env.manager.fail = SeerrError("Request for this media already exists.", 409)
    row = await env.service.create(env.admin, "movie", 603, None)
    assert row.state == "sent" and row.manager_request_id is None and row.note


async def test_reconcile_closes_requests_when_the_library_has_the_title(env: Env) -> None:
    sent = await env.service.create(env.admin, "movie", 603, None)
    pending = await env.service.create(env.viewer, "tv", 1399, None)
    await env.add_library_title()
    assert await env.service.reconcile() == 1
    rows = {r.id: r for r in await env.service.list(env.admin, scope="all", state=None)}
    assert rows[sent.id].state == "available" and rows[pending.id].state == "pending_approval"
    notices = await env.notices(env.admin.profile_id)
    assert [(n.kind, n.ref_id) for n in notices] == [("request_available", "tmdb:movie:603")]
    assert notices[0].title == "The Matrix is now available"
    assert await env.service.reconcile() == 0 and len(await env.notices(env.admin.profile_id)) == 1


async def test_reconcile_ignores_hand_off_only_sources(env: Env) -> None:
    await env.service.create(env.admin, "movie", 603, None)
    await env.add_library_title(source_type="streaming_service")
    assert await env.service.reconcile() == 0


async def test_reconcile_follows_manager_status(env: Env) -> None:
    movie = await env.service.create(env.viewer, "movie", 603, None)
    show = await env.service.create(env.other, "tv", 1399, None)
    await env.service.decide(env.admin, movie.id, True, None)
    await env.service.decide(env.admin, show.id, True, None)
    assert await env.service.reconcile() == 0
    env.manager.statuses = {101: "available", 102: "declined"}
    assert await env.service.reconcile() == 2
    rows = {r.id: r for r in await env.service.list(env.admin, scope="all", state=None)}
    assert rows[movie.id].state == "available" and rows[show.id].state == "declined"
    assert [n.kind for n in await env.notices("viewer1")] == ["request_available"]
    assert [n.kind for n in await env.notices("viewer2")] == ["request_declined"]


async def test_reconcile_marks_failed_and_survives_manager_errors(env: Env) -> None:
    movie = await env.service.create(env.admin, "movie", 603, None)
    show = await env.service.create(env.admin, "tv", 1399, None)
    env.manager.statuses = {101: SeerrError("boom", 500), 102: "failed"}  # type: ignore[dict-item]
    assert await env.service.reconcile() == 1
    rows = {r.id: r for r in await env.service.list(env.admin, scope="all", state=None)}
    assert rows[movie.id].state == "sent" and rows[show.id].state == "failed"
    # A request the manager no longer knows cannot complete there.
    env.manager.statuses = {101: SeerrError("Request not found.", 404)}  # type: ignore[dict-item]
    assert await env.service.reconcile() == 1
    assert {r.id for r in await env.service.list(env.admin, scope="all", state="failed")} == {movie.id, show.id}
    assert await env.notices(env.admin.profile_id) == []


async def test_reconcile_stops_asking_an_unreachable_manager(env: Env) -> None:
    await env.service.create(env.admin, "movie", 603, None)
    await env.service.create(env.admin, "tv", 1399, None)
    asked: list[int] = []

    async def unreachable(request_id: int) -> str:
        asked.append(request_id)
        raise SeerrError("Could not reach the request manager (ConnectError).")

    env.manager.request_status = unreachable  # type: ignore[method-assign]
    assert await env.service.reconcile() == 0
    assert asked == [101]


async def test_restricted_profile_cannot_request_a_blocked_title(env: Env) -> None:
    class Policy:
        enabled = True

        async def require(self, profile_id: str, ref: str) -> None:
            if profile_id == "viewer1":
                raise Forbidden("blocked", code="content_restricted")

    env.service.content_policy = Policy()
    await env.service.create(env.other, "movie", 603, None)
    with pytest.raises(Forbidden) as err:
        await env.service.create(env.viewer, "movie", 603, None)
    assert err.value.code == "content_restricted"


async def test_deleting_a_profile_removes_its_requests_and_notices(env: Env) -> None:
    row = await env.service.create(env.viewer, "movie", 603, None)
    await env.service.decide(env.admin, row.id, False, None)
    await env.api.c.auth.delete_profile(env.admin, "viewer1")
    assert await env.notices("viewer1") == []
    assert await env.service.list(env.admin, scope="all", state=None) == []
