"""What a provider's game-day channel name says.

Sports panels sell "event slots": numbered channels of one network that are renamed for
each game instead of carrying a guide (``XX (NET 001) | Sport: Away at Home (2026-10-02
14:50:00)``, or, without a date, ``LEAGUE | 02 - 1pm Away at Home``). The name is then the
only schedule there is, and the manager in front of the panel fills the empty guide with
placeholder programmes that repeat it in fixed blocks.

Everything here is pure and total: a name that is not a slot is ``None`` and is shown as it
is stored. The stored name is never rewritten, so favourites, channel numbers and recording
keys do not move; callers ask for the display name where a channel is shown. What differs
between deployments comes from the source config (``event_timezone``,
``event_duration_minutes``): the embedded time is the provider's local time.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_EVENT_MINUTES = 210
# A slot's game has no guide row; its programme id says so and carries the channel row and the start.
SLOT_PROGRAMME = "prog:slot-"
SlotState = Literal["live", "upcoming", "ended", "unscheduled"]


# SQL LIKE patterns, for a name with runs of spaces collapsed: every slot name matches one of them.
# A catalog has tens of thousands of channels, many with a "|" in the name, and only these few
# are worth handing to the parser. A timestamp, "(NETWORK 001) |", or "| 02 -" with one to three digits.
SLOT_NAME_LIKE: tuple[str, ...] = (
    "%20__-__-__%",
    "%) |%",
    "%)|%",
    *(
        f"%|{before}{'_' * digits}{after}{dash}%"
        for before in ("", " ")
        for digits in (1, 2, 3)
        for after in ("", " ")
        for dash in "-–"
    ),
)


@dataclass(frozen=True)
class SlotConfig:
    """The per-source half: where the provider's clock is, and how long a game is taken to last."""

    zone: ZoneInfo
    duration: timedelta

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> SlotConfig:
        zone = ZoneInfo("UTC")
        name = config.get("event_timezone")
        if isinstance(name, str) and name.strip():
            try:
                zone = ZoneInfo(name.strip())
            except (ZoneInfoNotFoundError, ValueError):
                pass
        minutes = config.get("event_duration_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 24 * 60:
            minutes = DEFAULT_EVENT_MINUTES
        return cls(zone=zone, duration=timedelta(minutes=minutes))

    def summary(self) -> dict[str, Any]:
        return {"event_timezone": self.zone.key, "event_duration_minutes": int(self.duration.total_seconds() // 60)}


DEFAULT_SLOT_CONFIG = SlotConfig.from_config({})


@dataclass(frozen=True)
class EventSlot:
    network: str
    number: str | None  # as the provider writes it: "008" stays "008"
    sport: str | None
    title: str
    home: str | None
    away: str | None
    starts_local: datetime | None  # naive, on the provider's clock

    @property
    def channel_name(self) -> str:
        return f"{self.network} {self.number}" if self.number else self.network

    @property
    def teams(self) -> list[str]:
        """Both sides in the order the name gives them, like ``Programme.teams``."""
        if not (self.home and self.away):
            return []
        first = self.title.find(self.home), self.title.find(self.away)
        return [self.home, self.away] if first[0] <= first[1] else [self.away, self.home]

    def window(self, config: SlotConfig) -> tuple[datetime, datetime] | None:
        if self.starts_local is None:
            return None
        # An hour the zone repeats is its first pass; one it skips keeps the offset before the jump.
        start = self.starts_local.replace(tzinfo=config.zone).astimezone(UTC)
        return start, start + config.duration

    def state(self, now: datetime, config: SlotConfig) -> SlotState:
        window = self.window(config)
        if window is None:
            return "unscheduled"
        return "upcoming" if now < window[0] else "live" if now < window[1] else "ended"


_QUALITY_MARKS = ("4K", "UHD", "FHD", "HD", "SD")
_QUALITY = re.compile(r"(?<![A-Z0-9])(4K|UHD|FHD|HD|SD)(?![A-Z0-9])")
_QUALITY_TAIL = re.compile(r"(?:\s*[\[(]?\b(?:4K|UHD|FHD|HD|SD)\b[\])]?)+\s*$", re.IGNORECASE)
_COUNTRY_LEAD = re.compile(r"^\(?\s*[A-Z]{2}\s*\)?\s*[:|]\s*(?=\S)")
# "[XX] (NETWORK 001) | text": the bracketed network and number is what marks a dated slot.
_BRACKETED = re.compile(
    r"^(?:\(\s*[A-Z]{2,3}\s*\)|[A-Z]{2,3})?\s*\(\s*(?P<network>[^()|]*?)\s*(?P<number>\d{1,4})\s*\)\s*\|\s*(?P<body>\S.*)$"
)
# "LEAGUE | 02 - 1pm text": a bare label, a slot number and a dash.
_NUMBERED = re.compile(
    r"^(?P<network>[^()|]*[^()|\s])\s*\|\s*(?P<number>\d{1,3})\s*[-–]\s*"
    r"(?:(?P<hint>\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)\s+)?(?P<body>\S.*)$",
    re.IGNORECASE,
)
_STAMP = re.compile(
    r"\s*\(\s*(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})[T\s]+(?P<h>\d{1,2}):(?P<min>\d{2})(?::(?P<s>\d{2}))?\s*\)$"
)
_SPORT = re.compile(r"^(?P<sport>[^:]{2,40}?)\s*:\s+(?P<event>\S.*)$")
_DIVISION = re.compile(r"\s*\(\s*(?P<mark>[MW])\s*\)$", re.IGNORECASE)
_SIDES = re.compile(r"\s+(?P<sep>at|@|vs?\.?|versus)\s+", re.IGNORECASE)
_SIDE_MAX = 60


def _is_superscript(ch: str) -> bool:
    return unicodedata.decomposition(ch).startswith(("<super>", "<sub>"))


def _plain(text: str) -> str:
    """Superscript and modifier letters as the plain ones they stand for (``ᴴᴰ`` is ``HD``)."""
    return "".join(unicodedata.normalize("NFKC", ch) if _is_superscript(ch) else ch for ch in text)


def _squash(text: str) -> str:
    return " ".join(text.split())


def quality_hint(name: str) -> str | None:
    """The picture quality a channel name advertises, in plain or superscript letters."""
    found = set(_QUALITY.findall(_plain(name or "").upper()))
    return next((mark for mark in _QUALITY_MARKS if mark in found), None)


def _without_quality(text: str) -> str:
    kept = " ".join(word for token in text.split() if (word := "".join(ch for ch in token if not _is_superscript(ch))))
    return _QUALITY.sub(" ", kept) if kept else text


def network_name(name: str) -> str:
    """A sports network's name without its country tag and trailing quality marks."""
    text = _squash("".join(ch for ch in name if not _is_superscript(ch)))
    text = _COUNTRY_LEAD.sub("", text)
    return _QUALITY_TAIL.sub("", text).strip(" -–|:") or _squash(name)


def is_placeholder(title: str | None, channel_name: str | None) -> bool:
    """A programme that only repeats its channel's name is filler for a missing guide, not a listing.

    So is one titled like an event slot: it repeats a name the channel had when the filler
    was made, and would otherwise outlive the rename as a listing in its own right.
    """
    a, b = _squash(title or "").casefold(), _squash(channel_name or "").casefold()
    return bool(a) and (a == b or parse_event_slot(title or "") is not None)


def _sides(event: str) -> tuple[str | None, str | None]:
    """``(home, away)`` when the text is exactly ``A at B``, ``A @ B``, ``A vs B`` or ``A v B``."""
    found = list(_SIDES.finditer(event))
    if len(found) != 1:
        return None, None
    first, second = event[: found[0].start()].strip(), event[found[0].end() :].strip()
    if not (0 < len(first) <= _SIDE_MAX and 0 < len(second) <= _SIDE_MAX) or first.casefold() == second.casefold():
        return None, None
    # "at" is also an ordinary word ("Night at the Arena"): both sides have to look like names.
    if not all(side[0].isupper() or side[0].isdigit() for side in (first, second)):
        return None, None
    visiting = found[0].group("sep").casefold() in ("at", "@")
    return (second, first) if visiting else (first, second)


def _sport(text: str) -> str:
    sport = _squash(text)
    division = _DIVISION.search(sport)
    if division:
        prefix = "Men's" if division.group("mark").upper() == "M" else "Women's"
        sport = f"{prefix} {sport[: division.start()].strip()}"
    return sport


def _start(stamp: re.Match[str]) -> datetime | None:
    try:
        return datetime(
            int(stamp.group("y")),
            int(stamp.group("m")),
            int(stamp.group("d")),
            int(stamp.group("h")),
            int(stamp.group("min")),
            int(stamp.group("s") or 0),
        )
    except ValueError:
        return None


@lru_cache(maxsize=8192)
def parse_event_slot(name: str) -> EventSlot | None:
    """The slot a channel name (or a placeholder programme's title) describes; ``None`` for any other name."""
    text = _squash(_plain(name or ""))
    bracketed = _BRACKETED.match(text)
    numbered = None if bracketed else _NUMBERED.match(text)
    found = bracketed or numbered
    if found is None:
        return None
    network = _squash(_without_quality(found.group("network"))).strip(" -–:")
    body = found.group("body").strip()
    starts_local = None
    stamp = _STAMP.search(body)
    if stamp:
        starts_local = _start(stamp)
        body = body[: stamp.start()].strip()
    sport = None
    split = _SPORT.match(body)
    # "Cup Final: A at B" without a sport in front reads the same; the first part is kept as given.
    if split and not _SIDES.search(split.group("sport")):
        sport, body = _sport(split.group("sport")), split.group("event").strip()
    home, away = _sides(body)
    if not network or not body:
        return None
    # "XX | 24 - Hour News" and "(XX) (Example 5) | News" are ordinary channels: a name without
    # a timestamp is a slot only when it names a game, or a kick-off time after its number.
    hint = numbered.group("hint") if numbered else None
    if stamp is None and not (home or hint):
        return None
    return EventSlot(
        network=network,
        number=found.group("number"),
        sport=sport,
        title=body,
        home=home,
        away=away,
        starts_local=starts_local,
    )


def slot_programme_id(channel_pk: int, start: datetime | None) -> str:
    """Id of the programme a slot stands for; a dateless slot has no start to carry."""
    return f"{SLOT_PROGRAMME}{channel_pk}-{int(start.timestamp()) if start else 0}"


def display_name(name: str) -> str:
    """What a channel is called on screen: ``NETWORK 001`` for a slot, the stored name otherwise."""
    slot = parse_event_slot(name)
    return slot.channel_name if slot else name
