"""Source-exact Plex library videos with explicit sports genres, never live feeds."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from sqlalchemy import String, cast, func, or_, select

from app.infra.models import Source, Title
from app.services import ids
from app.services.audio_selection import audio_info
from app.services.catalog import CatalogService, _escape_like
from app.services.sports import SPORT_RULES, classify_sport

SCAN_LIMIT = 2000


async def sports_recordings(
    catalog: CatalogService,
    profile_id: str,
    *,
    allowed_refs: Callable[[str, list[str]], Awaitable[set[str]]],
    language: str | None = None,
    sport: str | None = None,
    q: str | None = None,
    limit: int = 40,
    offset: int = 0,
) -> dict[str, Any]:
    keywords = [name.lower() for name, _ in SPORT_RULES] + [
        "nfl",
        "nba",
        "wnba",
        "mlb",
        "nhl",
        "epl",
        "uefa",
        "pga",
        "nascar",
        "ufc",
        "ipl",
    ]
    genres = func.lower(cast(Title.genres, String))
    query = (
        select(Title, Source.name)
        .join(Source, Source.id == Title.source_id)
        .where(
            Source.type == "plex",
            Source.enabled.is_(True),
            Title.kind.in_(("movie", "episode")),
            Title.extra["part_key"].as_string().is_not(None),
            or_(*(genres.like(f"%{word}%") for word in keywords)),
        )
    )
    if q and q.strip():
        query = query.where(Title.title.ilike("%" + _escape_like(q.strip()) + "%", escape="\\"))
    async with catalog._db.session() as session:
        rows = list(
            (await session.execute(query.order_by(Title.added_at.desc(), Title.id.desc()).limit(SCAN_LIMIT + 1))).all()
        )
    truncated = len(rows) > SCAN_LIMIT
    rows = rows[:SCAN_LIMIT]
    allowed = await allowed_refs(profile_id, [ids.lib_id(row.source_id, row.ext_id) for row, _ in rows])
    preferred = await catalog.preferred_languages(profile_id, language)
    items = []
    for row, source_name in rows:
        title_id = ids.lib_id(row.source_id, row.ext_id)
        if title_id not in allowed:
            continue
        extra = row.extra or {}
        if not isinstance(extra.get("part_key"), str) or not extra["part_key"].strip():
            continue
        if extra.get("part_available") in (False, 0, "0", "false"):
            continue
        # No classification from a generic game/movie title or a library folder.
        kind = classify_sport("", " ".join(g for g in row.genres or [] if isinstance(g, str)))
        if kind is None or (sport and sport != kind):
            continue
        audio = audio_info(extra, label=row.title, group=row.group_name)
        known = set(audio["audio_languages"])
        # Unknown is discoverable, not silently labelled English/playable in English.
        if preferred == ["und"] and known:
            continue
        if preferred and known and not known.intersection(preferred):
            continue
        original = None
        try:
            original = date.fromisoformat(str(extra.get("originally_available_at"))).isoformat()
        except ValueError:
            pass
        items.append(
            {
                "title_id": title_id,
                "title": row.title,
                "source_id": row.source_id,
                "source_name": source_name,
                "sport": kind,
                "originally_available_at": original,
                "duration_ms": row.duration_ms,
                "audio_languages": audio["audio_languages"],
                "audio_verified": audio["audio_verified"],
                "language_hint": audio["language_hint"],
                "kind": "library_video",
                "live": False,
            }
        )
    limit, offset = max(1, min(100, limit)), max(0, offset)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "truncated": truncated,
    }
