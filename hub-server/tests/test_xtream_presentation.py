"""Presentation rules for raw panel names. Every name here is invented; only the shapes are real."""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.xtream.presentation import Presented, present_group, present_title


def present(name: str, category: str = "", **kw: Any) -> Presented:
    args: dict[str, Any] = {"year": None, "is_adult": False, "default_language": None, "shelf_map": {}}
    return present_title(name, category, **{**args, **kw})


@pytest.mark.parametrize(
    "name,title,year,quality",
    [
        ("NF - Millionaire", "Millionaire", None, None),
        ("EN - The Mill  (2023)", "The Mill", 2023, None),
        ("4K-A+ - Berlin ER (2025) (DE)", "Berlin ER", 2025, "4K"),
        ("EN-TOP - Something (1999)", "Something", 1999, None),
        ("D+ - The Million Dollar Duck  (1971)", "The Million Dollar Duck", 1971, None),
        ("4K - Night Ferry (2019)", "Night Ferry", 2019, "4K"),
        ("  Plain   Title  ", "Plain Title", None, None),
        ("Harbour Lights (US)", "Harbour Lights", None, None),
        # Not provider tags: a numeric title, a lower-case word and a tag longer than six characters.
        ("1917 - The Long Walk", "1917 - The Long Walk", None, None),
        ("Up - Close", "Up - Close", None, None),
        ("CHANNEL - Opening Night", "CHANNEL - Opening Night", None, None),
        # A tag is only dropped when something is left to show.
        ("EN - ", "EN -", None, None),
    ],
)
def test_title_rules(name: str, title: str, year: int | None, quality: str | None) -> None:
    shown = present(name)
    assert (shown.title, shown.year, shown.quality) == (title, year, quality)


def test_row_year_wins_over_the_title_suffix() -> None:
    shown = present("EN - The Mill (2023)", year=2022)
    assert (shown.title, shown.year) == ("The Mill", 2022)


@pytest.mark.parametrize(
    "category,shelf,quality,kids",
    [
        ("EN - NEW RELEASE", "New Releases", None, False),
        ("EN - SCIENCE FICTION", "Science Fiction", None, False),
        ("EN - KIDS ⁴ᴷ ³⁸⁴⁰ᴾ", "Kids", "4K", True),
        ("EN - MANGA/ANIME", "Anime", None, False),
        ("NETFLIX MOVIES ⁴ᴷ ³⁸⁴⁰ᴾ ᴰᵒˡᵇʸ ⱽᶦˢᶦᵒⁿ", "Netflix", "4K", False),
        ("NETFLIX  SERIES", "Netflix", None, False),
        ("AMAZON DOCU-MOVIES", "Prime Video", None, False),
        ("APPLE+ KIDS", "Apple TV+", None, True),
        ("DISNEY+ STAND-UP COMEDY", "Disney+", None, False),
        ("HBO MAX SERIES ⁴ᴷ ³⁸⁴⁰ᴾ", "Max", "4K", False),
        ("PARAMOUNT+ MOVIES", "Paramount+", None, False),
        ("PEACOCK ORIGINALS", "Peacock", None, False),
        ("SHOWTIME SERIES", "Showtime", None, False),
        ("SKY CINEMA", "Sky", None, False),
        ("DISCOVERY+ SHOWS", "Discovery+", None, False),
        ("CRUNCHYROLL SERIES", "Crunchyroll", None, False),
        ("ENGLISH SERIES ᴰᴼᴸᴮʸ ᴬᵁᴰᴵᴼ", "Series", None, False),
        ("MARVEL MOVIES 3840P (MULTI)", "Marvel Movies", "4K", False),
        ("TOP MOVIES BLURAY (MULTI-SUBS)", "Top Movies", None, False),
        ("EN - ACTION 4K HEVC", "Action", "4K", False),
        ("EN - DRAMA DOLBY VISION", "Drama", None, False),
        ("EN - DOCU-SERIES", "Documentaries", None, False),
        ("DOCUMENTARIES", "Documentaries", None, False),
        ("EN - STAND-UP COMEDY", "Stand-Up Comedy", None, False),
        ("EN - TV SHOWS", "TV Shows", None, False),
        ("EN - WAR AND HISTORY", "War and History", None, False),
        # A brand is a leading word, never a substring of another one.
        ("SKYLINE COLLECTION", "Skyline Collection", None, False),
        # Names a provider already wrote in mixed case are left alone.
        ("Action & Adventure", "Action & Adventure", None, False),
        ("", "", None, False),
    ],
)
def test_shelf_rules(category: str, shelf: str, quality: str | None, kids: bool) -> None:
    shown = present("Some Title", category)
    assert (shown.shelf, shown.quality, shown.kids, shown.raw_category) == (shelf, quality, kids, category)


