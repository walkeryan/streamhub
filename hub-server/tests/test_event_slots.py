"""Event-slot channel names: what a provider's game-day name says, and when the game is on."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.event_slots import (
    SLOT_NAME_LIKE,
    EventSlot,
    SlotConfig,
    display_name,
    is_placeholder,
    network_name,
    parse_event_slot,
    quality_hint,
)


def sql_like(pattern: str, text: str) -> bool:
    """SQL LIKE, as the database applies the prefilter to a name with its runs of spaces collapsed."""
    expression = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern)
    return re.fullmatch(expression, re.sub(r" {2,}", " ", text), re.DOTALL) is not None


@pytest.mark.parametrize(
    "name",
    [
        "XX (VANTIX+ 001) | Football: Morning Football Show (2026-11-03 08:00:00)",
        "(XX) (VANTIX+ 003) | Soccer (M): Oakdale at Port Linden (2026-09-29 18:50:00)",
        "  ( XX )   ( VANTIX+   012 )|Football :  Oakdale   at  Riverton  ( 2026-10-02   14:50 )  ",
        "(XX) (VANTIX+ 008) | Field Hockey: Northfield at Lakeside",
        "(XX) (VANTIX+ 008) | Field Hockey: Northfield at Lakeside (2026-13-45 14:50:00)",
        "CORVAN LEAGUE  | 02 - 1pm Kestrels at Badgers",
        "CORVAN LEAGUE | 11 - 4:25pm Otters vs Herons",
        "CORVAN LEAGUE | 3 - Kestrels at Badgers",
        "CORVAN LEAGUE|104-Kestrels at Badgers",
        "CORVAN LEAGUE |  7 – 1pm Kestrels at Badgers",
    ],
)
def test_every_slot_name_passes_the_sql_prefilter(name: str) -> None:
    assert parse_event_slot(name) is not None
    assert any(sql_like(pattern, name) for pattern in SLOT_NAME_LIKE), name


def test_the_sql_prefilter_leaves_ordinary_channel_names_out() -> None:
    for name in ("XX | Example One HD", "XX| NEWS 24", "COMBAT HD", "Example Sports 2 (West)", "UK: General One"):
        assert not any(sql_like(pattern, name) for pattern in SLOT_NAME_LIKE), name


def slot(name: str) -> EventSlot:
    parsed = parse_event_slot(name)
    assert parsed is not None, name
    return parsed


def test_a_dated_slot_with_a_bare_country_tag() -> None:
    s = slot("XX (VANTIX+ 001) | Football: Morning Football Show (2026-11-03 08:00:00)")
    assert (s.network, s.number, s.channel_name) == ("VANTIX+", "001", "VANTIX+ 001")
    assert (s.sport, s.title, s.home, s.away) == ("Football", "Morning Football Show", None, None)
    assert s.starts_local == datetime(2026, 11, 3, 8, 0)


def test_a_dated_slot_with_a_bracketed_country_tag_and_two_sides() -> None:
    s = slot("(XX) (VANTIX+ 003) | Soccer (M): Oakdale at Port Linden (2026-09-29 18:50:00)")
    assert (s.network, s.number) == ("VANTIX+", "003")
    assert (s.sport, s.title) == ("Men's Soccer", "Oakdale at Port Linden")
    assert (s.away, s.home) == ("Oakdale", "Port Linden")
    assert s.starts_local == datetime(2026, 9, 29, 18, 50)


@pytest.mark.parametrize(
    ("body", "home", "away"),
    [
        ("Northfield at Lakeside", "Lakeside", "Northfield"),
        ("Northfield @ Lakeside", "Lakeside", "Northfield"),
        ("Northfield vs Lakeside", "Northfield", "Lakeside"),
        ("Northfield vs. Lakeside", "Northfield", "Lakeside"),
        ("Northfield v Lakeside", "Northfield", "Lakeside"),
        ("Northfield VS Lakeside", "Northfield", "Lakeside"),
        # Not two named sides: nothing is guessed.
        ("Night at the Arena", None, None),
        ("Northfield at Lakeside at Millbrook", None, None),
        ("Lakeside vs Lakeside", None, None),
        ("Weekly Round-up", None, None),
    ],
)
def test_sides_are_read_only_from_an_explicit_matchup(body: str, home: str | None, away: str | None) -> None:
    s = slot(f"(XX) (VANTIX+ 008) | Field Hockey: {body} (2026-10-02 14:50:00)")
    assert (s.title, s.home, s.away) == (body, home, away)
    assert s.sport == "Field Hockey"


@pytest.mark.parametrize(
    ("sport", "expected"),
    [
        ("Soccer (W)", "Women's Soccer"),
        ("Basketball (M)", "Men's Basketball"),
        ("Field  Hockey", "Field Hockey"),
        ("Volleyball (U19)", "Volleyball (U19)"),
    ],
)
def test_the_sport_text_is_kept_and_only_its_division_mark_is_spelled_out(sport: str, expected: str) -> None:
    assert slot(f"XX (VANTIX+ 020) | {sport}: Oakdale at Riverton (2026-10-02 14:50:00)").sport == expected


def test_a_slot_without_a_sport_keeps_the_whole_text_as_its_title() -> None:
    s = slot("XX (VANTIX+ 044) | Oakdale at Riverton (2026-10-02 14:50:00)")
    assert (s.sport, s.title, s.away, s.home) == (None, "Oakdale at Riverton", "Oakdale", "Riverton")
    # A colon inside the event text is not a sport separator twice over.
    s = slot("XX (VANTIX+ 044) | Football: Cup Final: Oakdale at Riverton (2026-10-02 14:50:00)")
    assert (s.sport, s.title) == ("Football", "Cup Final: Oakdale at Riverton")


def test_odd_spacing_and_a_short_time_are_tolerated() -> None:
    s = slot("  ( XX )   ( VANTIX+   012 )|Football :  Oakdale   at  Riverton  ( 2026-10-02   14:50 )  ")
    assert (s.channel_name, s.sport, s.title) == ("VANTIX+ 012", "Football", "Oakdale at Riverton")
    assert s.starts_local == datetime(2026, 10, 2, 14, 50)
    assert slot("XX (VANTIX+ 012) | Football: Oakdale at Riverton (2026-10-02T14:50:00)").starts_local == s.starts_local


def test_a_dateless_league_slot_is_a_game_channel() -> None:
    s = slot("CORVAN LEAGUE  | 02 - 1pm Kestrels at Badgers")
    assert (s.network, s.number, s.channel_name) == ("CORVAN LEAGUE", "02", "CORVAN LEAGUE 02")
    assert (s.sport, s.title, s.away, s.home) == (None, "Kestrels at Badgers", "Kestrels", "Badgers")
    assert s.starts_local is None
    assert slot("CORVAN LEAGUE | 11 - 4:25pm Otters vs Herons").title == "Otters vs Herons"
    assert slot("CORVAN LEAGUE | 3 - Kestrels at Badgers").number == "3"


def test_a_missing_or_malformed_date_leaves_a_slot_without_a_start() -> None:
    missing = slot("(XX) (VANTIX+ 008) | Field Hockey: Northfield at Lakeside")
    assert (missing.channel_name, missing.title, missing.starts_local) == (
        "VANTIX+ 008",
        "Northfield at Lakeside",
        None,
    )
    for stamp in ("2026-13-45 14:50:00", "2026-02-30 10:00:00", "2026-10-02 25:61:00"):
        bad = slot(f"(XX) (VANTIX+ 008) | Field Hockey: Northfield at Lakeside ({stamp})")
        assert (bad.title, bad.starts_local) == ("Northfield at Lakeside", None), stamp


@pytest.mark.parametrize(
    "name",
    [
        "XX: CORVAN LEAGUE REPLAY 1 HD",
        "CORVAN LEAGUE ENDZONE ᴴᴰ",
        "COMBAT HD",
        "CORVAN LEAGUE: PORT LINDEN KESTRELS ᴴᴰ",
        "UK | General One",
        "XX | 24 - Hour News",
        "(XX) (Example 5) | Evening News",
        "(XX) (Example 5) | Talk: The Late Programme",
        "Example One HD",
        "Radio (Classic 2)",
        "",
    ],
)
def test_other_names_are_not_event_slots_and_stay_as_they_are(name: str) -> None:
    assert parse_event_slot(name) is None
    assert display_name(name) == name


def test_the_display_name_of_a_slot_is_its_network_and_number() -> None:
    assert (
        display_name("(XX) (VANTIX+ 008) | Field Hockey: Northfield at Lakeside (2026-10-02 14:50:00)") == "VANTIX+ 008"
    )
    assert display_name("XX (LUMEX TV 4K 101) | Rugby: Oakdale v Riverton (2026-10-02 14:50:00)") == "LUMEX TV 101"
    assert display_name("CORVAN LEAGUE | 02 - 1pm Kestrels at Badgers") == "CORVAN LEAGUE 02"


@pytest.mark.parametrize(
    ("name", "quality"),
    [
        ("CORVAN LEAGUE ENDZONE ᴴᴰ", "HD"),
        ("COMBAT HD", "HD"),
        ("Example Sports ᶠᴴᴰ", "FHD"),
        ("Example Sports FHD", "FHD"),
        ("Example Sports ⁴ᴷ", "4K"),
        ("Example Sports UHD", "UHD"),
        ("XX (LUMEX TV 4K 101) | Rugby: Oakdale v Riverton (2026-10-02 14:50:00)", "4K"),
        ("Example Sports [HD]", "HD"),
        ("Example Sports", None),
        ("SHDTV Sports", None),
        ("UHDistrict", None),
    ],
)
def test_quality_is_read_from_plain_and_superscript_decorations(name: str, quality: str | None) -> None:
    assert quality_hint(name) == quality


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("XX: CORVAN LEAGUE REPLAY 1 HD", "CORVAN LEAGUE REPLAY 1"),
        ("CORVAN LEAGUE ENDZONE ᴴᴰ", "CORVAN LEAGUE ENDZONE"),
        ("COMBAT HD", "COMBAT"),
        ("CORVAN LEAGUE: PORT LINDEN KESTRELS ᴴᴰ", "CORVAN LEAGUE: PORT LINDEN KESTRELS"),
        ("XX | Example Sports 2 FHD", "Example Sports 2"),
        ("HD", "HD"),
    ],
)
def test_a_network_name_loses_its_country_tag_and_quality_marks(name: str, expected: str) -> None:
    assert network_name(name) == expected


@pytest.mark.parametrize(
    ("title", "channel", "expected"),
    [
        ("COMBAT HD", "COMBAT HD", True),
        ("  combat   hd ", "COMBAT HD", True),
        ("(XX) (VANTIX+ 008) | Field Hockey: A at B", "(XX)  (VANTIX+ 008) | Field Hockey: A at B", True),
        ("Combat Tonight", "COMBAT HD", False),
        # The channel was renamed for its next game; the filler still carries the old name.
        ("(XX) (VANTIX+ 008) | Field Hockey: A at B (2026-10-02 14:50:00)", "(XX) (VANTIX+ 008) | Rugby: C v D", True),
        ("CORVAN LEAGUE | 02 - 1pm Kestrels at Badgers", "CORVAN LEAGUE 02", True),
        ("Boxing: Rivers vs Stone", "COMBAT HD", False),
        ("", "COMBAT HD", False),
        ("COMBAT HD", "", False),
    ],
)
def test_a_programme_that_only_repeats_its_channel_name_is_a_placeholder(
    title: str, channel: str, expected: bool
) -> None:
    assert is_placeholder(title, channel) is expected


def test_slot_config_reads_the_source_keys_leniently() -> None:
    default = SlotConfig.from_config({})
    assert (default.zone.key, default.duration) == ("UTC", timedelta(minutes=210))
    set_ = SlotConfig.from_config({"event_timezone": "America/Chicago", "event_duration_minutes": 150})
    assert (set_.zone.key, set_.duration) == ("America/Chicago", timedelta(minutes=150))
    for bad in ({"event_timezone": "Mars/Olympus"}, {"event_timezone": 5}, {"event_timezone": ""}):
        assert SlotConfig.from_config(bad).zone.key == "UTC"
    for bad in ({"event_duration_minutes": 0}, {"event_duration_minutes": "long"}, {"event_duration_minutes": 100000}):
        assert SlotConfig.from_config(bad).duration == timedelta(minutes=210)
    assert set_.summary() == {"event_timezone": "America/Chicago", "event_duration_minutes": 150}


def test_the_embedded_time_is_local_to_the_configured_zone() -> None:
    s = slot("(XX) (VANTIX+ 003) | Soccer (M): Oakdale at Port Linden (2026-09-29 18:50:00)")
    assert s.window(SlotConfig.from_config({})) == (
        datetime(2026, 9, 29, 18, 50, tzinfo=UTC),
        datetime(2026, 9, 29, 22, 20, tzinfo=UTC),
    )
    eastern = SlotConfig.from_config({"event_timezone": "America/New_York"})
    assert s.window(eastern) == (datetime(2026, 9, 29, 22, 50, tzinfo=UTC), datetime(2026, 9, 30, 2, 20, tzinfo=UTC))


def test_the_offset_follows_daylight_saving_on_either_side_of_the_change() -> None:
    eastern = SlotConfig.from_config({"event_timezone": "America/New_York", "event_duration_minutes": 60})
    # Clocks go back at 02:00 on 2026-11-01 in this zone: the day before is UTC-4, the day after UTC-5.
    before = slot("XX (VANTIX+ 001) | Football: Oakdale at Riverton (2026-10-31 20:00:00)").window(eastern)
    after = slot("XX (VANTIX+ 001) | Football: Oakdale at Riverton (2026-11-01 20:00:00)").window(eastern)
    assert before is not None and after is not None
    assert before[0] == datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    assert after[0] == datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
    # The repeated hour is read as its first pass, and an hour that never happens still lands on the clock.
    repeated = slot("XX (VANTIX+ 001) | Football: Oakdale at Riverton (2026-11-01 01:30:00)").window(eastern)
    skipped = slot("XX (VANTIX+ 001) | Football: Oakdale at Riverton (2026-03-08 02:30:00)").window(eastern)
    assert repeated is not None and skipped is not None
    assert repeated[0] == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert skipped[0].astimezone(ZoneInfo("America/New_York")).hour in (1, 3)
    assert skipped[1] - skipped[0] == timedelta(minutes=60)


def test_a_slot_is_live_from_its_start_until_its_length_has_passed() -> None:
    config = SlotConfig.from_config({})
    s = slot("XX (VANTIX+ 001) | Football: Oakdale at Riverton (2026-10-31 20:00:00)")
    start = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)
    assert s.state(start - timedelta(seconds=1), config) == "upcoming"
    assert s.state(start, config) == "live"
    assert s.state(start + timedelta(minutes=210) - timedelta(seconds=1), config) == "live"
    assert s.state(start + timedelta(minutes=210), config) == "ended"
    assert s.state(start + timedelta(days=42), config) == "ended"
    dateless = slot("CORVAN LEAGUE | 02 - 1pm Kestrels at Badgers")
    assert dateless.window(config) is None and dateless.state(start, config) == "unscheduled"
