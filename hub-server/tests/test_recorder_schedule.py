"""RecorderService: snapshots, rules, and the tick state machine, on a fake clock and a fake capture backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.infra.models import Channel, ChannelGroup, Programme, Recording, RecordingRule
from app.services import ids
from app.services.auth import Principal
from app.services.errors import Forbidden, NotFound, ServiceError
from app.services.permissions import BUILTIN_ROLES
from app.services.recorder import (
    CaptureResult,
    NullCapture,
    RecorderService,
    SourceBusy,
    programme_details,
    programme_key,
    recording_filename,
    rule_matches,
)
from app.services.sync import SyncScheduler
from tests.conftest import Api
from tests.test_epg_integrity import add_source

NOW = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
GB = 1024**3


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> datetime:
        self.at += timedelta(**delta)
        return self.at


class FakeCapture:
    def __init__(self) -> None:
        self.busy = False
        self.running: set[str] = set()
        self.attempts: list[str] = []
        self.stopped: list[str] = []
        self.results: dict[str, CaptureResult] = {}
        self.archive: dict[str, CaptureResult] = {}
        self.recover_calls: list[str] = []

    async def start(self, recording: Recording) -> None:
        self.attempts.append(recording.id)
        if self.busy:
            raise SourceBusy("Source connection limit is occupied.")
        self.running.add(recording.id)

    async def stop(self, recording: Recording) -> CaptureResult:
        self.running.discard(recording.id)
        self.stopped.append(recording.id)
        return self.results.get(recording.id, CaptureResult(path=f"{recording.id}.mkv", size_bytes=4096))

    def is_running(self, recording_id: str) -> bool:
        return recording_id in self.running

    async def recover_from_archive(self, recording: Recording) -> CaptureResult | None:
        self.recover_calls.append(recording.id)
        return self.archive.get(recording.id)


@dataclass
class Env:
    api: Api
    svc: RecorderService
    clock: Clock
    capture: FakeCapture
    root: Path
    admin: Principal
    source: dict[str, str]
    free: dict[str, int] = field(default_factory=lambda: {"bytes": 500 * GB})

    async def channel(self, epg_id: str, source_id: str | None = None) -> Channel:
        async with self.api.c.db.session() as s:
            return (
                await s.execute(
                    select(Channel).where(
                        Channel.source_id == (source_id or self.source["id"]), Channel.epg_id == epg_id
                    )
                )
            ).scalar_one()

    async def programme(
        self,
        ch: Channel,
        title: str,
        start: datetime,
        minutes: int = 60,
        *,
        category: str | None = None,
        description: str | None = None,
    ) -> str:
        async with self.api.c.db.session() as s:
            row = Programme(
                feed=ch.source_id,
                epg_channel=ch.epg_id or "",
                title=title,
                start=start,
                stop=start + timedelta(minutes=minutes),
                category=category,
                description=description,
            )
            s.add(row)
            await s.commit()
            return ids.prog_id(row.id)

    async def tick(self, **delta: float) -> None:
        await self.svc.tick(self.clock.advance(**delta))
        await self.svc.wait_idle()  # archive recovery runs beside the tick

    async def recordings(self) -> list[Recording]:
        async with self.api.c.db.session() as s:
            return list((await s.execute(select(Recording).order_by(Recording.starts_at, Recording.id))).scalars())

    async def get(self, recording_id: str) -> Recording:
        async with self.api.c.db.session() as s:
            row = await s.get(Recording, recording_id)
        assert row is not None
        return row

    async def viewer(self, name: str = "Viewer", *, kids: bool = False) -> Principal:
        profile = (await self.api.post("/profiles", json={"name": name, "pin": "1234", "kids": kids})).json()
        role = BUILTIN_ROLES["kid" if kids else "family"]
        return Principal(profile_id=profile["id"], kids=kids, permissions=role.permissions)


@pytest.fixture
async def env(api: Api, tmp_path: Path) -> Env:
    root = tmp_path / "recordings"
    root.mkdir()
    clock, capture = Clock(NOW), FakeCapture()
    free = {"bytes": 500 * GB}
    svc = RecorderService(
        api.c.db,
        api.c.channels,
        recordings_dir=str(root),
        min_free_gb=50,
        capture=capture,
        clock=clock,
        free_bytes=lambda: free["bytes"],
    )
    source = await add_source(api, "Feed one")
    admin = Principal(profile_id=api.profile_id, is_admin=True, kids=False)
    return Env(api, svc, clock, capture, root, admin, source, free)


def hub(ch: Channel) -> str:
    return ids.channel_id(ch.source_id, ch.ext_id)


# ---------------------------------------------------------------------- pure helpers
def test_programme_key_is_source_channel_and_start() -> None:
    start = datetime(2026, 11, 14, 20, 30, tzinfo=ZoneInfo("Europe/Paris"))
    assert programme_key("ch:src1:bbc-one", start) == "src1:bbc-one@20261114T193000Z"
    assert programme_key("ch:src1:bbc-one", start.astimezone(UTC)) == programme_key("ch:src1:bbc-one", start)


@pytest.mark.parametrize(
    ("title", "category", "expected"),
    [
        ("Evening Quiz", None, ("Evening Quiz", None, None, None, False, False)),
        ("Harbour Lights S02E05 The Storm", "Drama", ("Harbour Lights", "The Storm", 2, 5, False, False)),
        ("Harbour Lights - S2 E11", None, ("Harbour Lights", None, 2, 11, False, False)),
        (
            "NBA: Boston Celtics vs Miami Heat",
            None,
            ("NBA: Boston Celtics vs Miami Heat", None, None, None, True, False),
        ),
        ("Tennis Replay", None, ("Tennis Replay", None, None, None, True, True)),
        ("Harbour Lights", "Drama (Repeat)", ("Harbour Lights", None, None, None, False, True)),
    ],
)
def test_programme_details_come_only_from_what_the_guide_says(
    title: str, category: str | None, expected: tuple[str, str | None, int | None, int | None, bool, bool]
) -> None:
    d = programme_details(title, category)
    assert (d.title, d.subtitle, d.season, d.episode, d.is_sport, d.is_repeat) == expected


@pytest.mark.parametrize(
    ("kind", "match", "title", "category", "expected"),
    [
        ("series", "Harbour Lights", "The Harbour Lights (2026) [HD]", None, True),
        ("series", "Harbour Lights", "Harbour Lights S02E05 The Storm", None, True),
        ("series", "Harbour Lights", "Harbour Lights Extra", None, False),
        ("team", "Celtics", "NBA: Boston Celtics vs Miami Heat", None, True),
        ("team", "boston celtics", "Boston  Celtics @ Miami Heat", "Basketball", True),
        ("team", "Celtic", "NBA: Boston Celtics vs Miami Heat", None, False),  # whole words only
        ("team", "Celtics", "The Celtics Story", "Documentary", False),  # not a game
        ("keyword", "storm", "Harbour Lights S02E05 The STORM", None, True),
        ("keyword", "storm", "Brainstorming Live", None, True),
        ("keyword", "storm", "Evening Quiz", None, False),
    ],
)
def test_rule_matching(kind: str, match: str, title: str, category: str | None, expected: bool) -> None:
    assert rule_matches(kind, match, title, category) is expected


def rec(**kw: object) -> Recording:
    base: dict[str, object] = {
        "id": "r1",
        "title": "Harbour Lights",
        "subtitle": None,
        "season": None,
        "episode": None,
        "is_sport": False,
        "rule_id": None,
        "starts_at": datetime(2026, 11, 14, 20, 5, tzinfo=UTC),
    }
    return Recording(**{**base, **kw})


@pytest.mark.parametrize(
    ("recording", "expected"),
    [
        (
            rec(season=2, episode=5, subtitle="The Storm"),
            "Harbour Lights/Season 02/Harbour Lights - S02E05 - The Storm.mkv",
        ),
        (rec(season=2, episode=105), "Harbour Lights/Season 02/Harbour Lights - S02E105.mkv"),
        (rec(rule_id="rule1"), "Harbour Lights/Harbour Lights - 2026-11-14.mkv"),
        (
            rec(title="NBA Basketball", subtitle="Boston Celtics vs Miami Heat", is_sport=True, rule_id="rule1"),
            "Sports/NBA Basketball - Boston Celtics vs Miami Heat (2026-11-14).mkv",
        ),
        (rec(title="NBA: Celtics @ Heat", is_sport=True), "Sports/NBA Celtics @ Heat (2026-11-14).mkv"),
        (rec(title="Evening Quiz"), "Evening Quiz (2026-11-14 2005).mkv"),
        (rec(title='../etc/pass"wd?  <x>|*.'), "etc pass wd x (2026-11-14 2005).mkv"),
        (rec(title=" ... "), "Untitled (2026-11-14 2005).mkv"),
    ],
)
def test_recording_filename_follows_the_naming_table(recording: Recording, expected: str) -> None:
    assert recording_filename(recording, tz=UTC) == expected


def test_recording_filename_dates_use_the_given_zone_and_stay_inside_the_root() -> None:
    late = rec(title="Evening Quiz", starts_at=datetime(2026, 11, 15, 1, 30, tzinfo=UTC))
    assert recording_filename(late, tz=ZoneInfo("America/New_York")) == "Evening Quiz (2026-11-14 2030).mkv"
    long = recording_filename(rec(title="x" * 600, season=1, episode=1, subtitle="y" * 600), tz=UTC)
    assert all(0 < len(part.encode()) <= 255 for part in long.split("/"))
    hostile = recording_filename(rec(title="/abs/../..", season=1, episode=1, subtitle="../../x"), tz=UTC)
    assert not hostile.startswith("/") and ".." not in hostile.split("/")


# ---------------------------------------------------------------------- one programme
async def test_schedule_snapshots_the_programme_with_padding(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    pid = await env.programme(bbc, "Harbour Lights S02E05 The Storm", NOW + timedelta(hours=2), category="Drama")
    r = await env.svc.schedule_programme(env.admin, pid)
    assert r.state == "scheduled" and r.profile_id == env.admin.profile_id and r.rule_id is None
    assert (r.title, r.subtitle, r.season, r.episode, r.is_sport) == ("Harbour Lights", "The Storm", 2, 5, False)
    assert r.channel_id == hub(bbc) and r.channel_name == bbc.name
    assert r.programme_key == programme_key(hub(bbc), NOW + timedelta(hours=2))
    assert (r.starts_at, r.ends_at) == (NOW + timedelta(hours=2), NOW + timedelta(hours=3))
    assert (r.pad_before_s, r.pad_after_s) == (120, 300)
    # Asking again is the same recording, not a second one.
    assert (await env.svc.schedule_programme(env.admin, pid)).id == r.id
    assert len(await env.recordings()) == 1


async def test_sports_get_the_sixty_minute_tail(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    pid = await env.programme(bbc, "NBA: Boston Celtics vs Miami Heat", NOW + timedelta(hours=1), 150)
    r = await env.svc.schedule_programme(env.admin, pid)
    assert r.is_sport and (r.pad_before_s, r.pad_after_s) == (120, 3600)


async def test_schedule_rejects_ended_unknown_and_mismatched_programmes(env: Env) -> None:
    bbc, tf1 = await env.channel("bbc1.uk"), await env.channel("tf1.fr")
    ended = await env.programme(bbc, "Earlier", NOW - timedelta(hours=2))
    with pytest.raises(ServiceError) as exc:
        await env.svc.schedule_programme(env.admin, ended)
    assert exc.value.code == "programme_ended"
    with pytest.raises(NotFound):
        await env.svc.schedule_programme(env.admin, "prog:999999")
    with pytest.raises(NotFound):
        await env.svc.schedule_programme(env.admin, "tmdb:movie:603")
    upcoming = await env.programme(bbc, "Later", NOW + timedelta(hours=2))
    for stale in ((hub(tf1), None), (None, NOW + timedelta(hours=3))):
        with pytest.raises(ServiceError) as exc:
            await env.svc.schedule_programme(env.admin, upcoming, *stale)
        assert (exc.value.status, exc.value.code) == (409, "guide_changed")
    with pytest.raises(NotFound):
        await env.svc.schedule_programme(env.admin, upcoming, hub(tf1), NOW + timedelta(hours=2))
    assert await env.recordings() == []


async def test_recording_survives_a_guide_refresh(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    start = NOW + timedelta(hours=2)
    old = await env.programme(bbc, "Harbour Lights", start)
    r = await env.svc.schedule_programme(env.admin, old)
    # A refresh replaces every programme row of the feed, so the programme gets a new id.
    async with env.api.c.db.session() as s:
        await s.execute(delete(Programme).where(Programme.feed == bbc.source_id))
        await s.commit()
    await env.programme(bbc, "Filler", NOW)
    new = await env.programme(bbc, "Harbour Lights", start)
    assert new != old
    assert [x.id for x in await env.recordings()] == [r.id]
    payload = {
        hub(bbc): [
            {"id": new, "channel_id": hub(bbc), "start": start, "stop": start + timedelta(hours=1), "title": "x"},
            {"id": "prog:1", "channel_id": hub(bbc), "start": NOW, "stop": NOW + timedelta(hours=1), "title": "y"},
        ]
    }
    await env.svc.annotate(env.admin, payload)
    assert [p["recording_state"] for p in payload[hub(bbc)]] == ["scheduled", None]
    # And scheduling the refreshed programme finds the same recording.
    assert (await env.svc.schedule_programme(env.admin, new)).id == r.id
    # Row ids are handed out again, so the old id now names another programme. With the
    # channel and start the client saw, it still resolves to the programme that was meant.
    assert (await env.api.c.channels.programme(old)).title == "Filler"
    assert (await env.svc.schedule_programme(env.admin, old, hub(bbc), start)).id == r.id
    assert len(await env.recordings()) == 1


# ---------------------------------------------------------------------- rules
async def test_series_rule_records_every_matching_future_programme(env: Env) -> None:
    bbc, tf1 = await env.channel("bbc1.uk"), await env.channel("tf1.fr")
    await env.programme(bbc, "The Harbour Lights", NOW + timedelta(hours=1))
    await env.programme(tf1, "Harbour Lights S02E06 Calm", NOW + timedelta(days=1))
    await env.programme(bbc, "Harbour Lights Extra", NOW + timedelta(hours=3))
    await env.programme(bbc, "Harbour Lights", NOW - timedelta(minutes=30))  # already on air: not a future programme
    rule = await env.svc.add_rule(env.admin, "series", " Harbour Lights ", None, False, None)
    assert rule.match == "Harbour Lights" and rule.enabled
    rows = await env.recordings()
    assert [(r.title, r.channel_id, r.rule_id, r.state) for r in rows] == [
        ("The Harbour Lights", hub(bbc), rule.id, "scheduled"),
        ("Harbour Lights", hub(tf1), rule.id, "scheduled"),
    ]
    assert rows[1].subtitle == "Calm" and (rows[1].season, rows[1].episode) == (2, 6)
    # Re-applying after the next guide refresh creates nothing new.
    assert await env.svc.apply_rules() == 0
    await env.programme(bbc, "Harbour Lights", NOW + timedelta(days=2))
    assert await env.svc.apply_rules() == 1


async def test_rule_limited_to_a_channel(env: Env) -> None:
    bbc, tf1 = await env.channel("bbc1.uk"), await env.channel("tf1.fr")
    await env.programme(bbc, "Harbour Lights", NOW + timedelta(hours=1))
    await env.programme(tf1, "Harbour Lights", NOW + timedelta(hours=5))
    await env.svc.add_rule(env.admin, "series", "Harbour Lights", hub(tf1), False, None)
    assert [r.channel_id for r in await env.recordings()] == [hub(tf1)]


async def test_team_rule_records_games_only(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    await env.programme(bbc, "NBA: Boston Celtics vs Miami Heat", NOW + timedelta(hours=1), 150)
    await env.programme(bbc, "The Celtics Story", NOW + timedelta(hours=5), category="Documentary")
    await env.programme(bbc, "NBA: Chicago Bulls vs Miami Heat", NOW + timedelta(hours=8), 150)
    await env.svc.add_rule(env.admin, "team", "Celtics", None, False, None)
    rows = await env.recordings()
    assert [r.title for r in rows] == ["NBA: Boston Celtics vs Miami Heat"]
    assert rows[0].is_sport and rows[0].pad_after_s == 3600


async def test_keyword_rule_is_a_case_insensitive_substring(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    await env.programme(bbc, "Gardening Today", NOW + timedelta(hours=1))
    await env.programme(bbc, "The Great GARDEN Rescue", NOW + timedelta(hours=2))
    await env.programme(bbc, "Evening Quiz", NOW + timedelta(hours=3))
    await env.svc.add_rule(env.admin, "keyword", "garden", None, False, None)
    assert [r.title for r in await env.recordings()] == ["Gardening Today", "The Great GARDEN Rescue"]


async def test_new_only_skips_repeats_and_episodes_already_recorded(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    await env.programme(bbc, "Harbour Lights S02E05 The Storm", NOW + timedelta(hours=1))
    await env.programme(bbc, "Harbour Lights S02E05 The Storm", NOW + timedelta(hours=6))  # same episode again
    await env.programme(bbc, "Harbour Lights S02E06 Calm", NOW + timedelta(hours=8), category="Drama (Repeat)")
    await env.programme(bbc, "Harbour Lights S02E07 Tide", NOW + timedelta(hours=10))
    await env.svc.add_rule(env.admin, "series", "Harbour Lights", None, True, None)
    rows = await env.recordings()
    assert [(r.episode, r.starts_at) for r in rows] == [(5, NOW + timedelta(hours=1)), (7, NOW + timedelta(hours=10))]
    # The first airing is recorded; a later guide still offering the second one changes nothing.
    env.clock.advance(hours=3)
    assert await env.svc.apply_rules() == 0


async def test_without_new_only_every_airing_is_recorded(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    await env.programme(bbc, "Harbour Lights S02E05 The Storm", NOW + timedelta(hours=1))
    await env.programme(bbc, "Harbour Lights S02E05 The Storm", NOW + timedelta(hours=6), category="Repeat")
    await env.svc.add_rule(env.admin, "series", "Harbour Lights", None, False, None)
    assert len(await env.recordings()) == 2


async def test_same_event_on_several_channels_is_recorded_once(env: Env) -> None:
    second = await add_source(env.api, "Feed two")
    one, two = await env.channel("bbc1.uk"), await env.channel("bbc1.uk", second["id"])
    tf1 = await env.channel("tf1.fr")
    start = NOW + timedelta(hours=2)
    for ch in (two, one, tf1):
        await env.programme(ch, "NBA: Boston Celtics vs Miami Heat", start, 150)
    first = min((one, two, tf1), key=lambda c: (c.number, c.id))
    await env.svc.add_rule(env.admin, "team", "Celtics", None, False, None)
    assert [r.channel_id for r in await env.recordings()] == [hub(first)]
    # A rule naming a channel prefers it over the lowest number.
    other = max((one, two, tf1), key=lambda c: (c.number, c.id))
    viewer = await env.viewer()
    await env.svc.add_rule(viewer, "team", "Celtics", hub(other), False, None)
    mine = [r for r in await env.recordings() if r.profile_id == viewer.profile_id]
    assert [r.channel_id for r in mine] == [hub(other)]


async def test_series_duplicates_need_the_same_title_and_overlapping_times(env: Env) -> None:
    bbc, tf1 = await env.channel("bbc1.uk"), await env.channel("tf1.fr")
    await env.programme(bbc, "Garden Rescue", NOW + timedelta(hours=2))
    await env.programme(tf1, "Garden Rescue", NOW + timedelta(hours=2, minutes=30))  # overlaps: the same event
    await env.programme(tf1, "Garden Party", NOW + timedelta(hours=2))  # another programme at the same time
    await env.programme(tf1, "Garden Rescue", NOW + timedelta(hours=5))  # a later airing
    await env.svc.add_rule(env.admin, "keyword", "garden", None, False, None)
    first = min((bbc, tf1), key=lambda c: (c.number, c.id))
    assert sorted((r.title, r.starts_at, r.channel_id) for r in await env.recordings()) == [
        ("Garden Party", NOW + timedelta(hours=2), hub(tf1)),
        ("Garden Rescue", NOW + timedelta(hours=2 if first is bbc else 2.5), hub(first)),
        ("Garden Rescue", NOW + timedelta(hours=5), hub(tf1)),
    ]


async def test_different_episodes_at_the_same_time_are_both_recorded(env: Env) -> None:
    bbc, tf1 = await env.channel("bbc1.uk"), await env.channel("tf1.fr")
    await env.programme(bbc, "Harbour Lights S02E07 Tide", NOW + timedelta(hours=2))
    await env.programme(tf1, "Harbour Lights S01E01 Arrival", NOW + timedelta(hours=2))  # a marathon elsewhere
    await env.svc.add_rule(env.admin, "series", "Harbour Lights", None, False, None)
    assert sorted((r.season, r.episode) for r in await env.recordings()) == [(1, 1), (2, 7)]


async def test_cancelled_recording_is_not_scheduled_again_by_its_rule(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    pid = await env.programme(bbc, "Harbour Lights", NOW + timedelta(hours=1))
    await env.svc.add_rule(env.admin, "series", "Harbour Lights", None, False, None)
    (r,) = await env.recordings()
    await env.svc.cancel(env.admin, r.id)
    assert (await env.get(r.id)).state == "cancelled"
    assert await env.svc.apply_rules() == 0
    assert await env.svc.list(env.admin, state=None) == []
    # Asking for it again by hand brings the same row back.
    again = await env.svc.schedule_programme(env.admin, pid)
    assert again.id == r.id and again.state == "scheduled"


async def test_disabling_or_deleting_a_rule_drops_what_it_scheduled(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    await env.programme(bbc, "Harbour Lights", NOW + timedelta(hours=1))
    by_hand = await env.svc.schedule_programme(
        env.admin, await env.programme(bbc, "Evening Quiz", NOW + timedelta(hours=3))
    )
    rule = await env.svc.add_rule(env.admin, "series", "Harbour Lights", None, False, None)
    assert len(await env.recordings()) == 2
    rule = await env.svc.update_rule(env.admin, rule.id, {"enabled": False})
    assert not rule.enabled and [r.id for r in await env.recordings()] == [by_hand.id]
    await env.svc.update_rule(env.admin, rule.id, {"enabled": True, "keep": 3})
    assert len(await env.recordings()) == 2
    await env.svc.delete_rule(env.admin, rule.id)
    assert [r.id for r in await env.recordings()] == [by_hand.id]
    assert await env.svc.list_rules(env.admin) == []


async def test_rule_validation(env: Env) -> None:
    for kind, match, keep in [
        ("film", "x y z", None),
        ("keyword", "  ", None),
        ("keyword", "ab", None),
        ("series", "Harbour", 0),
    ]:
        with pytest.raises(ServiceError) as exc:
            await env.svc.add_rule(env.admin, kind, match, None, False, keep)
        assert exc.value.code == "invalid_request"
    with pytest.raises(NotFound):
        await env.svc.add_rule(env.admin, "series", "Harbour Lights", "ch:nope:nope", False, None)
    first = await env.svc.add_rule(env.admin, "series", "Harbour Lights", None, False, None)
    assert (await env.svc.add_rule(env.admin, "series", "the harbour lights", None, True, 5)).id == first.id
    async with env.api.c.db.session() as s:
        assert len(list((await s.execute(select(RecordingRule))).scalars())) == 1


# ---------------------------------------------------------------------- tick
async def scheduled(env: Env, title: str = "Harbour Lights", minutes: int = 60) -> Recording:
    bbc = await env.channel("bbc1.uk")
    return await env.svc.schedule_programme(
        env.admin, await env.programme(bbc, title, NOW + timedelta(hours=1), minutes)
    )


async def test_tick_starts_two_minutes_early_and_stops_five_minutes_late(env: Env) -> None:
    r = await scheduled(env)
    await env.tick(minutes=57, seconds=59)
    assert env.capture.attempts == [] and (await env.get(r.id)).state == "scheduled"
    await env.tick(seconds=1)
    assert env.capture.attempts == [r.id] and (await env.get(r.id)).state == "recording"
    await env.tick(minutes=66, seconds=59)  # 20:04:59
    assert env.capture.stopped == [] and (await env.get(r.id)).state == "recording"
    await env.tick(seconds=1)
    done = await env.get(r.id)
    assert env.capture.stopped == [r.id] and env.capture.attempts == [r.id]
    assert (done.state, done.path, done.size_bytes, done.error) == ("done", f"{r.id}.mkv", 4096, None)


async def test_sports_recording_runs_an_hour_past_the_listed_end(env: Env) -> None:
    r = await scheduled(env, "NBA: Boston Celtics vs Miami Heat", 120)
    await env.tick(minutes=58)
    await env.tick(hours=2, minutes=61)  # 59 minutes past the listed end
    assert (await env.get(r.id)).state == "recording"
    await env.tick(minutes=1)
    assert (await env.get(r.id)).state == "done"


async def test_busy_source_is_retried_each_minute_for_ten_minutes_then_missed(env: Env) -> None:
    r = await scheduled(env)
    env.capture.busy = True
    await env.tick(minutes=58)
    assert env.capture.attempts == [r.id]
    await env.tick(seconds=30)  # the scheduler ticks every 30 s; retries are a minute apart
    assert len(env.capture.attempts) == 1
    for _ in range(9):
        await env.tick(seconds=30)
        await env.tick(seconds=30)
    assert len(env.capture.attempts) == 10 and (await env.get(r.id)).state == "scheduled"
    await env.tick(minutes=1)
    missed = await env.get(r.id)
    assert len(env.capture.attempts) == 11
    assert missed.state == "missed" and "connection" in (missed.error or "")
    await env.tick(minutes=1)
    assert len(env.capture.attempts) == 11


async def test_a_connection_freed_within_the_window_starts_the_recording(env: Env) -> None:
    r = await scheduled(env)
    env.capture.busy = True
    await env.tick(minutes=58)
    env.capture.busy = False
    await env.tick(minutes=4)
    assert (await env.get(r.id)).state == "recording"


async def test_missed_recording_is_recovered_from_the_archive_after_the_programme(env: Env) -> None:
    r = await scheduled(env)
    env.capture.busy = True
    await env.tick(minutes=58)
    await env.tick(minutes=10)
    assert (await env.get(r.id)).state == "missed"
    env.capture.archive[r.id] = CaptureResult(path="archive.mkv", size_bytes=9000)
    await env.tick(minutes=30)  # still on air: nothing to fetch yet
    assert env.capture.recover_calls == []
    await env.tick(minutes=30)
    recovered = await env.get(r.id)
    assert env.capture.recover_calls == [r.id]
    assert (recovered.state, recovered.path, recovered.size_bytes, recovered.error) == (
        "done",
        "archive.mkv",
        9000,
        None,
    )


async def test_archive_recovery_is_attempted_once_and_missed_stays_missed_without_one(env: Env) -> None:
    r = await scheduled(env)
    other = await env.svc.schedule_programme(
        env.admin, await env.programme(await env.channel("tf1.fr"), "Le Journal", NOW + timedelta(hours=1))
    )
    env.capture.busy = True
    await env.tick(minutes=58)
    await env.tick(minutes=10)
    env.capture.archive[other.id] = CaptureResult(path=None, error="archive returned no data")
    for _ in range(3):
        await env.tick(hours=1)
    assert sorted(env.capture.recover_calls) == sorted([r.id, other.id])
    assert (await env.get(r.id)).state == "missed"
    failed = await env.get(other.id)
    assert failed.state == "missed" and "archive returned no data" in (failed.error or "")


async def test_archive_file_is_dropped_when_the_recording_was_deleted_meanwhile(env: Env) -> None:
    await scheduled(env)
    env.capture.busy = True
    await env.tick(minutes=58)
    await env.tick(minutes=10)
    fetched = env.root / "archive.mkv"

    async def slow(recording: Recording) -> CaptureResult:
        await env.svc.cancel(env.admin, recording.id)  # the user deletes the missed entry mid-fetch
        fetched.write_bytes(b"x")
        return CaptureResult(path=str(fetched), size_bytes=1)

    env.capture.recover_from_archive = slow
    await env.tick(hours=1)
    assert await env.recordings() == [] and not fetched.exists()


async def test_never_started_recording_is_missed_once_its_window_has_passed(env: Env) -> None:
    r = await scheduled(env)
    await env.tick(hours=3)  # the server was down for the whole programme
    assert env.capture.attempts == [] and (await env.get(r.id)).state == "missed"


async def test_low_free_space_stops_recordings_and_refuses_new_ones(env: Env) -> None:
    running = await scheduled(env)
    await env.tick(minutes=58)
    later = await env.svc.schedule_programme(
        env.admin, await env.programme(await env.channel("tf1.fr"), "Le Journal", NOW + timedelta(hours=1, minutes=30))
    )
    env.free["bytes"] = 50 * GB - 1
    await env.tick(minutes=30)
    stopped = await env.get(running.id)
    assert env.capture.stopped == [running.id] and env.capture.attempts == [running.id]
    assert stopped.state == "failed" and "free space" in (stopped.error or "") and stopped.path == f"{running.id}.mkv"
    refused = await env.get(later.id)
    assert refused.state == "failed" and "free space" in (refused.error or "")


async def test_capture_that_dies_early_is_failed_and_keeps_the_partial_file(env: Env) -> None:
    r = await scheduled(env)
    await env.tick(minutes=58)
    env.capture.running.clear()
    env.capture.results[r.id] = CaptureResult(path="partial.mkv", size_bytes=10, error="upstream closed")
    await env.tick(minutes=5)
    failed = await env.get(r.id)
    assert (failed.state, failed.path, failed.error) == ("failed", "partial.mkv", "upstream closed")


async def test_capture_error_at_the_end_is_a_failed_recording(env: Env) -> None:
    r = await scheduled(env)
    await env.tick(minutes=58)
    env.capture.results[r.id] = CaptureResult(path=None, error="ffmpeg exited with status 1")
    await env.tick(minutes=70)
    failed = await env.get(r.id)
    assert failed.state == "failed" and failed.error == "ffmpeg exited with status 1"


async def test_null_capture_fails_the_recording_when_it_is_due(env: Env) -> None:
    r = await scheduled(env)
    env.svc.capture = NullCapture()
    await env.tick(minutes=58)
    failed = await env.get(r.id)
    assert failed.state == "failed" and failed.error == "recording is not configured"


async def test_keep_prunes_the_oldest_finished_recordings_of_a_rule(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    for day in range(3):
        await env.programme(bbc, "Harbour Lights", NOW + timedelta(days=day, hours=1))
    tf1 = await env.channel("tf1.fr")
    untouched = await env.svc.schedule_programme(
        env.admin, await env.programme(tf1, "Evening Quiz", NOW + timedelta(hours=1))
    )
    rule = await env.svc.add_rule(env.admin, "series", "Harbour Lights", None, False, 2)
    first, second, third = [r for r in await env.recordings() if r.rule_id == rule.id]
    for r in (first, untouched, second, third):
        file = env.root / f"{r.id}.mkv"
        file.write_bytes(b"x")
        env.capture.results[r.id] = CaptureResult(path=str(file), size_bytes=1)
    await env.tick(minutes=58)
    await env.tick(hours=4)
    await env.tick(hours=20)
    await env.tick(hours=4)
    assert {r.id for r in await env.recordings() if r.state == "done"} == {first.id, second.id, untouched.id}
    await env.tick(hours=20)
    await env.tick(hours=4)
    assert {r.id for r in await env.recordings()} == {second.id, third.id, untouched.id}
    assert not (env.root / f"{first.id}.mkv").exists() and (env.root / f"{second.id}.mkv").exists()


async def test_cancel_stops_a_running_capture_and_removes_the_partial_file(env: Env) -> None:
    r = await scheduled(env)
    partial = env.root / "partial.mkv"
    partial.write_bytes(b"x")
    env.capture.results[r.id] = CaptureResult(path=str(partial), size_bytes=1)
    await env.tick(minutes=58)
    await env.svc.cancel(env.admin, r.id)
    cancelled = await env.get(r.id)
    assert env.capture.stopped == [r.id] and cancelled.state == "cancelled" and cancelled.path is None
    assert not partial.exists()
    # Tombstones are dropped once the programme is over.
    await env.tick(hours=2)
    assert await env.recordings() == []


async def test_deleting_a_finished_recording_removes_its_file_but_never_one_outside_the_root(
    env: Env, tmp_path: Path
) -> None:
    r = await scheduled(env)
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"x")
    env.capture.results[r.id] = CaptureResult(path=str(outside), size_bytes=1)
    await env.tick(minutes=58)
    await env.tick(minutes=70)
    await env.svc.cancel(env.admin, r.id)
    assert await env.recordings() == [] and outside.exists()


# ---------------------------------------------------------------------- visibility and switches
async def test_users_see_their_own_recordings_and_admins_see_all(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    viewer, other = await env.viewer("One"), await env.viewer("Two")
    mine = await env.svc.schedule_programme(
        viewer, await env.programme(bbc, "Harbour Lights", NOW + timedelta(hours=1))
    )
    await env.svc.schedule_programme(env.admin, await env.programme(bbc, "Evening Quiz", NOW + timedelta(hours=3)))
    rule = await env.svc.add_rule(viewer, "keyword", "nothing matches", None, False, None)
    assert [r.id for r in await env.svc.list(viewer, state=None)] == [mine.id]
    assert await env.svc.list(other, state=None) == []
    assert len(await env.svc.list(env.admin, state=None)) == 2
    assert [r.id for r in await env.svc.list(env.admin, state="scheduled")] == [mine.id, (await env.recordings())[1].id]
    assert [r.id for r in await env.svc.list_rules(viewer)] == [rule.id]
    assert await env.svc.list_rules(other) == [] and len(await env.svc.list_rules(env.admin)) == 1
    with pytest.raises(NotFound):
        await env.svc.cancel(other, mine.id)
    with pytest.raises(NotFound):
        await env.svc.delete_rule(other, rule.id)
    # One file per programme for the household: someone else's recording cannot be scheduled twice.
    with pytest.raises(ServiceError) as exc:
        await env.svc.schedule_programme(other, await env.programme(bbc, "Harbour Lights", NOW + timedelta(hours=1)))
    assert exc.value.code == "already_scheduled"
    with pytest.raises(ServiceError) as exc:
        await env.svc.list(viewer, state="bogus")
    assert exc.value.code == "invalid_request"
    await env.svc.cancel(env.admin, mine.id)
    assert (await env.get(mine.id)).state == "cancelled"


async def test_kids_profiles_cannot_schedule(env: Env) -> None:
    bbc = await env.channel("bbc1.uk")
    kid = await env.viewer("Kid", kids=True)
    pid = await env.programme(bbc, "Harbour Lights", NOW + timedelta(hours=1))
    with pytest.raises(Forbidden) as exc:
        await env.svc.schedule_programme(kid, pid)
    assert exc.value.code == "recording_forbidden"
    with pytest.raises(Forbidden):
        await env.svc.add_rule(kid, "series", "Harbour Lights", None, False, None)
    assert await env.svc.list(kid, state=None) == []


async def test_off_until_a_recordings_directory_is_configured(env: Env, tmp_path: Path) -> None:
    bbc = await env.channel("bbc1.uk")
    pid = await env.programme(bbc, "Harbour Lights", NOW + timedelta(hours=1))
    r = await env.svc.schedule_programme(env.admin, pid)
    rule = await env.svc.add_rule(env.admin, "keyword", "quiz", None, False, None)
    for missing in ("", str(tmp_path / "not-created")):
        off = RecorderService(
            env.api.c.db, env.api.c.channels, recordings_dir=missing, capture=env.capture, clock=env.clock
        )
        assert not off.enabled
        for call in (
            off.schedule_programme(env.admin, pid),
            off.cancel(env.admin, r.id),
            off.add_rule(env.admin, "series", "Harbour Lights", None, False, None),
            off.update_rule(env.admin, rule.id, {"enabled": False}),
            off.delete_rule(env.admin, rule.id),
        ):
            with pytest.raises(ServiceError) as exc:
                await call
            assert (exc.value.status, exc.value.code) == (409, "recording_unavailable")
        assert await off.list(env.admin, state=None) == [] and await off.list_rules(env.admin) == []
        assert await off.apply_rules() == 0
        await off.tick(env.clock.advance(hours=1))
        payload = [{"id": pid, "channel_id": hub(bbc), "start": r.starts_at, "stop": r.ends_at}]
        assert (await off.annotate(env.admin, payload))[0].get("recording_state") is None
    assert env.capture.attempts == [] and not (tmp_path / "not-created").exists()
    assert env.svc.enabled and (await env.get(r.id)).state == "scheduled"


async def test_rules_and_scheduling_respect_the_profiles_content_policy(api: Api, tmp_path: Path) -> None:
    from app.services.classification import classification_record, summarize
    from tests.test_content_policy import activate, seed

    policy = await seed(api)
    await activate(api)
    now = datetime.now(UTC).replace(microsecond=0)
    rated = summarize("xmltv", "programme", [classification_record("US", "US-TVPG", "TV-G")])
    listings = [
        ("On now", now - timedelta(minutes=10), rated),
        ("Harbour Lights", now + timedelta(hours=2), rated),
        ("Harbour Lights", now + timedelta(hours=5), {}),  # unrated: blocked for a restricted profile
    ]
    async with api.c.db.session() as s:
        s.add(ChannelGroup(id="group", source_id="fixture", name="Group"))
        rows = [
            Programme(
                feed="fixture", epg_channel="epg", title=t, start=at, stop=at + timedelta(hours=1), age_evidence=ev
            )
            for t, at, ev in listings
        ]
        s.add_all(rows)
        await s.commit()
    root = tmp_path / "recordings"
    root.mkdir()
    svc = RecorderService(api.c.db, api.c.channels, recordings_dir=str(root), capture=FakeCapture())
    svc.content_policy = policy
    child = Principal(profile_id="child", permissions=BUILTIN_ROLES["family"].permissions)
    admin = Principal(profile_id=api.profile_id, is_admin=True, kids=False)

    await svc.add_rule(child, "series", "Harbour Lights", None, False, None)
    assert [r.starts_at for r in await svc.list(child, state=None)] == [now + timedelta(hours=2)]
    with pytest.raises(Forbidden) as exc:
        await svc.schedule_programme(child, ids.prog_id(rows[2].id))
    assert exc.value.code == "content_restricted"
    # An unrestricted profile's rule sees the whole guide; the rated airing is already the household's.
    await svc.add_rule(admin, "series", "Harbour Lights", None, False, None)
    mine = [r for r in await svc.list(admin, state=None) if r.profile_id == admin.profile_id]
    assert [r.starts_at for r in mine] == [now + timedelta(hours=5)]


# ---------------------------------------------------------------------- wiring
async def test_rules_follow_each_guide_refresh_and_the_tick_runs_every_thirty_seconds(api: Api) -> None:
    assert api.c.sync.after_epg_refresh == api.c.recorder.apply_rules
    assert isinstance(api.c.recorder.capture, NullCapture) and not api.c.recorder.enabled
    ticks: list[int] = []

    async def tick() -> None:
        ticks.append(1)

    scheduler = SyncScheduler(api.c.sync, None, tick)
    scheduler.start()
    try:
        job = scheduler._sched.get_job("recorder")
        assert job is not None and job.trigger.interval.total_seconds() == 30
    finally:
        scheduler.shutdown()
    plain = SyncScheduler(api.c.sync)
    plain.start()
    try:
        assert plain._sched.get_job("recorder") is None
    finally:
        plain.shutdown()


async def test_run_tick_uses_the_injected_clock(env: Env) -> None:
    r = await scheduled(env)
    env.clock.advance(minutes=58)
    await env.svc.run_tick()
    assert (await env.get(r.id)).state == "recording"
