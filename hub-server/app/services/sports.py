"""Current sports listings from actual source-scoped EPG, never inferred scores or teams."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.infra.models import Programme, Source
from app.services import ids
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


def classify_sport(title: str, category: str | None) -> str | None:
    text = f"{category or ''} {title}"
    for sport, pattern in PATTERNS:
        if pattern.search(text):
            return sport
    return None


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
            if kind is None or (term and term not in f"{row[7]} {row[3]}".casefold()):
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
                        "title": row[7],
                        "category": row[8],
                        "start": row[9],
                        "stop": row[10],
                        "channel_id": ids.channel_id(row[1], row[2]),
                        "channel_name": row[3],
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


async def sports_events(
    channels: ChannelService,
    profile_id: str,
    *,
    date: str | None = None,
    timezone: str = "America/New_York",
    language: str | None = None,
    sport: str | None = None,
    status: str = "all",
    q: str | None = None,
    limit: int = 40,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bounded, source-scoped EPG events. Titles are evidence, not a sports data feed."""
    import hashlib
    from datetime import date as calendar_date
    from datetime import time, timedelta
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from sqlalchemy import or_

    from app.services.errors import ServiceError

    now = now or datetime.now(UTC)
    try:
        zone = ZoneInfo(timezone)
        day = calendar_date.fromisoformat(date) if date else now.astimezone(zone).date()
        if not 1900 <= day.year <= 2100:
            raise ValueError("date outside supported guide range")
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ServiceError("Use an ISO date and a valid IANA timezone") from exc
    if status not in {"all", "current", "upcoming", "replay"}:
        raise ServiceError("Unknown sports status filter")
    start = datetime.combine(day, time.min, zone).astimezone(UTC)
    stop = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
    eligible, _ = await channels._channel_query(profile_id, language=language)
    ch = eligible.subquery()
    # SQL prefilter reduces the bounded read to possible sports listings. The
    # conservative word-boundary classifier below rejects substring false positives.
    keywords = (
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
        )
        .join(Programme, (Programme.feed == ch.c.source_id) & (Programme.epg_channel == ch.c.epg_id))
        .join(Source, Source.id == ch.c.source_id)
        .where(Programme.stop > start, Programme.start < stop, or_(*(text.like(f"%{word}%") for word in keywords)))
        .order_by(Programme.start, Programme.title, Programme.feed, Programme.id, ch.c.id)
    )
    sport_terms = {
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
    if sport:
        if sport not in sport_terms:
            raise ServiceError("Unknown sport filter")
        query = query.where(or_(*(text.like(f"%{word}%") for word in sport_terms[sport])))
    if status == "replay":
        query = query.where(or_(*(text.like(f"%{word}%") for word in ("replay", "rerun", "encore", "repeat"))))
    if q and q.strip():
        term = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(func.lower(Programme.title).like(f"%{term}%", escape="\\"))
    if status == "current":
        query = query.where(Programme.start <= now, Programme.stop > now)
    elif status == "upcoming":
        query = query.where(Programme.start > now)
    cap = 5000
    async with channels._db.session() as session:
        eligible_count = int((await session.execute(select(func.count()).select_from(ch))).scalar_one())
        rows = list((await session.execute(query.limit(cap + 1))).all())
    truncated = len(rows) > cap
    grouped: dict[str, dict[str, Any]] = {}
    scheduled: set[int] = set()
    for row in rows[:cap]:
        kind = classify_sport(row[7], row[8])
        if kind is None:
            continue
        scheduled.add(row[0])
        label = f"{row[7]} {row[8] or ''}"
        replay = bool(re.search(r"\b(?:replay|rerun|encore|repeat)\b", label, re.I))
        live = not replay and bool(re.search(r"(?:^live\s*[:\-]|\(live\)|\blive coverage\b)", label, re.I))
        if status == "replay" and not replay:
            continue
        # Only explicit matchups with identical title and exact schedule are
        # grouped across sources. Generic studio shows retain source/EPG identity.
        matchup = bool(re.search(r"\S.+\s(?:vs?\.?|versus|@)\s.+\S", row[7], re.I))
        identity = "|".join(
            [
                re.sub(r"\s+", " ", row[7]).strip().casefold(),
                kind,
                row[9].isoformat(),
                row[10].isoformat(),
                str(replay),
                "matchup" if matchup else f"{row[1]}:{row[11]}",
            ]
        )
        event_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        event = grouped.setdefault(
            event_id,
            {
                "id": event_id,
                "title": row[7],
                "sport": kind,
                "start": row[9],
                "stop": row[10],
                "status": "upcoming" if row[9] > now else "current" if row[10] > now else "ended",
                "replay": replay,
                "live_evidence": live,
                "grouping_evidence": "matching_title_and_time" if matchup else "same_source_programme",
                "feeds": [],
            },
        )
        event["feeds"].append(
            {
                "programme_id": ids.prog_id(row[6]),
                "channel_id": ids.channel_id(row[1], row[2]),
                "channel_name": row[3],
                "source_id": row[1],
                "source_name": row[5],
                "language": row[4] if row[4] and row[4] != "und" else None,
                "language_verified": False,
            }
        )
    counts = Counter(event["sport"] for event in grouped.values())
    events = [event for event in grouped.values() if not sport or event["sport"] == sport]
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
