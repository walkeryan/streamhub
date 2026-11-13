"""Plex resume, both ways, for the one user linked to the Plex account.

The Plex server is respx-mocked: ``/library/metadata/…`` answers with the account's
``viewOffset``/``lastViewedAt``/``viewCount``, ``/:/timeline`` records what is written to it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.adapters.plex.provider import PlexSource
from app.infra.models import Progress, Source, Title
from app.services import user_state
from tests.conftest import Api
from tests.people import Member, member, owner_id, set_user

PLEX = "http://plex.example.test:32400"
FILM = "lib:plex:55"
HOUR = 3_600_000


def episode(key: str, number: int, **state: Any) -> dict[str, Any]:
    part = {"key": f"/library/parts/{key}/file.mkv", "container": "mkv"}
    return {
        **{"ratingKey": key, "type": "episode", "title": f"Part {number}", "duration": HOUR},
        **{"parentRatingKey": "80", "parentIndex": 1, "index": number, "Media": [{"Part": [part]}]},
        **state,
    }


class Plex:
    """A Plex server: its metadata, with the account's watch state in it, and the timeline updates it was sent."""

    def __init__(self, router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.episodes: list[dict[str, Any]] = [episode("71", 1), episode("72", 2), episode("73", 3)]
        self.state_reads: list[str] = []  # rating keys whose watch state was asked for
        self.timeline: list[dict[str, str]] = []
        self.down = False
        path = rf"^{PLEX}/library/metadata/(?P<key>\d+)(?P<sub>/allLeaves|/children)?(\?.*)?$"
        router.get(url__regex=path).mock(side_effect=self.read)
        router.get(f"{PLEX}/:/timeline").mock(side_effect=self.wrote)
        asked = PlexSource.watch_states

        async def watch_states(source: PlexSource, item: Any) -> Any:
            self.state_reads.append(item.ext_id)
            return await asked(source, item)

        monkeypatch.setattr(PlexSource, "watch_states", watch_states)

    def watched(self, key: str, *, offset: int | None, at: datetime | None, count: int = 0) -> None:
        item: dict[str, Any] = {"ratingKey": key, "type": "movie", "title": "The Mill", "duration": 2 * HOUR}
        item["Media"] = [{"Part": [{"key": "/library/parts/9/file.mkv", "container": "mkv"}]}]
        if offset:
            item["viewOffset"] = offset
        if at is not None:
            item["lastViewedAt"] = int(at.timestamp())
        if count:
            item["viewCount"] = count
        self.items[key] = item

    def read(self, request: httpx.Request, key: str, sub: str | None = None) -> httpx.Response:
        if self.down:
            return httpx.Response(500)
        assert request.headers["x-plex-token"] == "plex-token"
        if sub == "/children":
            rows = [{"ratingKey": "80", "type": "season", "title": "Season 1", "index": 1, "parentRatingKey": key}]
        elif sub == "/allLeaves":
            rows = self.episodes
        else:
            rows = [self.items[key]] if key in self.items else []
        return httpx.Response(200, json={"MediaContainer": {"Metadata": rows}})

    def wrote(self, request: httpx.Request) -> httpx.Response:
        self.timeline.append(dict(request.url.params))
        return httpx.Response(200)


@pytest.fixture
def plex(monkeypatch: pytest.MonkeyPatch) -> Iterator[Plex]:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.route(host="hub.test").pass_through()
        yield Plex(router, monkeypatch)


@pytest.fixture(autouse=True)
def every_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ask Plex on every open in these tests; the pause between asks has a test of its own.
    monkeypatch.setattr(user_state, "ACCOUNT_STATE_EVERY_S", 0.0)


async def library(api: Api) -> None:
    await api.accept_disclaimer()
    config = api.c.crypto.encrypt_json({"server_url": PLEX, "token": "plex-token"})
    async with api.c.db.session() as s:
        s.add(Source(id="plex", type="plex", name="Library", config_enc=config))
        part = {"part_key": "/library/parts/9/file.mkv", "container": "mkv"}
        s.add(Title(source_id="plex", ext_id="55", kind="movie", title="The Mill", duration_ms=2 * HOUR, extra=part))
        s.add(Title(source_id="plex", ext_id="70", kind="show", title="The Harbour"))
        await s.commit()


async def linked_owner(api: Api) -> None:
    await set_user(api, await owner_id(api), plex_account_linked=True)


async def resume(who: Api | Member, ref: str = FILM) -> int:
    r = await who.post("/playback/resolve", json={"id": ref, "client": "android_tv"})
    assert r.status_code == 200, r.text
    return int(r.json()["resume_position_ms"])


async def report(who: Api | Member, position_ms: int, state: str = "paused", ref: str = FILM) -> None:
    body = {"id": ref, "position_ms": position_ms, "duration_ms": 2 * HOUR, "state": state}
    assert (await who.post("/playback/progress", json=body)).status_code == 204


async def local(api: Api, profile_id: str, ref: str = FILM) -> Progress | None:
    async with api.c.db.session() as s:
        return (
            await s.execute(select(Progress).where(Progress.profile_id == profile_id, Progress.ref_id == ref))
        ).scalar_one_or_none()


def ago(**kw: Any) -> datetime:
    return datetime.now(UTC).replace(microsecond=0) - timedelta(**kw)


# ---------------------------------------------------------------- reading Plex
async def test_newer_plex_offset_wins_on_title_open_and_at_resolve(api: Api, plex: Plex) -> None:
    await library(api)
    await linked_owner(api)
    # Nothing local yet: what the account watched in Plex an hour ago is where the film resumes.
    seen = ago(hours=1)
    plex.watched("55", offset=600_000, at=seen)
    assert (await api.get(f"/titles/{FILM}")).status_code == 200
    row = await local(api, api.profile_id)
    assert row is not None and (row.position_ms, row.completed, row.updated_at) == (600_000, False, seen)
    assert row.duration_ms == 2 * HOUR and row.source_id == "plex"
    assert await resume(api) == 600_000

    # Watched further here, then further still in Plex: the later of the two wins each time.
    await report(api, 900_000)
    assert await resume(api) == 900_000
    plex.watched("55", offset=1_500_000, at=datetime.now(UTC) + timedelta(seconds=5))
    assert await resume(api) == 1_500_000
    assert plex.state_reads and set(plex.state_reads) == {"55"}


async def test_older_plex_offset_loses(api: Api, plex: Plex) -> None:
    await library(api)
    await linked_owner(api)
    await report(api, 900_000)
    plex.watched("55", offset=120_000, at=ago(days=2))
    assert (await api.get(f"/titles/{FILM}")).status_code == 200
    assert await resume(api) == 900_000
    row = await local(api, api.profile_id)
    assert row is not None and row.position_ms == 900_000 and row.updated_at > ago(minutes=1)
    # Never watched in Plex at all: nothing to take over, nothing created.
    plex.watched("55", offset=None, at=None)
    assert await resume(api) == 900_000


async def test_watched_in_plex_marks_completed(api: Api, plex: Plex) -> None:
    await library(api)
    await linked_owner(api)
    await report(api, 900_000)
    plex.watched("55", offset=None, at=datetime.now(UTC) + timedelta(seconds=5), count=1)
    assert await resume(api) == 0
    row = await local(api, api.profile_id)
    assert row is not None and (row.completed, row.position_ms) == (True, 0)
    # Started again in Plex afterwards: a view count with an offset is a film in progress.
    plex.watched("55", offset=300_000, at=datetime.now(UTC) + timedelta(seconds=10), count=1)
    assert await resume(api) == 300_000
    assert (await local(api, api.profile_id)).completed is False  # type: ignore[union-attr]


async def test_show_opens_with_the_accounts_episodes_and_plays_the_next_one(api: Api, plex: Plex) -> None:
    await library(api)
    await linked_owner(api)
    seen = ago(hours=3)
    stamp = int(seen.timestamp())
    plex.episodes = [
        episode("71", 1, lastViewedAt=stamp, viewCount=1),
        episode("72", 2, lastViewedAt=stamp, viewOffset=240_000),
        episode("73", 3),
    ]
    assert (await api.get("/titles/lib:plex:70")).status_code == 200
    assert plex.state_reads == ["70"]  # asked once, for the whole show
    first, second = await local(api, api.profile_id, "lib:plex:71"), await local(api, api.profile_id, "lib:plex:72")
    assert first is not None and first.completed and second is not None and second.position_ms == 240_000
    assert await local(api, api.profile_id, "lib:plex:73") is None
    r = await api.post("/playback/resolve", json={"id": "lib:plex:70", "client": "android_tv"})
    assert r.status_code == 200, r.text
    assert (r.json()["id"], r.json()["resume_position_ms"]) == ("lib:plex:72", 240_000)


async def test_plex_being_down_or_slow_never_fails_the_request(api: Api, plex: Plex) -> None:
    await library(api)
    await linked_owner(api)
    await report(api, 900_000)
    plex.down = True
    assert (await api.get(f"/titles/{FILM}")).status_code == 200
    assert await resume(api) == 900_000 and plex.state_reads


async def test_plex_is_asked_at_most_once_in_a_while(api: Api, plex: Plex, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_state, "ACCOUNT_STATE_EVERY_S", 10.0)
    await library(api)
    await linked_owner(api)
    plex.watched("55", offset=600_000, at=ago(hours=1))
    for _ in range(3):
        assert (await api.get(f"/titles/{FILM}")).status_code == 200
        assert await resume(api) == 600_000
    assert plex.state_reads == ["55"]


# ---------------------------------------------------------------- writing to Plex
async def test_only_the_linked_users_progress_is_forwarded_to_plex(api: Api, plex: Plex) -> None:
    await library(api)
    await linked_owner(api)
    guest = await member(api, "family", "Guest")
    await report(guest, 400_000, "playing")
    assert plex.timeline == []  # someone else's viewing is not written into the account's history
    await report(api, 900_000, "playing")
    [sent] = plex.timeline
    assert (sent["ratingKey"], sent["time"], sent["state"], sent["duration"]) == ("55", "900000", "playing", "7200000")


# ---------------------------------------------------------------- everyone else
async def test_unlinked_user_ignores_plex_and_does_not_write_to_it(api: Api, plex: Plex) -> None:
    await library(api)
    guest = await member(api, "family", "Guest")
    plex.watched("55", offset=600_000, at=datetime.now(UTC) + timedelta(seconds=5), count=3)
    # Nobody is linked: not the owner, not a member. Plex's state is never read…
    for who in (api, guest):
        assert (await who.get(f"/titles/{FILM}")).status_code == 200
        assert await resume(who) == 0
    await report(guest, 400_000)
    await report(api, 700_000)
    assert await resume(guest) == 400_000 and await resume(api) == 700_000
    assert (await api.get("/titles/lib:plex:70")).status_code == 200
    r = await guest.post("/playback/resolve", json={"id": "lib:plex:70", "client": "android_tv"})
    assert r.status_code == 200 and r.json()["id"] == "lib:plex:71"
    # …and never written.
    assert plex.state_reads == [] and plex.timeline == []

    # Linking one user changes nothing for the other.
    await linked_owner(api)
    assert await resume(api) == 600_000 and await resume(guest) == 400_000
    assert (await guest.get(f"/titles/{FILM}")).status_code == 200
    assert (await local(api, guest.profile_id)).position_ms == 400_000  # type: ignore[union-attr]
    assert plex.state_reads == ["55"] and plex.timeline == []
