"""/sports/events and /sports/channels over event-slot names, placeholders and real listings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.sports import sports_events, sports_on_now
from tests.conftest import Api
from tests.test_channel_presentation import add_lineup, add_placeholders, add_programme, stamp

NOW = datetime(2026, 11, 7, 20, 0, tzinfo=UTC)
HOCKEY = "(XX) (VANTIX+ 001) | Field Hockey: Northfield at Lakeside"
EVENTS = "XX| VANTIX+ EVENTS"


def lineup(at: datetime) -> list[tuple[str, str, str]]:
    return [
        ("vantix1", EVENTS, f"{HOCKEY} {stamp(at - timedelta(minutes=30))}"),
        ("vantix2", EVENTS, f"XX (VANTIX+ 002) | Football: Morning Football Show {stamp(at + timedelta(hours=2))}"),
        (
            "vantix3",
            EVENTS,
            f"(XX) (VANTIX+ 003) | Soccer (M): Oakdale at Port Linden {stamp(at - timedelta(days=42))}",
        ),
        (
            "lumex7",
            "XX| LUMEX TV",
            f"XX (LUMEX TV ᴴᴰ 007) | Field Hockey: Northfield at Lakeside {stamp(at - timedelta(minutes=30))}",
        ),
        ("league2", "CORVAN LEAGUE", "CORVAN LEAGUE | 02 - 1pm Kestrels at Badgers"),
        ("combat", "XX| SPORTS", "COMBAT HD"),
        ("footy", "XX| SPORTS", "XX: Example Football Network ᶠᴴᴰ"),
        ("news", "XX| NEWS", "Example News"),
    ]


async def events(api: Api, at: datetime = NOW, **kw: Any) -> dict[str, Any]:
    kw.setdefault("timezone", "UTC")
    kw.setdefault("language", "all")
    return await sports_events(api.c.channels, api.profile_id, now=at, **kw)


def titles(page: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(e["title"], e["kind"], e["status"]) for e in page["events"]]


async def test_events_are_live_and_coming_slots_never_placeholders_or_old_names(api: Api) -> None:
    source = await add_lineup(api, lineup(NOW))
    # Every channel "shows" its own name, and those names are full of sport words.
    await add_placeholders(api, source, NOW)
    page = await events(api)
    assert titles(page) == [
        ("Northfield at Lakeside", "event", "current"),
        ("Morning Football Show", "event", "upcoming"),
        ("Kestrels at Badgers", "channel", "unscheduled"),
    ]
    assert page["total"] == 3 and not page["truncated"]
    assert page["sports"] == [
        {"name": "Field Hockey", "count": 1},
        {"name": "Football", "count": 1},
        {"name": "Sports", "count": 1},
    ]
    # The stale name is an event on no day, not even the one written in it.
    old = await events(api, date=(NOW - timedelta(days=42)).date().isoformat())
    assert titles(old) == []
    assert (await sports_on_now(api.c.channels, api.profile_id, now=NOW, language="all"))["total"] == 0


async def test_filler_left_from_an_earlier_name_is_not_an_event_either(api: Api) -> None:
    # The slot was renamed for tonight's game after the manager wrote its filler: the guide still
    # "shows" last week's football game in four-hour blocks, on a channel that now has another name.
    rows = [
        ("vantix1", EVENTS, f"(XX) (VANTIX+ 001) | Rugby: Oakdale v Riverton {stamp(NOW + timedelta(hours=1))}"),
        ("vantix2", EVENTS, "VANTIX+ 002"),
    ]
    source = await add_lineup(api, rows)
    old = f"(XX) (VANTIX+ 001) | Football: Kestrels at Badgers {stamp(NOW - timedelta(days=7))}"
    for epg in ("vantix1", "vantix2"):
        await add_programme(api, source, epg, old, NOW - timedelta(hours=1), 4)
    assert titles(await events(api)) == [("Oakdale v Riverton", "event", "upcoming")]
    assert (await sports_on_now(api.c.channels, api.profile_id, now=NOW, language="all"))["total"] == 0


async def test_one_game_on_two_channels_is_one_event_with_clean_feeds(api: Api) -> None:
    await add_lineup(api, lineup(NOW))
    game = (await events(api, status="current"))["events"][0]
    assert (game["title"], game["sport"], game["kind"]) == ("Northfield at Lakeside", "Field Hockey", "event")
    assert (game["home"], game["away"], game["league"]) == ("Lakeside", "Northfield", "VANTIX+")
    assert game["live_evidence"] and not game["replay"]
    assert game["start"] == NOW - timedelta(minutes=30) and game["stop"] == game["start"] + timedelta(minutes=210)
    listed = (await api.get("/channels", params={"language": "all"})).json()["channels"]
    by_name = {c["name"]: c for c in listed}
    assert [(f["channel_name"], f["quality"], f["language_verified"]) for f in game["feeds"]] == [
        ("VANTIX+ 001", None, False),
        ("LUMEX TV 007", "HD", False),
    ]
    for feed in game["feeds"]:
        channel = by_name[feed["channel_name"]]
        assert (feed["channel_id"], feed["channel_number"]) == (channel["id"], channel["number"])

    undated = (await events(api))["events"][-1]
    assert (undated["league"], undated["home"], undated["away"]) == ("CORVAN LEAGUE", "Badgers", "Kestrels")
    assert not undated["live_evidence"]
    assert [f["channel_name"] for f in undated["feeds"]] == ["CORVAN LEAGUE 02"]


async def test_a_slot_is_current_exactly_while_its_window_is_open(api: Api) -> None:
    start = NOW - timedelta(minutes=30)
    await add_lineup(api, [("vantix1", EVENTS, f"{HOCKEY} {stamp(start)}")])
    day = {"date": start.date().isoformat()}
    assert titles(await events(api, start - timedelta(seconds=1), **day)) == [
        ("Northfield at Lakeside", "event", "upcoming")
    ]
    assert titles(await events(api, start, **day)) == [("Northfield at Lakeside", "event", "current")]
    last = start + timedelta(minutes=210) - timedelta(seconds=1)
    assert titles(await events(api, last, **day)) == [("Northfield at Lakeside", "event", "current")]
    assert titles(await events(api, last + timedelta(seconds=1), **day)) == []
    assert titles(await events(api, start - timedelta(seconds=1), status="current", **day)) == []
    assert titles(await events(api, start, status="upcoming", **day)) == []


async def test_status_sport_search_and_day_filters_cover_slots(api: Api) -> None:
    tomorrow = NOW + timedelta(days=1)
    rows = [*lineup(NOW), ("vantix4", EVENTS, f"XX (VANTIX+ 004) | Rugby: Oakdale v Riverton {stamp(tomorrow)}")]
    await add_lineup(api, rows)
    assert [t[0] for t in titles(await events(api, status="current"))] == ["Northfield at Lakeside"]
    assert [t[0] for t in titles(await events(api, status="upcoming"))] == ["Morning Football Show"]
    assert [t[0] for t in titles(await events(api, status="active"))] == [
        "Northfield at Lakeside",
        "Morning Football Show",
        "Kestrels at Badgers",
    ]
    assert titles(await events(api, status="replay")) == []
    hockey = await events(api, sport="Field Hockey")
    assert hockey["total"] == 1 and len(hockey["sports"]) == 3
    assert (await events(api, sport="Curling"))["total"] == 0
    assert [t[0] for t in titles(await events(api, q="football"))] == ["Morning Football Show"]
    assert [t[0] for t in titles(await events(api, q="lumex tv"))] == ["Northfield at Lakeside"]
    # Tomorrow's game is on tomorrow's page, or on a two-day page from today; a dateless channel only on today's.
    assert [t[0] for t in titles(await events(api, days=2))][-2:] == ["Oakdale v Riverton", "Kestrels at Badgers"]
    # A day's page is what overlaps it: tonight's late game is still running after midnight.
    assert titles(await events(api, date=tomorrow.date().isoformat())) == [
        ("Morning Football Show", "event", "upcoming"),
        ("Oakdale v Riverton", "event", "upcoming"),
    ]
    later = (tomorrow + timedelta(days=1)).date().isoformat()
    assert titles(await events(api, date=later)) == []


async def test_the_day_a_slot_falls_on_follows_the_source_event_timezone(api: Api) -> None:
    # 23:30 on the 7th on a clock five hours behind UTC is 04:30 UTC on the 8th.
    rows = [("vantix1", EVENTS, "XX (VANTIX+ 001) | Football: Oakdale at Riverton (2026-11-07 23:30:00)")]
    await add_lineup(api, rows, config={"event_timezone": "Etc/GMT+5"})
    game = (await events(api, date="2026-11-08"))["events"][0]
    assert game["start"] == datetime(2026, 11, 8, 4, 30, tzinfo=UTC) and game["status"] == "upcoming"
    assert titles(await events(api, date="2026-11-07")) == []
    # The viewer's zone decides which calendar day the request means.
    assert len((await events(api, date="2026-11-07", timezone="Etc/GMT+5"))["events"]) == 1


async def test_real_listings_are_events_beside_slots_and_name_their_sides(api: Api) -> None:
    source = await add_lineup(api, lineup(NOW))
    await add_placeholders(api, source, NOW, skip=("combat", "footy"))
    await add_programme(api, source, "combat", "Boxing: Rivers vs Stone", NOW - timedelta(hours=1), 2)
    await add_programme(api, source, "footy", "Football: Rivermen at Highlanders", NOW + timedelta(hours=1), 2)
    await add_programme(api, source, "footy", "Football Classics", NOW - timedelta(hours=3), 2)
    page = await events(api)
    by_title = {e["title"]: e for e in page["events"]}
    boxing = by_title["Boxing: Rivers vs Stone"]
    assert (boxing["kind"], boxing["status"], boxing["home"], boxing["away"]) == ("event", "current", "Rivers", "Stone")
    assert (boxing["league"], boxing["live_evidence"]) == (None, False)
    assert [(f["channel_name"], f["quality"]) for f in boxing["feeds"]] == [("COMBAT HD", "HD")]
    away = by_title["Football: Rivermen at Highlanders"]
    assert (away["home"], away["away"], away["status"]) == ("Highlanders", "Rivermen", "upcoming")
    assert away["feeds"][0]["quality"] == "FHD" and away["feeds"][0]["programme_id"].startswith("prog:")
    assert by_title["Football Classics"]["status"] == "ended"
    # "active" is what a screen without an Ended section asks for.
    assert "Football Classics" not in {e["title"] for e in (await events(api, status="active"))["events"]}
    assert page["coverage"]["channels_with_sports_schedule"] == 6


async def test_the_events_route_serves_slots_and_validates_its_window(api: Api) -> None:
    at = datetime.now(UTC).replace(microsecond=0)
    await add_lineup(api, lineup(at))
    response = await api.get("/sports/events", params={"timezone": "UTC", "language": "all", "status": "active"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert {(e["title"], e["kind"], e["status"]) for e in body["events"]} >= {
        ("Northfield at Lakeside", "event", "current"),
        ("Kestrels at Badgers", "channel", "unscheduled"),
    }
    live = next(e for e in body["events"] if e["status"] == "current")
    assert [f["channel_number"] for f in live["feeds"]] == sorted(f["channel_number"] for f in live["feeds"])
    # A feed names the same programme the channel list shows on that channel.
    listed = {c["id"]: c for c in (await api.get("/channels", params={"language": "all"})).json()["channels"]}
    assert [f["programme_id"] for f in live["feeds"]] == [listed[f["channel_id"]]["now"]["id"] for f in live["feeds"]]
    assert (await api.get("/sports/events", params={"days": 0})).status_code == 422
    assert (await api.get("/sports/events", params={"days": 8})).status_code == 422
    assert (await api.get("/sports/events", params={"status": "ended"})).status_code == 422


async def test_sports_channels_are_the_always_on_networks(api: Api) -> None:
    at = datetime.now(UTC).replace(microsecond=0)
    source = await add_lineup(api, lineup(at))
    await add_placeholders(api, source, at, skip=("footy",))
    await add_programme(api, source, "footy", "Football: Rivermen at Highlanders", at - timedelta(minutes=5), 2)
    response = await api.get("/sports/channels", params={"language": "all"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["total"], body["truncated"]) == (2, False)
    combat, footy = body["channels"]
    assert (combat["channel_name"], combat["quality"], combat["now"]) == ("COMBAT", "HD", None)
    assert (footy["channel_name"], footy["quality"]) == ("Example Football Network", "FHD")
    assert (footy["now"]["title"], footy["now"]["teams"]) == (
        "Football: Rivermen at Highlanders",
        ["Rivermen", "Highlanders"],
    )
    listed = {c["id"]: c for c in (await api.get("/channels", params={"language": "all"})).json()["channels"]}
    assert listed[combat["channel_id"]]["number"] == combat["channel_number"]
    assert listed[combat["channel_id"]]["name"] == "COMBAT HD"
    paged = (await api.get("/sports/channels", params={"language": "all", "limit": 1, "offset": 1})).json()
    assert [c["channel_name"] for c in paged["channels"]] == ["Example Football Network"]
    found = (await api.get("/sports/channels", params={"language": "all", "q": "combat"})).json()
    assert [c["channel_name"] for c in found["channels"]] == ["COMBAT"]
    assert (await api.client.get("/api/v1/sports/channels")).status_code == 401
