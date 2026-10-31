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
    ("Soccer", r"\b(?:soccer|premier league|uefa|la liga|bundesliga)\b"),
    ("Football", r"\bfootball\b"),
    ("Tennis", r"\btennis\b"),
    ("Golf", r"\b(?:golf|pga|lpga)\b"),
    ("Motorsport", r"\b(?:formula 1|formula one|nascar|indycar|motogp|motorsport)\b"),
    ("Cricket", r"\bcricket\b"),
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
