"""Team facts for football listings: names, matching, scoreboard and summary parsing, caching, failures.

Every team here is fictional and every answer is a fixture shaped like ESPN's public site API.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.sports_facts import (
    COLLEGE,
    NFL,
    SportsFacts,
    espn_days,
    is_football,
    leagues_for,
    match_game,
    normalise_name,
    parse_probability,
    parse_scoreboard,
)

FIXTURES = Path(__file__).parent / "fixtures" / "espn"
KICKOFF = datetime(2026, 11, 14, 17, tzinfo=UTC)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def event(home: str, away: str, start: datetime = KICKOFF, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"{away}@{home}",
        "kind": "event",
        "title": f"{away} at {home}",
        "sport": "American football",
        "league": None,
        "home": home,
        "away": away,
        "start": start,
        "stop": start + timedelta(hours=3, minutes=30),
        "status": "current",
        "replay": False,
        **extra,
    }


class Espn:
    """A scoreboard and summaries served by an ``httpx.MockTransport``, counting what was asked."""

    def __init__(self, board: Any = None, summaries: dict[str, Any] | None = None) -> None:
        self.board = board if board is not None else fixture("scoreboard_college.json")
        self.summaries = (
            summaries
            if summaries is not None
            else {
                "401990001": fixture("summary_live.json"),
                "401990002": fixture("summary_pregame.json"),
            }
        )
        self.calls: list[httpx.URL] = []
        self.fail = False
        self.delay = 0.0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            return httpx.Response(503, text="unavailable")
        if request.url.path.endswith("/scoreboard"):
            if "/nfl/" in request.url.path:
                return httpx.Response(200, json={"events": []})
            return httpx.Response(200, json=self.board)
        if request.url.path.endswith("/summary"):
            found = self.summaries.get(request.url.params.get("event", ""))
            return httpx.Response(200, json=found) if found else httpx.Response(404)
        return httpx.Response(404)

    def factory(self, **kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def asked(self, part: str) -> int:
        return sum(1 for url in self.calls if url.path.endswith(part))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("No. 12 Brookfield", "brookfield"),
        ("#5 Harbor Herons", "harbor herons"),
        ("The University of Lakeview", "lakeview"),
        ("Lakeview University", "lakeview"),
        ("Brookfield St.", "brookfield state"),
        ("Brookfield State", "brookfield state"),
        ("Ríverside Ötters", "riverside otters"),
        ("Port-Kestrel A&M", "port kestrel a m"),
        ("Hawai'i Herons", "hawaii herons"),
    ],
)
def test_names_are_normalised(spelling: str, expected: str) -> None:
    assert normalise_name(spelling) == expected


def test_football_and_league_hints() -> None:
    assert is_football("American football") and is_football("Football") and is_football("College Football")
    assert not is_football("Soccer") and not is_football("Australian Football") and not is_football(None)
    assert leagues_for({"title": "NFL: Herons at Otters", "sport": "American football"}) == (NFL,)
    assert leagues_for({"title": "Herons at Otters", "sport": "College Football"}) == (COLLEGE,)
    assert leagues_for({"title": "Herons at Otters", "sport": "Football"}) == (COLLEGE, NFL)
    # ESPN's days are Eastern: a late kickoff in UTC is still the evening before.
    assert {d.isoformat() for d in espn_days(datetime(2026, 11, 15, 0, 30, tzinfo=UTC))} == {"2026-11-14"}
    assert len(espn_days(datetime(2026, 11, 15, 3, 30, tzinfo=UTC))) == 2


def test_scoreboard_is_parsed_without_scores() -> None:
    games = parse_scoreboard(COLLEGE, fixture("scoreboard_college.json"))
    assert [g.id for g in games] == ["401990001", "401990002"]
    live, later = games
    assert live.state == "in" and live.detail == "Q2 7:14" and live.start == KICKOFF
    assert live.home.api() == {
        "name": "Brookfield Badgers",
        "short_name": "Brookfield",
        "color": "#7a1f3d",
        "alt_color": "#f2c14e",
        "rank": 12,
        "record": "8-1",
        "conference_record": "5-1 Lakes League",
    }
    assert live.away.rank is None and live.away.color == "#2b4c9a" and live.away.record == "6-3"
    # Not a conference game: the conference record is not given a conference name.
    assert later.state == "pre" and later.detail is None
    assert later.away.rank == 5 and later.away.conference_record == "6-0"
    assert "score" not in json.dumps([g.home.api() for g in games])


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            {
                "period": 2,
                "displayClock": "0:00",
                "type": {"name": "STATUS_HALFTIME", "state": "in", "shortDetail": "Halftime"},
            },
            "Halftime",
        ),
        ({"period": 3, "displayClock": "0:00", "type": {"name": "STATUS_END_PERIOD", "state": "in"}}, "End of Q3"),
        ({"period": 5, "displayClock": "2:01", "type": {"name": "STATUS_IN_PROGRESS", "state": "in"}}, "OT 2:01"),
        (
            {
                "period": 4,
                "displayClock": "0:00",
                "type": {"name": "STATUS_FINAL", "state": "post", "shortDetail": "Final"},
            },
            "Final",
        ),
    ],
)
def test_status_detail(status: dict[str, Any], expected: str) -> None:
    board = fixture("scoreboard_college.json")
    board["events"][0]["competitions"][0]["status"] = status
    assert parse_scoreboard(COLLEGE, board)[0].detail == expected


def test_unreadable_games_are_left_out() -> None:
    board = fixture("scoreboard_college.json")
    del board["events"][0]["competitions"][0]["competitors"][0]["homeAway"]
    board["events"].append({"id": "x"})
    assert [g.id for g in parse_scoreboard(COLLEGE, board)] == ["401990002"]
    assert parse_scoreboard(COLLEGE, None) == [] and parse_scoreboard(COLLEGE, {"events": None}) == []


def test_probability_parsing() -> None:
    assert parse_probability(fixture("summary_live.json"), "in") == {"away": 14.6, "home": 85.4, "live": True}
    assert parse_probability(fixture("summary_pregame.json"), "pre") == {"away": 61.8, "home": 38.2, "live": False}
    # Never made up, never after the final whistle.
    assert parse_probability({}, "pre") is None
    assert parse_probability({"winprobability": []}, "in") is None
    assert parse_probability({"winprobability": [{"homeWinPercentage": 1.7}]}, "in") is None
    assert parse_probability(fixture("summary_live.json"), "post") is None
    assert parse_probability({"predictor": {"homeTeam": {"gameProjection": "70"}}}, "pre") == {
        "away": 30.0,
        "home": 70.0,
        "live": False,
    }


def test_matching_either_order_window_and_ambiguity() -> None:
    games = parse_scoreboard(COLLEGE, fixture("scoreboard_college.json"))
    # Either order, any of the spellings: the scoreboard's home and away win.
    assert match_game(event("Brookfield Badgers", "Lakeview Kestrels"), games).id == "401990001"
    assert match_game(event("Lakeview", "No. 12 Brookfield"), games).id == "401990001"
    assert match_game(event("BRK", "Kestrels"), games).id == "401990001"
    # Three hours either side of kickoff, no more.
    assert match_game(event("Brookfield", "Lakeview", KICKOFF + timedelta(hours=2, minutes=59)), games) is not None
    assert match_game(event("Brookfield", "Lakeview", KICKOFF - timedelta(hours=3, minutes=1)), games) is None
    # Only one side matching is no match.
    assert match_game(event("Brookfield", "Riverside"), games) is None
    # Two candidates are no match.
    twin = copy.deepcopy(fixture("scoreboard_college.json")["events"][0])
    twin["id"] = "401990009"
    twice = parse_scoreboard(COLLEGE, {"events": [*fixture("scoreboard_college.json")["events"], twin]})
    assert match_game(event("Brookfield", "Lakeview"), twice) is None


async def test_annotate_live_game_with_facts_and_probability() -> None:
    espn = Espn()
    facts = SportsFacts(espn.factory)
    events = [event("Lakeview", "Brookfield"), {**event("x", "y"), "sport": "Basketball"}]
    await facts.annotate(events)
    matchup = events[0]["matchup"]
    # The listing had the sides the other way round: the scoreboard decides.
    assert matchup["home"]["name"] == "Brookfield Badgers" and matchup["away"]["name"] == "Lakeview Kestrels"
    assert matchup["home"]["rank"] == 12 and matchup["away"]["rank"] is None
    assert matchup["status_detail"] == "Q2 7:14"
    assert matchup["win_probability"] == {"away": 14.6, "home": 85.4, "live": True}
    assert matchup["art_url"] is None
    assert events[1]["matchup"] is None
    # College named nowhere: both scoreboards are read, once each.
    assert espn.asked("/scoreboard") == 2 and espn.asked("/summary") == 1
    sent = next(url for url in espn.calls if "college-football/scoreboard" in url.path)
    assert sent.params["dates"] == "20261114" and sent.params["groups"] == "80"


async def test_pregame_prediction_and_upcoming_games_skip_the_summary() -> None:
    espn = Espn()
    facts = SportsFacts(espn.factory)
    pre = event("Riverside", "Harbor", datetime(2026, 11, 14, 20, 30, tzinfo=UTC))
    upcoming = {**event("Brookfield", "Lakeview"), "status": "upcoming"}
    await facts.annotate([pre, upcoming])
    assert pre["matchup"]["win_probability"] == {"away": 61.8, "home": 38.2, "live": False}
    assert pre["matchup"]["away"]["rank"] == 5 and pre["matchup"]["status_detail"] is None
    # A row shows no bar: no summary is asked for a game still to come.
    assert upcoming["matchup"]["win_probability"] is None and upcoming["matchup"]["home"]["rank"] == 12
    assert espn.asked("/summary") == 1


async def test_replay_shows_no_clock_or_probability() -> None:
    espn = Espn()
    facts = SportsFacts(espn.factory)
    replay = event("Brookfield", "Lakeview", replay=True)
    await facts.annotate([replay])
    assert replay["matchup"]["home"]["rank"] == 12
    assert replay["matchup"]["status_detail"] is None and replay["matchup"]["win_probability"] is None
    assert espn.asked("/summary") == 0


async def test_unmatched_games_keep_guide_names() -> None:
    facts = SportsFacts(Espn().factory)
    other = event("Northfield Owls", "Southgate Foxes")
    await facts.annotate([other])
    assert other["matchup"] == {
        "away": {
            "name": "Southgate Foxes",
            "short_name": "Southgate Foxes",
            "color": None,
            "alt_color": None,
            "rank": None,
            "record": None,
            "conference_record": None,
        },
        "home": {
            "name": "Northfield Owls",
            "short_name": "Northfield Owls",
            "color": None,
            "alt_color": None,
            "rank": None,
            "record": None,
            "conference_record": None,
        },
        "status_detail": None,
        "win_probability": None,
        "art_url": None,
    }
    # One side unknown, or a game channel with no date: no matchup.
    lone, channel = event("Northfield Owls", ""), {**event("Brookfield", "Lakeview"), "kind": "channel"}
    await facts.annotate([lone, channel])
    assert lone["matchup"] is None and channel["matchup"] is None


async def test_cache_expiry_live_and_idle() -> None:
    espn, clock = Espn(), Clock()
    facts = SportsFacts(espn.factory, clock=clock, now=lambda: KICKOFF)
    await facts.annotate([event("Brookfield", "Lakeview")])
    await facts.annotate([event("Brookfield", "Lakeview")])
    assert espn.asked("/scoreboard") == 2 and espn.asked("/summary") == 1
    # A game is on: its scoreboard and the live summary are a minute old at most. The other
    # scoreboard, with nothing on, keeps its fifteen minutes.
    clock.now += 61
    await facts.annotate([event("Brookfield", "Lakeview")])
    assert espn.asked("/scoreboard") == 3 and espn.asked("/summary") == 2
    # Nothing on and nothing about to start: fifteen minutes.
    espn.board = {"events": [e for e in fixture("scoreboard_college.json")["events"] if e["id"] == "401990002"]}
    espn.board["events"][0]["date"] = espn.board["events"][0]["competitions"][0]["date"] = "2026-11-14T23:30Z"
    clock.now += 61
    await facts.annotate([event("Brookfield", "Lakeview")])
    calls = espn.asked("/scoreboard")
    clock.now += 600
    await facts.annotate([event("Brookfield", "Lakeview")])
    assert espn.asked("/scoreboard") == calls
    clock.now += 301
    await facts.annotate([event("Brookfield", "Lakeview")])
    assert espn.asked("/scoreboard") == calls + 2


async def test_failures_degrade_to_no_facts_and_are_not_retried_at_once() -> None:
    espn, clock = Espn(), Clock()
    espn.fail = True
    facts = SportsFacts(espn.factory, clock=clock, now=lambda: KICKOFF)
    listed = event("Brookfield", "Lakeview")
    await facts.annotate([listed])
    assert listed["matchup"]["home"]["name"] == "Brookfield" and listed["matchup"]["win_probability"] is None
    asked = len(espn.calls)
    await facts.annotate([event("Brookfield", "Lakeview")])
    assert len(espn.calls) == asked
    espn.fail = False
    clock.now += 61
    again = event("Brookfield", "Lakeview")
    await facts.annotate([again])
    assert again["matchup"]["home"]["rank"] == 12
    # Garbage is a failure too.
    espn.board = "not a scoreboard"
    clock.now += 61
    garbage = event("Brookfield", "Lakeview")
    await facts.annotate([garbage])
    assert garbage["matchup"]["home"]["rank"] is None


async def test_a_slow_lookup_never_holds_the_response_and_fills_the_cache() -> None:
    espn = Espn()
    espn.delay = 0.3
    facts = SportsFacts(espn.factory, budget_s=0.05)
    first = event("Brookfield", "Lakeview")
    loop = asyncio.get_running_loop()
    began = loop.time()
    await facts.annotate([first])
    assert loop.time() - began < 0.25
    assert first["matchup"]["home"]["rank"] is None
    await asyncio.sleep(0.4)
    espn.delay = 0.0
    second = event("Brookfield", "Lakeview")
    await facts.annotate([second])
    assert second["matchup"]["home"]["rank"] == 12
    await facts.shutdown()


async def test_disabled_asks_nothing() -> None:
    espn = Espn()
    facts = SportsFacts(espn.factory, enabled=False)
    listed = event("Brookfield", "Lakeview")
    await facts.annotate([listed])
    assert espn.calls == [] and listed["matchup"]["home"]["name"] == "Brookfield"
