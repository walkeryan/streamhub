"""A label-only sport listing ("Live: College Football") names its sides where its description starts."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.sports import description_matchup, listing_matchup, sports_events, sports_on_now
from tests.conftest import Api
from tests.test_unified_search import channel, now, programme, search, seed

GAME = (
    "The No. 20 Kestrels Rowing Club (6-3, 4-2 League) host the Badgers (6-3, 3-3 League) at Riverside Park. "
    "Both teams aim to break a two-game skid."
)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (GAME, ("Badgers at Kestrels Rowing Club", "Kestrels Rowing Club", "Badgers")),
        ("#7 Badgers visit the Kestrels Saturday in a rematch.", ("Badgers at Kestrels", "Kestrels", "Badgers")),
        ("The Herons take on the No. 3 Otters (9-0).", ("Herons vs Otters", "Herons", "Otters")),
        ("Kestrels welcome Badgers.", ("Badgers at Kestrels", "Kestrels", "Badgers")),
        # The first sentence only: the next one's opening word is not part of a name.
        ("The Herons meet the Otters. Herons look to rebound.", ("Herons vs Otters", "Herons", "Otters")),
        (
            "The Herons (4-5) host the Otters (5-4, 3-4). Otters have lost five straight.",
            ("Otters at Herons", "Herons", "Otters"),
        ),
        # A trip to a place, then the side they meet there.
        (
            "The Herons (6-3) travel to Lakeside to take on the undefeated No. 8 Otters (9-0) at the Bowl.",
            ("Herons at Otters", "Otters", "Herons"),
        ),
        ("The Herons travel to Lakeside Arena to face the Otters.", ("Herons at Otters", "Otters", "Herons")),
        # "visit" a place is not a game; a studio show "hosts" experts.
        ("The Herons visit the Sunshine State for a battle with the Otters.", None),
        ("Jo Smith hosts Network's top experts for a daily discussion.", None),
        # Prose that does not open with the two sides names none.
        ("In a rematch of last year's final, the Kestrels host the Badgers.", None),
        ("Highlights and analysis from around the league.", None),
        ("The Kestrels host the Kestrels.", None),
        (None, None),
        ("", None),
    ],
)
def test_the_opening_of_a_description_names_the_sides(description, expected):
    assert description_matchup(description) == expected


def test_a_title_that_names_its_sides_wins_and_a_non_sport_listing_is_left_alone():
    assert listing_matchup("NBA: Home vs Away", "Sports", GAME) == ("NBA: Home vs Away", "Home", "Away")
    assert listing_matchup("Live: College Football", None, GAME)[0] == "Badgers at Kestrels Rowing Club"
    assert listing_matchup("Evening News", "News", GAME) == ("Evening News", None, None)
    assert listing_matchup("Live: College Football", None, "Pregame build-up.") == (
        "Live: College Football",
        None,
        None,
    )


async def test_sports_events_show_and_group_a_described_game_and_search_finds_it_by_its_sides(api: Api) -> None:
    await seed(
        api,
        channel("fox1", "Station One", 1203),
        channel("fox2", "Station Two", 1301),
        channel("news", "Halden News", 400),
        programme("fox1", "Live: College Football", timedelta(hours=3), 210, description=GAME, category="Sports"),
        programme("fox2", "Live: College Football", timedelta(hours=3), 210, description=GAME, category="Sports"),
        programme("news", "Evening News", timedelta(hours=3), 60, description=GAME),
    )
    day = now().astimezone().date()
    data = await sports_events(api.c.channels, api.profile_id, now=now(), language="all", days=2, date=day.isoformat())
    games = [e for e in data["events"] if e["title"] == "Badgers at Kestrels Rowing Club"]
    assert len(games) == 1, [e["title"] for e in data["events"]]
    game = games[0]
    assert (game["home"], game["away"]) == ("Kestrels Rowing Club", "Badgers")
    assert [f["channel_number"] for f in game["feeds"]] == [1203, 1301]

    for term in ("kestrels", "badgers"):
        found = await sports_events(api.c.channels, api.profile_id, now=now(), language="all", days=2, q=term)
        assert [e["title"] for e in found["events"]] == ["Badgers at Kestrels Rowing Club"]
    # The rest of the description is not searched: the venue finds nothing.
    assert (await sports_events(api.c.channels, api.profile_id, now=now(), language="all", days=2, q="riverside"))[
        "total"
    ] == 0


async def test_sports_on_now_shows_the_described_matchup(api: Api) -> None:
    await seed(
        api,
        channel("fox1", "Station One", 1203),
        programme("fox1", "Live: College Football", timedelta(minutes=-20), 210, description=GAME, category="Sports"),
    )
    data = await sports_on_now(api.c.channels, api.profile_id, language="all", now=now(), q="badgers")
    assert [item["title"] for item in data["items"]] == ["Badgers at Kestrels Rowing Club"]


async def test_unified_search_finds_a_label_only_game_by_its_teams(api: Api) -> None:
    await seed(
        api,
        channel("fox1", "Station One", 1203),
        channel("fox2", "Station Two", 1301),
        channel("news", "Halden News", 400),
        programme("fox1", "Live: College Football", timedelta(hours=15), 210, description=GAME, category="Sports"),
        programme("fox2", "Live: College Football", timedelta(hours=15), 210, description=GAME, category="Sports"),
        # Not sport: its description is never read for teams.
        programme("news", "Evening News", timedelta(hours=2), 60, description=GAME),
        # Sport, but the team is only mentioned later in the description.
        programme(
            "news",
            "Live: College Football",
            timedelta(hours=5),
            210,
            description="Pregame build-up, then a look back at the Kestrels' season.",
        ),
    )
    found = (await search(api, "kestrels"))["programmes"]
    assert [(entry["title"], entry["is_sport"], entry["teams"]) for entry in found] == [
        ("Badgers at Kestrels Rowing Club", True, ["Badgers", "Kestrels Rowing Club"])
    ]
    assert [feed["channel_number"] for feed in found[0]["feeds"]] == [1203, 1301]
    assert (await search(api, "riverside"))["programmes"] == []
