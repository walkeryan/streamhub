"""What a raw panel name looks like on screen.

Panels decorate everything: titles carry a provider or language tag and a year
(``EN - The Mill  (2023)``), categories carry language tags, brand names and
quality marks, often in superscript (``EN - KIDS ⁴ᴷ ³⁸⁴⁰ᴾ``). The rules here are
generic and total; anything specific to one deployment comes from the source
config (``shelf_map``, ``group_map``, ``default_language``, ``hidden_groups``,
``adult_categories``). Results are stored on the catalog rows at sync, so browse
queries never re-derive them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.adapters.language.data import ISO1_TO_3, ISO2B_TO_3, LANG_KEYWORDS, LANG_NAMES
from app.adapters.language.inferrer import lang_from_category, lang_from_token, normalise_code
from app.domain.groups import GROUP_SEPARATOR, group_path

UNKNOWN_LANGUAGE = "und"


@dataclass(frozen=True)
class Presented:
    title: str
    year: int | None
    shelf: str
    quality: str | None
    language: str  # ISO 639-3, the code the rest of the hub filters on, or "und"
    kids: bool
    adult: bool
    raw_category: str


def _str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): str(v).strip() for k, v in value.items() if isinstance(v, str) and v.strip()}


def _str_set(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(v) for v in value if isinstance(v, str) and v.strip())


@dataclass(frozen=True)
class PresentationConfig:
    """The deployment-specific half of the rules, read leniently from the source config."""

    shelf_map: Mapping[str, str] = field(default_factory=dict)
    group_map: Mapping[str, str] = field(default_factory=dict)
    default_language: str | None = None
    hidden_groups: frozenset[str] = frozenset()
    adult_categories: frozenset[str] = frozenset()
    # A panel has a few hundred groups and tens of thousands of channels: each group is worked out once.
    _groups: dict[str, str | None] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> PresentationConfig:
        default = config.get("default_language")
        return cls(
            shelf_map=_str_map(config.get("shelf_map")),
            group_map=_str_map(config.get("group_map")),
            default_language=normalise_code(default) if isinstance(default, str) else None,
            hidden_groups=_str_set(config.get("hidden_groups")),
            adult_categories=_str_set(config.get("adult_categories")),
        )

    def title(self, name: str, raw_category: str, *, year: int | None, is_adult: bool) -> Presented:
        return present_title(
            name,
            raw_category,
            year=year,
            is_adult=is_adult,
            default_language=self.default_language,
            shelf_map=self.shelf_map,
            adult_categories=self.adult_categories,
        )

    def group(self, raw_group: str) -> str | None:
        if raw_group not in self._groups:
            self._groups[raw_group] = present_group(
                raw_group, group_map=self.group_map, hidden_groups=self.hidden_groups
            )
        return self._groups[raw_group]

    def summary(self) -> dict[str, Any]:
        return {
            "default_language": self.default_language,
            "shelf_map": dict(self.shelf_map),
            "group_map": dict(self.group_map),
            "hidden_groups": sorted(self.hidden_groups),
            "adult_categories": sorted(self.adult_categories),
        }


# "EN - ", "NF - ", "4K-A+ - ", "EN-TOP - ": upper case, at most six characters, at least one letter.
_TITLE_TAG = re.compile(
    r"^(?P<uhd>4K-)?(?P<tag>(?=[A-Z0-9+-]*[A-Z])[A-Z0-9+](?:[A-Z0-9+-]{0,4}[A-Z0-9+])?)\s+-\s+(?=\S)"
)
_TRAILING_COUNTRY = re.compile(r"\s*\([A-Z]{2}\)$")
_TRAILING_YEAR = re.compile(r"\s*\(((?:19|20)\d{2})\)$")
# "EN - ", "US| ", "UK | ", "DE: " at the start of a category or channel group.
_LEADING_TAG = re.compile(r"^(?P<tag>[A-Za-z]{2,3})(?P<sep>\s*[|:]\s*|\s+[-–]\s+)(?=\S)")
_LEADING_WORD = re.compile(r"^(?P<word>[^\W\d_]+)\s+(?=\S)")
_FOUR_K = re.compile(r"(?<![A-Z0-9])(?:4K|UHD|2160P|3840P)(?![A-Z0-9])")
_NOISE = re.compile(
    r"\(MULTI(?:[- ]SUBS?)?\)"
    r"|(?<![A-Z0-9])(?:4K|UHD|2160P|3840P|HEVC|DOLBY\s+(?:AUDIO|VISION)|BLU-?RAY)(?![A-Z0-9])",
    re.IGNORECASE,
)
_KIDS = re.compile(r"(?<![A-Z])KIDS(?![A-Z])")
_EDGE_PUNCTUATION = " -–|/:"
_BRANDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), name)
    for pattern, name in (
        (r"^NETFLIX(?![A-Z])", "Netflix"),
        (r"^(?:AMAZON|PRIME\s+VIDEO)(?![A-Z])", "Prime Video"),
        (r"^APPLE\s*(?:TV\s*)?\+", "Apple TV+"),
        (r"^DISNEY\s*\+", "Disney+"),
        (r"^HBO\s+MAX(?![A-Z])", "Max"),
        (r"^PARAMOUNT\s*\+", "Paramount+"),
        (r"^PEACOCK(?![A-Z])", "Peacock"),
        (r"^SHOWTIME(?![A-Z])", "Showtime"),
        (r"^SKY(?![A-Z])", "Sky"),
        (r"^DISCOVERY\s*\+", "Discovery+"),
        (r"^CRUNCHYROLL(?![A-Z])", "Crunchyroll"),
    )
)
_FIXES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), name)
    for pattern, name in (
        (r"^NEW RELEASES?$", "New Releases"),
        (r"^(?:MANGA\s*/\s*ANIME|ANIME\s*/\s*MANGA)$", "Anime"),
        (r"^(?:DOCU-\S+|DOCUMENTAR(?:Y|IES))$", "Documentaries"),
    )
)
# All-caps input cannot tell an acronym from a word, so the usual ones are listed.
_ACRONYMS = frozenset(
    {"TV", "PPV", "VIP", "VOD", "HD", "FHD", "UHD", "SD", "3D", "4K", "IMAX", "UK", "US", "USA", "EU", "DC"}
    | {"NFL", "NBA", "NHL", "MLB", "MLS", "NCAA", "UFC", "WWE", "MMA", "F1", "UEFA", "FIFA"}
)
# Channel groups are named after networks and leagues far more often than shelves are.
_GROUP_ACRONYMS = _ACRONYMS | frozenset(
    {"ABC", "CBS", "NBC", "FOX", "CW", "PBS", "ESPN", "BTN", "TNT", "TBS", "HBO", "AMC", "MTV", "BET", "CNN", "BBC"}
    | {"NCAAF", "NCAAB"}
)
_CONSONANTS = frozenset("BCDFGHJKLMNPQRSTVWXZ")  # not Y: SKY and MY are words
_LETTERS_THEN_DIGITS = re.compile(r"([^\W\d_]+)\d*")
# Providers name a group after one of its channels; a viewer reads it as the kind of channel.
_PLURALS = {"sport": "sports", "movie": "movies", "kid": "kids", "documentary": "documentaries"}
_SMALL_WORDS = frozenset({"a", "an", "and", "for", "in", "of", "on", "or", "the", "to"})
_NAME_TO_CODE = {name.upper(): code for code, name in LANG_NAMES.items() if code != UNKNOWN_LANGUAGE}


def _is_superscript(ch: str) -> bool:
    return unicodedata.decomposition(ch).startswith(("<super>", "<sub>"))


def _strip_noise(text: str) -> tuple[str, bool]:
    """Remove quality decorations. The flag says one of them marked the content as 4K."""
    four_k = False
    words: list[str] = []
    for token in text.split():
        marks = "".join(ch for ch in token if _is_superscript(ch))
        if marks:
            four_k = four_k or bool(_FOUR_K.search(unicodedata.normalize("NFKC", marks).upper()))
            token = "".join(ch for ch in token if not _is_superscript(ch)).strip(_EDGE_PUNCTUATION)
            if not token:
                continue
        words.append(token)
    plain = " ".join(words)
    four_k = four_k or bool(_FOUR_K.search(plain.upper()))
    plain = _NOISE.sub(" ", plain)
    return " ".join(plain.split()).strip(_EDGE_PUNCTUATION), four_k


def _is_group_acronym(part: str) -> bool:
    """Upper-case letters (a channel number may follow: ``TNT2``) that are an abbreviation, not a word."""
    found = _LETTERS_THEN_DIGITS.fullmatch(part)
    letters = found.group(1) if found else part
    return (
        part in _GROUP_ACRONYMS
        or letters in _GROUP_ACRONYMS
        or (len(letters) in (2, 3) and _CONSONANTS.issuperset(letters))
    )


def _title_case(text: str, is_acronym: Callable[[str], bool] = _ACRONYMS.__contains__) -> str:
    def cased(match: re.Match[str]) -> str:
        part = match.group(0)
        return part if is_acronym(part) else part.capitalize()

    out: list[str] = []
    for word in text.split():
        if not word.isupper() or is_acronym(word):
            out.append(word)  # mixed case was written deliberately
        elif out and word.lower() in _SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append(re.sub(r"[^\W_]+(?:'[^\W_]+)?", cased, word))
    return " ".join(out)


def _language_code(tag: str) -> str | None:
    """A title tag is language evidence only when it is a language code.

    Title tags mix language codes with brand abbreviations, so the country
    fallback used for categories would turn brands into languages here.
    """
    token = tag.upper()
    if token in ("UK", "US", "USA"):
        return "eng"
    low = token.lower()
    if len(token) == 2:
        return ISO1_TO_3.get(low)
    if len(token) == 3:
        return low if low in LANG_NAMES and low != UNKNOWN_LANGUAGE else ISO2B_TO_3.get(low)
    return None


def _split_language(category: str) -> tuple[str, str | None]:
    """Strip an explicit leading language tag ("EN - ", "ENGLISH ") and report its language."""
    tag = _LEADING_TAG.match(category)
    if tag:
        language = lang_from_token(tag.group("tag"))
        if language:
            return category[tag.end() :], language
    word = _LEADING_WORD.match(category)
    if word:
        name = word.group("word").upper()
        language = LANG_KEYWORDS.get(name) or _NAME_TO_CODE.get(name)
        if language:
            return category[word.end() :], language
    return category, None


def _shelf(cleaned: str) -> str:
    upper = cleaned.upper()
    for pattern, name in (*_BRANDS, *_FIXES):
        if pattern.search(upper):
            return name
    return _title_case(cleaned)


def present_title(
    name: str,
    raw_category: str,
    *,
    year: int | None,
    is_adult: bool,
    default_language: str | None,
    shelf_map: Mapping[str, str],
    adult_categories: Collection[str] = (),
) -> Presented:
    raw_category = raw_category or ""
    category = " ".join(raw_category.split())
    title = " ".join((name or "").split())
    title_language: str | None = None
    quality_mark = False
    tag = _TITLE_TAG.match(title)
    if tag:
        title = title[tag.end() :]
        quality_mark = bool(tag.group("uhd")) or tag.group("tag").split("-")[0] == "4K"
        title_language = _language_code(tag.group("tag").split("-")[0])
    title = _TRAILING_COUNTRY.sub("", title)
    suffix = _TRAILING_YEAR.search(title)
    if suffix and title[: suffix.start()].strip():
        title = title[: suffix.start()].strip()
        year = year or int(suffix.group(1))

    remainder, category_language = _split_language(category)
    cleaned, category_mark = _strip_noise(remainder)
    shelf = shelf_map.get(raw_category) or shelf_map.get(category) or _shelf(cleaned) or category
    language = (
        category_language
        or title_language
        or normalise_code(default_language)
        or lang_from_category(raw_category)
        or UNKNOWN_LANGUAGE
    )
    return Presented(
        title=title,
        year=year,
        shelf=shelf,
        quality="4K" if quality_mark or category_mark else None,
        language=language,
        kids=bool(_KIDS.search(category.upper())),
        adult=is_adult or raw_category in adult_categories or category in adult_categories,
        raw_category=raw_category,
    )


def _group_name(cleaned: str) -> str:
    """The default display name of a group: title case, abbreviations kept, the kind of channel in the plural."""
    words = _title_case(cleaned, _is_group_acronym).split()
    for index in range(len(words) - 1, -1, -1):
        word = words[index]
        if word.isdigit() or _is_group_acronym(word):
            continue  # "SPORT PPV", "MOVIE 2": the word being described comes before these
        plural = _PLURALS.get(word.lower())
        if plural:
            words[index] = plural.capitalize() if word[0].isupper() else plural
        break
    # Nesting is an operator's decision (``group_map``); a provider's own " / " must not make a second level.
    return " ".join(words).replace(GROUP_SEPARATOR, "/")


def _keyed(names: Collection[str], candidates: Collection[str]) -> str | None:
    """The first configured name that is one of ``candidates``: as written first, then whatever its case."""
    for candidate in candidates:
        if candidate in names:
            return candidate
    folded = {name.casefold(): name for name in reversed(list(names))}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found is not None:
            return found
    return None


def present_group(raw_group: str, *, group_map: Mapping[str, str], hidden_groups: Collection[str] = ()) -> str | None:
    """Display name for a live channel group; ``None`` means the group is hidden.

    A ``group_map`` value may be a path of two levels, ``Parent / Child``. Config keys name a group
    by the provider's name, by the name this function gives it, or by the name it gave before
    abbreviations and plurals were handled ("Sport", "Abc"): configs written then keep working.
    """
    group = " ".join((raw_group or "").split())
    remainder = group
    tag = _LEADING_TAG.match(group)
    # "XX| " is a country tag whatever the letters; other separators need a known code.
    if tag and ("|" in tag.group("sep") or lang_from_token(tag.group("tag"))):
        remainder = group[tag.end() :]
    cleaned, _ = _strip_noise(remainder)
    name = _group_name(cleaned) or group or "Uncategorized"
    known_as = (raw_group, group, _title_case(cleaned) or group or "Uncategorized", name)
    if _keyed(hidden_groups, known_as) is not None:
        return None
    key = _keyed(group_map.keys(), known_as)
    return (group_path(group_map[key]) if key is not None else "") or name
