"""Sports listings from the source-scoped guide and from event-slot channel names.

Nothing is inferred: a listing is sport when its own title or category says so, teams are
the two sides a title spells out ("A vs B", "A at B") or, for a sport listing whose title is
only a label ("Live: College Football"), the two its description opens with ("A host B"), and
a game is live when a real programme or a dated slot (``app.services.event_slots``) covers this
moment. A placeholder programme that repeats its channel's name is not a listing at all.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from datetime import date as calendar_date
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select

from app.infra.models import Channel, Programme, Source
from app.services import ids
from app.services.errors import ServiceError
from app.services.event_slots import (
    DEFAULT_SLOT_CONFIG,
    SLOT_NAME_LIKE,
    display_name,
    is_placeholder,
    network_name,
    parse_event_slot,
    quality_hint,
    slot_programme_id,
)

if TYPE_CHECKING:  # the channel service stamps these facts on its programmes
    from app.services.channels import ChannelService

# Deliberately conservative: channel/group names and descriptions are not sports evidence.
SPORT_RULES = [
    ("American football", r"\b(?:nfl|ncaa\s+football|college football|american football)\b"),
    ("Basketball", r"\b(?:basketball|nba|wnba|ncaa\s+basketball)\b"),
    ("Baseball", r"\b(?:baseball|mlb)\b"),
    ("Hockey", r"\b(?:hockey|nhl)\b"),
    ("Cricket", r"\b(?:cricket|indian premier league|ipl)\b"),
    ("Soccer", r"\b(?:soccer|english premier league|epl|uefa|afc.*champions league|la ?liga|bundesliga)\b"),
    ("Football", r"\bfootball\b"),
    ("Tennis", r"\btennis\b"),
    ("Golf", r"\b(?:golf|pga|lpga)\b"),
    ("Motorsport", r"\b(?:formula 1|formula one|nascar|indycar|motogp|motorsport)\b"),
    ("Rugby", r"\brugby\b"),
    ("Combat sports", r"\b(?:boxing|ufc|mixed martial arts|wrestling)\b"),
    ("Sports", r"\b(?:sports?|sportscenter|sportscentre|athletics|olympics)\b"),
]
PATTERNS = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in SPORT_RULES]
# The only repeat marker the guide carries: the listing says so in its title or category.
REPLAY = re.compile(r"\b(?:replay|rerun|encore|repeat)\b", re.IGNORECASE)


def classify_sport(title: str, category: str | None) -> str | None:
    text = f"{category or ''} {title}"
    for sport, pattern in PATTERNS:
        if pattern.search(text):
            return sport
    return None


_VERSUS = re.compile(r"\s+(?:vs?\.?|versus)\s+", re.IGNORECASE)
_AT = re.compile(r"\s+(?:at|@)\s+")
# What stands before the first side ("NBA: ", "Live - ", "Tennis | ") and after the second
# (" (Live)", " - Game 3", ": Highlights").
_LEAD = re.compile(r"^.*(?:[:|]|\s[-–])\s+")
_TAIL = re.compile(r"\s*(?:[(\[|]|\s[-–]\s|:\s).*$")
_SIDE_MAX = 60


def split_matchup(title: str) -> list[str]:
    """The two sides a title spells out: "A vs B", "A v B", "A versus B", "A at B", "A @ B".

    A plain title split, for listings already known to be sport. "at" also reads as an
    ordinary word ("Live at the Open"), so there both sides must look like names.
    """
    for pattern, names in ((_VERSUS, False), (_AT, True)):
        parts = pattern.split(title)
        if len(parts) != 2:
            continue
        sides = [_LEAD.sub("", parts[0] + " ").strip(), _TAIL.sub("", parts[1]).strip()]
        if not all(0 < len(side) <= _SIDE_MAX for side in sides) or sides[0].casefold() == sides[1].casefold():
            continue
        if names and not all(side[0].isupper() or side[0].isdigit() for side in sides):
            continue
        return sides
    return []


def matchup_sides(title: str) -> tuple[str | None, str | None]:
    """``(home, away)`` of a title that spells out a matchup: the visitor comes first in "A at B"."""
    teams = split_matchup(title)
    if not teams:
        return None, None
    visiting = len(_VERSUS.split(title)) != 2
    return (teams[1], teams[0]) if visiting else (teams[0], teams[1])


def matchup_teams(title: str, category: str | None) -> list[str]:
    """The teams of a sports listing, when its title names both; otherwise none are guessed."""
    return split_matchup(title) if classify_sport(title, category) is not None else []


# A label-only sport listing names its sides where its description starts: "The No. 20 Kestrels
# (6-3, 4-2 League) host the Badgers (6-3) at Riverside Park." Only that first sentence, only these verbs.
_RECORD = re.compile(r"\s*\([^()]*\)")
# A sentence ends after a lower-case word ("...Seminoles. Pittsburgh"), not after "No." or "St.".
_SENTENCE_END = re.compile(r"(?<=[a-z]{3})[.!?](?:\s|$)")
_SIDE = (
    r"(?:[Tt]he\s+)?(?:undefeated\s+)?(?:(?:[Nn]o\.|#)\s*\d+\s+)?([A-Z0-9][\w.'&-]*(?:\s+(?:[A-Z0-9][\w.'&-]*|of))*)"
)
_HOME_FIRST = ("host", "hosts", "welcome", "welcomes")
_EVEN = ("take on", "takes on", "face", "faces", "meet", "meets", "battle", "battles", "play", "plays")
_VERBS = "|".join(re.escape(v) for v in sorted((*_HOME_FIRST, *_EVEN, "visit", "visits"), key=len, reverse=True))
_LEAD_MATCHUP = re.compile(rf"^\s*{_SIDE}\s+(?P<verb>{_VERBS})\s+{_SIDE}(?P<rest>.*)$")
# "A travel to Pasadena to take on B": B is the side, Pasadena only where they play.
_TRAVEL = re.compile(
    rf"^\s*{_SIDE}\s+(?:travels?|heads?)\s+to\s+[^.]*?\s+to\s+(?:take on|face|meet|play|battle)\s+{_SIDE}(?P<rest>.*)$"
)
# After "visit X" a game goes on with where or when; "visit the Sunshine State for a..." is a place.
_AFTER_VISIT = re.compile(r"^(?:$|[,;:]|\s+(?:at|in|on|this|tonight|today)\b)")
# Capitalised words that end a side without being part of it: "host the Badgers Saturday".
_NOT_A_NAME = re.compile(
    r"(?:\s+(?:of|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Tonight|Today|In|On|At))+$"
)


def _side(text: str) -> str | None:
    side = _NOT_A_NAME.sub("", text.rstrip(".'")).strip()
    if not 1 < len(side) <= _SIDE_MAX or side.isdigit() or "'s" in side or "\u2019s" in side:
        return None
    return side


def description_matchup(description: str | None) -> tuple[str, str | None, str | None] | None:
    """``(title, home, away)`` from a description whose first sentence opens with its two sides.

    "A host B" is "B at A" with A at home, "A visit B" and "A travel to <place> to take on B" are
    "A at B", and an even verb ("take on", "face", "meet") is "A vs B" as a title would put it.
    Rankings and records are not names. Anything else names no sides.
    """
    if not description:
        return None
    sentence = _SENTENCE_END.split(_RECORD.sub("", description[:400]), maxsplit=1)[0]
    travel = _TRAVEL.match(sentence)
    found = travel or _LEAD_MATCHUP.match(sentence)
    if not found:
        return None
    first, second = _side(found.group(1)), _side(found.group(2 if travel else 3))
    if first is None or second is None or first.casefold() == second.casefold():
        return None
    verb = "travel" if travel else found.group("verb")
    if verb in ("visit", "visits") and not _AFTER_VISIT.match(found.group("rest")):
        return None
    if verb in _HOME_FIRST:
        return f"{second} at {first}", first, second
    if verb in ("travel", "visit", "visits"):
        return f"{first} at {second}", second, first
    return f"{first} vs {second}", first, second


def listing_matchup(title: str, category: str | None, description: str | None) -> tuple[str, str | None, str | None]:
    """``(shown title, home, away)`` of a sport listing: its own title when that names the sides,
    else the matchup its description opens with, else its title and no sides."""
    home, away = matchup_sides(title)
    if home is not None or classify_sport(title, category) is None:
        return title, home, away
    return description_matchup(description) or (title, None, None)


def sport_listing_clause() -> Any:
    """SQL prefilter: guide rows whose category or title carries a sport word. The classifier decides."""
    text = func.lower(func.coalesce(Programme.category, "") + " " + Programme.title)
    return or_(*(text.like(f"%{word}%") for word in _KEYWORDS))


@lru_cache(maxsize=4096)
def sport_facts(title: str, category: str | None) -> tuple[bool, tuple[str, ...]]:
    """``(is_sport, teams)`` of a guide entry. Cached: a guide page repeats its listings."""
    is_sport = classify_sport(title, category) is not None
    return is_sport, tuple(split_matchup(title)) if is_sport else ()


async def sports_on_now(
    channels: ChannelService,
    profile_id: str,
    *,
    language: str | None = None,
    sport: str | None = None,
    q: str | None = None,
    limit: int = 40,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    eligible, _ = await channels._channel_query(profile_id, language=language)
    ch = eligible.subquery()
    current = (
        select(
            ch.c.id,
            ch.c.source_id,
            ch.c.ext_id,
            ch.c.name,
            ch.c.number,
            Source.name,
            Programme.id,
            Programme.title,
            Programme.category,
            Programme.start,
            Programme.stop,
            Programme.description,
        )
        .join(Programme, (Programme.feed == ch.c.source_id) & (Programme.epg_channel == ch.c.epg_id))
        .join(Source, Source.id == ch.c.source_id)
        .where(Programme.start <= now, Programme.stop > now)
        .order_by(Programme.start, Programme.title, ch.c.number, ch.c.id, Programme.id)
    )
    items: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    scheduled_channels: set[int] = set()
    total = 0
    term = (q or "").strip().casefold()
    async with channels._db.session() as session:
        eligible_count = int((await session.execute(select(func.count()).select_from(ch))).scalar_one())
        rows = await session.stream(current.execution_options(yield_per=500))
        async for row in rows:
            scheduled_channels.add(row[0])
            kind = classify_sport(row[7], row[8])
            # Filler that repeats the channel's name would classify by the channel, not by a listing.
            if kind is None or is_placeholder(row[7], row[3]) or parse_event_slot(row[3]) is not None:
                continue
            shown, _, _ = listing_matchup(row[7], row[8], row[11])
            if term and term not in f"{row[7]} {shown} {row[3]}".casefold():
                continue
            counts[kind] += 1
            if sport and sport != kind:
                continue
            if offset <= total < offset + limit:
                items.append(
                    {
                        "id": f"{row[0]}:{row[6]}",
                        "sport": kind,
                        "programme_id": ids.prog_id(row[6]),
                        "title": shown,
                        "category": row[8],
                        "start": row[9],
                        "stop": row[10],
                        "channel_id": ids.channel_id(row[1], row[2]),
                        "channel_name": display_name(row[3]),
                        "channel_number": row[4],
                        "source_name": row[5],
                    }
                )
            total += 1
    return {
        "as_of": now,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "sports": [{"name": name, "count": count} for name, count in sorted(counts.items())],
        "coverage": {"eligible_channels": eligible_count, "channels_with_current_schedule": len(scheduled_channels)},
    }


# SQL prefilter for the bounded guide read. The word-boundary classifier decides afterwards.
_KEYWORDS = (
    "sport",
    "football",
    "nfl",
    "ncaa",
    "basketball",
    "nba",
    "wnba",
    "baseball",
    "mlb",
    "hockey",
    "nhl",
    "soccer",
    "premier league",
    "uefa",
    "la liga",
    "laliga",
    "champions league",
    "ipl",
    "epl",
    "bundesliga",
    "tennis",
    "golf",
    "pga",
    "formula",
    "nascar",
    "indycar",
    "motogp",
    "cricket",
    "rugby",
    "boxing",
    "ufc",
    "martial arts",
    "wrestling",
    "athletics",
    "olympics",
)
_SPORT_TERMS = {
    "American football": ("nfl", "ncaa", "college football", "american football"),
    "Basketball": ("basketball", "nba", "wnba", "ncaa"),
    "Baseball": ("baseball", "mlb"),
    "Hockey": ("hockey", "nhl"),
    "Soccer": ("soccer", "premier league", "epl", "uefa", "champions league", "la liga", "laliga", "bundesliga"),
    "Football": ("football",),
    "Tennis": ("tennis",),
    "Golf": ("golf", "pga"),
    "Motorsport": ("formula", "nascar", "indycar", "motogp", "motorsport"),
    "Cricket": ("cricket", "indian premier league", "ipl"),
    "Rugby": ("rugby",),
    "Combat sports": ("boxing", "ufc", "martial arts", "wrestling"),
    "Sports": ("sport", "athletics", "olympics"),
}
_LIVE_LABEL = re.compile(r"(?:^live\s*[:\-]|\(live\)|\blive coverage\b)", re.IGNORECASE)
_MATCHUP = re.compile(r"\S.+\s(?:vs?\.?|versus|@)\s.+\S", re.IGNORECASE)
_STATUSES = {"all", "active", "current", "upcoming", "replay"}
SCAN_CAP = 5000


def _event_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _same_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


async def sports_events(
    channels: ChannelService,
    profile_id: str,
    *,
    date: str | None = None,
    timezone: str = "America/New_York",
    days: int = 1,
    language: str | None = None,
    sport: str | None = None,
    status: str = "all",
    q: str | None = None,
    limit: int = 40,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bounded, source-scoped sports events of ``days`` calendar days from ``date``.

    Two kinds of evidence, neither a sports data feed: guide programmes classified as sport,
    and event slots whose own name carries the game. ``current`` is a programme or a slot
    that covers this moment. A slot whose time has passed is not listed (its name stays for
    weeks); one without a date is listed as a game channel (``kind`` ``channel``) that makes
    no claim about when it plays.
    """
    now = now or datetime.now(UTC)
    try:
        zone = ZoneInfo(timezone)
        day = calendar_date.fromisoformat(date) if date else now.astimezone(zone).date()
        if not 1900 <= day.year <= 2100:
            raise ValueError("date outside supported guide range")
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ServiceError("Use an ISO date and a valid IANA timezone") from exc
    if status not in _STATUSES:
        raise ServiceError("Unknown sports status filter")
    days = max(1, min(7, days))
    start = datetime.combine(day, time.min, zone).astimezone(UTC)
    stop = datetime.combine(day + timedelta(days=days), time.min, zone).astimezone(UTC)
    eligible, _ = await channels._channel_query(profile_id, language=language)
    ch = eligible.subquery()
    text = func.lower(func.coalesce(Programme.category, "") + " " + Programme.title)
    query = (
        select(
            ch.c.id,
            ch.c.source_id,
            ch.c.ext_id,
            ch.c.name,
            ch.c.language,
            Source.name,
            Programme.id,
            Programme.title,
            Programme.category,
            Programme.start,
            Programme.stop,
            Programme.epg_channel,
            ch.c.number,
            Programme.description,
        )
        .join(Programme, (Programme.feed == ch.c.source_id) & (Programme.epg_channel == ch.c.epg_id))
        .join(Source, Source.id == ch.c.source_id)
        .where(
            Programme.stop > start,
            Programme.start < stop,
            sport_listing_clause(),
            # Filler rows are dropped before the cap; the exact comparison below collapses whitespace too.
            func.lower(func.trim(Programme.title)) != func.lower(func.trim(ch.c.name)),
        )
        .order_by(Programme.start, Programme.title, Programme.feed, Programme.id, ch.c.id)
    )
    # A slot names its own sport ("Field Hockey"); only the classifier's sports narrow the guide read.
    guide_sport = sport is None or sport in _SPORT_TERMS
    if sport and guide_sport:
        query = query.where(or_(*(text.like(f"%{word}%") for word in _SPORT_TERMS[sport])))
    if status == "replay":
        query = query.where(or_(*(text.like(f"%{word}%") for word in ("replay", "rerun", "encore", "repeat"))))
    term = (q or "").strip()
    if term:
        like = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        # The description is read for the sides a label-only listing names there; the loop below
        # keeps a row only when the search is in its title or in those sides.
        query = query.where(
            or_(
                func.lower(Programme.title).like(f"%{like}%", escape="\\"),
                func.lower(func.coalesce(Programme.description, "")).like(f"%{like}%", escape="\\"),
            )
        )
    if status == "current":
        query = query.where(Programme.start <= now, Programme.stop > now)
    elif status == "upcoming":
        query = query.where(Programme.start > now)
    elif status == "active":
        query = query.where(Programme.stop > now)
    squashed = func.replace(func.replace(ch.c.name, "  ", " "), "  ", " ")
    slot_query = (
        select(ch.c.id, ch.c.source_id, ch.c.ext_id, ch.c.name, ch.c.language, Source.name, ch.c.number)
        .join(Source, Source.id == ch.c.source_id)
        # Only names that can be slots reach the parser: the cap is for them, not for every "XX | Name".
        .where(or_(*(squashed.like(pattern) for pattern in SLOT_NAME_LIKE)))
        .order_by(ch.c.number, ch.c.id)
    )
    async with channels._db.session() as session:
        eligible_count = int((await session.execute(select(func.count()).select_from(ch))).scalar_one())
        rows = list((await session.execute(query.limit(SCAN_CAP + 1))).all()) if guide_sport else []
        slot_rows = list((await session.execute(slot_query.limit(SCAN_CAP + 1))).all())
    truncated = len(rows) > SCAN_CAP or len(slot_rows) > SCAN_CAP
    configs = await channels._sources.slot_configs() if slot_rows else {}
    grouped: dict[str, dict[str, Any]] = {}
    scheduled: set[int] = set()

    def feed(row: Any, programme_id: str, number: int) -> dict[str, Any]:
        return {
            "programme_id": programme_id,
            "channel_id": ids.channel_id(row[1], row[2]),
            "channel_name": display_name(row[3]),
            "channel_number": number,
            "quality": quality_hint(row[3]),
            "source_id": row[1],
            "source_name": row[5],
            "language": row[4] if row[4] and row[4] != "und" else None,
            "language_verified": False,
        }

    for row in rows[:SCAN_CAP]:
        kind = classify_sport(row[7], row[8])
        # A slot channel is scheduled by its name alone, and filler is no listing.
        if kind is None or is_placeholder(row[7], row[3]) or parse_event_slot(row[3]) is not None:
            continue
        scheduled.add(row[0])
        shown, home, away = listing_matchup(row[7], row[8], row[13])
        if term and term.casefold() not in f"{row[7]} {shown}".casefold():
            continue
        label = f"{row[7]} {row[8] or ''}"
        replay = bool(REPLAY.search(label))
        if status == "replay" and not replay:
            continue
        # Only explicit matchups with identical title and exact schedule are
        # grouped across sources. Generic studio shows retain source/EPG identity.
        # A matchup read from the description is as explicit as one in the title: the stations
        # carrying the same game at the same time are one event.
        matchup = bool(_MATCHUP.search(row[7])) or shown != row[7]
        event_id = _event_id(
            _same_title(shown),
            kind,
            row[9].isoformat(),
            row[10].isoformat(),
            str(replay),
            "matchup" if matchup else f"{row[1]}:{row[11]}",
        )
        event = grouped.setdefault(
            event_id,
            {
                "id": event_id,
                "kind": "event",
                "title": shown,
                "sport": kind,
                "league": None,
                "home": home,
                "away": away,
                "start": row[9],
                "stop": row[10],
                "status": "upcoming" if row[9] > now else "current" if row[10] > now else "ended",
                "replay": replay,
                "live_evidence": not replay and bool(_LIVE_LABEL.search(label)),
                "grouping_evidence": "matching_title_and_time" if matchup else "same_source_programme",
                "feeds": [],
            },
        )
        event["feeds"].append(feed(row, ids.prog_id(row[6]), row[12]))

    folded = term.casefold()
    for row in slot_rows[:SCAN_CAP]:
        slot = parse_event_slot(row[3])
        if slot is None:
            continue
        config = configs.get(row[1], DEFAULT_SLOT_CONFIG)
        state = slot.state(now, config)
        window = slot.window(config)
        replay = bool(REPLAY.search(f"{slot.title} {slot.sport or ''}"))
        if state == "ended" or (status == "replay" and not replay):
            continue
        if window is None:
            # No date in the name: a game channel, offered with today's events and never called live.
            if status not in ("all", "active") or not start <= now < stop:
                continue
            window = (start, stop)
        elif (
            not (window[1] > start and window[0] < stop)
            or (status == "current" and state != "live")
            or (status == "upcoming" and state != "upcoming")
        ):
            continue
        if folded and folded not in f"{slot.title} {slot.channel_name} {slot.sport or ''}".casefold():
            continue
        kind = slot.sport or classify_sport(f"{slot.title} {slot.network}", None) or "Sports"
        scheduled.add(row[0])
        dated = state != "unscheduled"
        # Two channels carrying one game: same title, same sport, same kick-off.
        event_id = _event_id("slot", _same_title(slot.title), kind, window[0].isoformat() if dated else "undated")
        event = grouped.setdefault(
            event_id,
            {
                "id": event_id,
                "kind": "event" if dated else "channel",
                "title": slot.title,
                "sport": kind,
                "league": slot.network,
                "home": slot.home,
                "away": slot.away,
                "start": window[0],
                "stop": window[1],
                "status": {"live": "current", "upcoming": "upcoming"}.get(state, "unscheduled"),
                "replay": replay,
                "live_evidence": state == "live" and not replay,
                "grouping_evidence": "matching_title_and_time",
                "feeds": [],
            },
        )
        event["feeds"].append(feed(row, slot_programme_id(row[0], window[0] if dated else None), row[6]))

    listed = sorted(grouped.values(), key=lambda e: (e["kind"] != "event", e["start"], e["title"].casefold(), e["id"]))
    for event in listed:
        event["feeds"].sort(key=lambda f: (f["channel_number"], f["channel_id"]))
    counts = Counter(event["sport"] for event in listed)
    events = [event for event in listed if not sport or event["sport"] == sport]
    limit, offset = max(1, min(100, limit)), max(0, offset)
    return {
        "as_of": now,
        "date": day.isoformat(),
        "timezone": timezone,
        "events": events[offset : offset + limit],
        "total": len(events),
        "limit": limit,
        "offset": offset,
        "sports": [{"name": name, "count": count} for name, count in sorted(counts.items())],
        "coverage": {"eligible_channels": eligible_count, "channels_with_sports_schedule": len(scheduled)},
        "truncated": truncated,
    }