def test_shelf_map_wins_over_every_rule() -> None:
    shown = present("Some Title", "NETFLIX MOVIES ⁴ᴷ", shelf_map={"NETFLIX MOVIES ⁴ᴷ": "Family Night"})
    assert (shown.shelf, shown.quality) == ("Family Night", "4K")


def test_quality_comes_from_either_the_title_or_the_category() -> None:
    assert present("4K-A+ - Berlin ER (2025)", "EN - DRAMA").quality == "4K"
    assert present("EN - Berlin ER (2025)", "EN - DRAMA ⁴ᴷ").quality == "4K"
    assert present("EN - Berlin ER (2025)", "EN - DRAMA").quality is None


@pytest.mark.parametrize(
    "name,category,default,language",
    [
        # explicit tag in the category
        ("Some Title", "EN - NEW RELEASE", None, "eng"),
        ("Some Title", "ENGLISH SERIES ᴰᴼᴸᴮʸ ᴬᵁᴰᴵᴼ", "fr", "eng"),
        ("Some Title", "FR - ACTION", "en", "fra"),
        # explicit tag on the title
        ("EN-TOP - Something (1999)", "TOP MOVIES BLURAY (MULTI-SUBS)", None, "eng"),
        ("DE - Berlin ER", "NETFLIX  SERIES", "en", "deu"),
        # a brand tag is not a language
        ("NF - Millionaire", "NETFLIX  SERIES", None, "und"),
        ("NF - Millionaire", "NETFLIX  SERIES", "en", "eng"),
        # the configured default beats the looser category inference
        ("Some Title", "MOVIES [FR]", "en", "eng"),
        ("Some Title", "MOVIES [FR]", None, "fra"),
        ("Some Title", "MARVEL MOVIES 3840P (MULTI)", None, "und"),
        ("Some Title", "", "eng", "eng"),
    ],
)
def test_language_precedence(name: str, category: str, default: str | None, language: str) -> None:
    assert present(name, category, default_language=default).language == language


def test_adult_from_panel_flag_or_configured_category() -> None:
    assert present("Some Title", "EN - DRAMA", is_adult=True).adult
    assert present("Some Title", "EN - LATE NIGHT", adult_categories=["EN - LATE NIGHT"]).adult
    assert not present("Some Title", "EN - DRAMA", adult_categories=["EN - LATE NIGHT"]).adult


@pytest.mark.parametrize(
    "raw,group",
    [
        ("US| SPORT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ", "Sport"),
        ("US| NEWS ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ", "News"),
        ("US| NFL PPV", "NFL PPV"),
        ("UK | News", "News"),
        ("CA| KIDS 4K", "Kids"),
        ("News", "News"),
        ("Uncategorized", "Uncategorized"),
        ("US| ⁴ᴷ", "US| ⁴ᴷ"),
    ],
)
def test_channel_group_rules(raw: str, group: str) -> None:
    assert present_group(raw, group_map={}) == group


def test_channel_group_map_and_hidden_groups() -> None:
    raw = "US| SPORT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ"
    assert present_group(raw, group_map={raw: "Live Sport"}) == "Live Sport"
    assert present_group(raw, group_map={}, hidden_groups=[raw]) is None
    assert present_group("US| NFL PPV", group_map={}, hidden_groups=["NFL PPV"]) is None
    assert present_group("US| NEWS", group_map={}, hidden_groups=[raw]) == "News"
