"""A watch room made from a show is on one episode: the creator's next, named when the room is made.

The rig of ``test_api_rooms`` with a three-episode series next to its film. Without a connection
limit every member resolves the room's title for themselves; with one they share a session.
"""

from __future__ import annotations

from typing import Any

import respx

from app.infra.models import Progress, Title
from tests import test_api_rooms, test_room_upstream
from tests.test_api_rooms import Rig
from tests.test_room_upstream import Ffmpeg, limit
from tests.test_upstream_session_pin import Panel

rig = test_api_rooms.rig
ffmpeg = test_room_upstream.ffmpeg


def episode_url(number: int) -> str:
    return f"http://streams.example/series/1/{number}.mkv"


def series(rig: Rig, episodes: int = 3) -> tuple[str, list[str]]:
    """Add a show with one season. Returns its id and its episodes' ids, in order."""

    def row(ext_id: str, kind: str, title: str, **fields: Any) -> Title:
        return Title(source_id=rig.source_id, ext_id=ext_id, kind=kind, title=title, norm_title=title.lower(), **fields)

    async def add() -> None:
        async with rig.c.db.session() as s:
            s.add(row("show1", "series", "Harbour Lights"))
            s.add(row("show1s1", "season", "Season 1", parent_ext_id="show1", season_number=1))
            for n in range(1, episodes + 1):
                s.add(
                    row(
                        f"show1e{n}",
                        "episode",
                        f"Part {n}",
                        parent_ext_id="show1s1",
                        season_number=1,
                        episode_number=n,
                        duration_ms=1_800_000,
                        extra={"stream_url": episode_url(n)},
                    )
                )
            await s.commit()

    rig.call(add)
    return f"lib:{rig.source_id}:show1", [f"lib:{rig.source_id}:show1e{n}" for n in range(1, episodes + 1)]


def finished(rig: Rig, profile_id: str, *refs: str) -> None:
    async def add() -> None:
        async with rig.c.db.session() as s:
            for ref in refs:
                s.add(Progress(profile_id=profile_id, ref_id=ref, position_ms=0, duration_ms=1, completed=True))
            await s.commit()

    rig.call(add)


def resolved(rig: Rig, ref: str, headers: dict[str, str] | None = None) -> str:
    """The id ``POST /playback/resolve`` plays for this caller."""
    r = rig.req(
        "POST", "/playback/resolve", headers, json={"id": ref, "recovery_generation": rig.c.proxy.recovery_epoch}
    )
    assert r.status_code == 200, r.text
    return str(r.json()["id"])


def viewers(rig: Rig, episodes: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """The host has seen episode 1. One guest is two episodes in, the other has seen nothing."""
    finished(rig, rig.admin_id, episodes[0])
    ahead, behind = rig.profile("ahead", "Ahead"), rig.profile("behind", "Behind")
    finished(rig, "ahead", episodes[0], episodes[1])
    return ahead, behind


def test_a_room_made_from_a_show_is_on_the_creators_next_episode_for_everyone(rig: Rig) -> None:
    show, episodes = series(rig)
    ahead, behind = viewers(rig, episodes)
    # Left to themselves, the three of them would each be given a different episode.
    assert [resolved(rig, show, who) for who in (None, ahead, behind)] == [episodes[1], episodes[2], episodes[0]]

    room = rig.room(show)
    code = room["code"]
    assert (room["id"], room["variant_id"], room["live"]) == (episodes[1], None, False)
    title = room["title"]
    assert (title["id"], title["kind"], title["title"]) == (episodes[1], "episode", "Part 2")
    assert title["subtitle"] == "Harbour Lights · S01E02"
    shown = rig.req("GET", f"/rooms/{code}", behind)
    assert shown.status_code == 200 and shown.json() == {**room, "is_host": False}

    for who in (behind, ahead, None):
        descriptor = rig.join(code, who)["descriptor"]
        assert descriptor["id"] == episodes[1], descriptor
        # The owner manages the sources and is given the episode's own URL; a viewer plays the same
        # episode through this server and never sees where it lives.
        if who is None:
            assert descriptor["url"] == episode_url(2), descriptor
        else:
            assert descriptor["mode"] == "proxy" and "/api/v1/stream/" in descriptor["url"], descriptor
            assert episode_url(2) not in repr(descriptor)
    # The room stays on its episode when its creator's own history moves on.
    finished(rig, rig.admin_id, episodes[1])
    assert resolved(rig, show) == episodes[2]
    assert rig.join(code, behind)["descriptor"]["id"] == episodes[1]
    assert rig.req("GET", f"/rooms/{code}").json()["id"] == episodes[1]


def test_a_shared_stream_room_made_from_a_show_plays_that_episode_whoever_joins_first(rig: Rig, ffmpeg: Ffmpeg) -> None:
    show, episodes = series(rig)
    ahead, behind = viewers(rig, episodes)
    limit(rig)
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        Panel(router, episode_url(2))
        room = rig.room(show)
        code = room["code"]
        assert room["id"] == episodes[1] and room["title"]["subtitle"] == "Harbour Lights · S01E02"
        # Not the host first: the session is still started on the room's episode, not the guest's.
        first = rig.join(code, behind)["descriptor"]
        second = rig.join(code, ahead, client="android_tv")["descriptor"]
        third = rig.join(code)["descriptor"]
    for descriptor in (first, second, third):
        assert (descriptor["id"], descriptor["mode"], descriptor["stream_epoch"]) == (episodes[1], "hls", 1)
    session = rig.c.room_playback.session(code)
    assert session is not None and (session.ref, session.variant_id) == (episodes[1], episodes[1])
    assert len(ffmpeg.runs) == 1 and str(ffmpeg.arg(0, "-i")).startswith(episode_url(2))


def test_a_named_variant_of_a_show_becomes_the_episode_it_plays(rig: Rig) -> None:
    show, episodes = series(rig)
    room = rig.room(show, variant_id=show, audio_track=1)
    assert (room["id"], room["variant_id"], room["audio_track"]) == (episodes[0], episodes[0], 1)
    assert rig.join(room["code"])["descriptor"]["id"] == episodes[0]
    # A season is a show cut short: its next episode, by the same rule.
    season = rig.room(f"lib:{rig.source_id}:show1s1")
    assert season["id"] == episodes[0] and season["title"]["kind"] == "episode"
    # A variant that is not one of the show's is refused before a room exists.
    r = rig.req("POST", "/rooms", json={"id": show, "variant_id": rig.film})
    assert r.status_code == 404, r.text
    assert len(rig.c.rooms.registry) == 2


def test_rooms_on_an_episode_or_a_film_are_on_what_was_asked_for(rig: Rig) -> None:
    _, episodes = series(rig)
    room = rig.room(episodes[2])
    assert (room["id"], room["title"]["title"], room["title"]["subtitle"]) == (episodes[2], "Part 3", "S01E03")
    assert rig.join(room["code"])["descriptor"]["id"] == episodes[2]
    assert rig.room()["id"] == rig.film


def test_a_show_without_episodes_makes_no_room(rig: Rig) -> None:
    show, _ = series(rig, episodes=0)
    r = rig.req("POST", "/rooms", json={"id": show})
    assert r.status_code == 404, r.text
    assert len(rig.c.rooms.registry) == 0