async def sports_channels(
    channels: ChannelService,
    profile_id: str,
    *,
    language: str | None = None,
    q: str | None = None,
    limit: int = 40,
    offset: int = 0,
) -> dict[str, Any]:
    """Always-on sports networks: channels a sports group or their own name puts there.

    Event slots are left to ``sports_events``. ``now`` is a real guide entry or nothing.
    """
    eligible, _ = await channels._channel_query(profile_id, language=language, q=q)
    label = func.lower(func.coalesce(Channel.group_override, Channel.group_name) + " " + Channel.name)
    query = eligible.where(or_(*(label.like(f"%{word}%") for word in _KEYWORDS))).order_by(Channel.number, Channel.id)
    async with channels._db.session() as session:
        rows = list((await session.execute(query.limit(SCAN_CAP + 1))).scalars())
    networks = [
        row
        for row in rows[:SCAN_CAP]
        if parse_event_slot(row.name) is None
        and classify_sport(row.name, row.group_override or row.group_name) is not None
    ]
    limit, offset = max(1, min(100, limit)), max(0, offset)
    page = networks[offset : offset + limit]
    listed = await channels.to_api(page, profile_id)
    return {
        "channels": [
            {
                "channel_id": item["id"],
                "channel_name": network_name(row.name),
                "channel_number": item["number"],
                "logo": item["logo"],
                "group": item["group"],
                "quality": quality_hint(row.name),
                "language": item["language"] if item["language"] != "und" else None,
                "source_id": item["source_id"],
                "now": item["now"],
            }
            for row, item in zip(page, listed, strict=True)
        ],
        "total": len(networks),
        "limit": limit,
        "offset": offset,
        "truncated": len(rows) > SCAN_CAP,
    }
