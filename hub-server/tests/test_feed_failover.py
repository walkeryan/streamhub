"""Switching to another feed of a game: the alternates a player asks for."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.infra.models import Channel, ChannelGroup, Programme, Source
from app.services.classification import classification_record, summarize
from app.services.sports import feed_alternates
from tests.conftest import Api
from tests.people import member

NOW = datetime.now(UTC).replace(microsecond=0)
GAME = "NHL: Ravens vs Otters"


def listing(channel: str, title: str = GAME, code: str | None = "TV-G", feed: str = "games") -> Programme:
    rating = summarize("xmltv", "programme", [classification_record("US", "US-TVPG", code)]) if code else {}
    return Programme(
        feed=feed,
        epg_channel=channel,
        start=NOW - timedelta(minutes=30),
        stop=NOW + timedelta(minutes=90),
        title=title,
        age_evidence=rating,
    )


@pytest.fixture
async def games(api: Api) -> Api:
    """One game on four channels (one of them hidden, one rated for adults), and a studio show on one."""
    async with api.c.db.session() as s:
        s.add(Source(id="games", name="Games", type="xtream", config_enc="unused"))
        await s.flush()
        s.add_all(
            [
                ChannelGroup(id="open", source_id="games", name="Open"),
                ChannelGroup(id="shut", source_id="games", name="Shut", hidden=True),
                *(
                    Channel(
                        source_id="games",
                        ext_id=ext,
                        name=name,
                        number=number,
                        epg_id=ext,
                        group_id=group,
                        group_name=group.title(),
                    )
                    for ext, name, number, group in [
                        ("north", "Harbor Sports 1", 11, "open"),
                        ("south", "Harbor Sports 2", 12, "open"),
                        ("east", "Ridge Sports", 13, "open"),
                        ("late", "Ridge Late", 14, "open"),
                        ("back", "Backroom Sports", 15, "shut"),
                        ("solo", "Lone Arena", 16, "open"),
                    ]
                ),
                listing("north"),
                listing("south"),
                listing("east"),
                listing("late", code="TV-MA"),
                listing("back"),
                listing("solo", "NBA: Herons vs Lynx"),
            ]
        )
        await s.commit()
    return api


async def test_alternates_go_round_the_game_from_the_channel_after_this_one(games: Api) -> None:
    api = games
    found = await feed_alternates(api.c.channels, api.profile_id, "ch:games:south", now=NOW)
    assert found["title"] == GAME and found["event_id"]
    # In the event's own order (by channel number), starting after the channel itself and wrapping round.
    assert [a["channel_id"] for a in found["alternates"]] == ["ch:games:east", "ch:games:late", "ch:games:north"]
    assert found["alternates"][0]["channel_name"] == "Ridge Sports" and found["alternates"][0]["channel_number"] == 13
    assert all(a["programme_id"].startswith("prog:") for a in found["alternates"])
    # A hidden group's channel is no feed for anyone, as on the Sports screen.
    assert all(a["channel_id"] != "ch:games:back" for a in found["alternates"])


async def test_no_alternates_without_a_second_feed_or_a_current_game(games: Api) -> None:
    api = games
    for ref in ("ch:games:solo", "ch:games:missing", "lib:games:1", "ch:games:back"):
        assert (await feed_alternates(api.c.channels, api.profile_id, ref, now=NOW))["alternates"] == []
    later = await feed_alternates(api.c.channels, api.profile_id, "ch:games:north", now=NOW + timedelta(hours=3))
    assert later == {"event_id": None, "title": None, "alternates": []}


async def test_alternates_endpoint_answers_within_the_viewers_ceiling(games: Api) -> None:
    api = games
    r = await api.get("/playback/alternates", params={"id": "ch:games:north"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    assert [a["channel_id"] for a in r.json()["alternates"]] == ["ch:games:south", "ch:games:east", "ch:games:late"]
    kid = await member(api, "kid", "Kid")
    r = await kid.get("/playback/alternates", params={"id": "ch:games:north"})
    assert r.status_code == 200, r.text
    # The feed whose programme is above the kid's ceiling is never offered, here or on the Sports screen.
    assert [a["channel_id"] for a in r.json()["alternates"]] == ["ch:games:south", "ch:games:east"]
    sports = await kid.get("/sports/events", params={"status": "current", "language": "all"})
    listed = {f["channel_id"] for e in sports.json()["events"] if e["title"] == GAME for f in e["feeds"]}
    assert listed == {"ch:games:north", "ch:games:south", "ch:games:east"}
    assert (await api.get("/playback/alternates")).status_code == 422
    assert (await api.client.get("/api/v1/playback/alternates", params={"id": "ch:games:north"})).status_code == 401
