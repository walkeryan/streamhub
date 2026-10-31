"""AvailabilityService: where can a title be watched?

Merges
- library matches (Plex/Jellyfin by tmdb/imdb id; Xtream VOD/series by tmdb field or
  normalised title + year), and
- TMDB/JustWatch watch providers for the region, filtered to the streaming services the
  user configured as ``streaming_service`` sources (matched via registry tmdb_provider_ids).

Badges are ordered: in-app playable first (by source priority), then hand-off services.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, delete, or_, select

from app.domain.entities import SourceType
from app.domain.ports import MetadataProvider
from app.infra.db import Database
from app.infra.models import Availability, ServiceRegistry, Source, Title, utcnow
from app.services import ids
from app.services.tiles import badge_for_source, sort_badges

ATTRIBUTION = "Availability data by JustWatch"


class AvailabilityService:
    def __init__(self, db: Database, meta: MetadataProvider) -> None:
        self._db = db
        self._meta = meta

    async def library_matches(
        self,
        media_type: str,
        *,
        tmdb_id: int | None,
        imdb_id: str | None,
        title: str | None,
        year: int | None,
    ) -> list[Title]:
        kinds = list(ids.MOVIE_KINDS if media_type == "movie" else ids.SHOW_KINDS)
        conds = []
        if tmdb_id:
            conds.append(Title.tmdb_id == tmdb_id)
        if imdb_id:
            conds.append(Title.imdb_id == imdb_id)
        if title:
            norm = ids.normalise_title(title)
            if norm:
                yc = Title.year.is_(None) if year is None else or_(Title.year == year, Title.year.is_(None))
                conds.append(and_(Title.tmdb_id.is_(None), Title.norm_title == norm, yc))
        if not conds:
            return []
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(Title)
                    .join(Source, Source.id == Title.source_id)
                    .where(Title.kind.in_(kinds), or_(*conds), Source.enabled.is_(True))
                )
            ).scalars()
            return list(rows)

    async def provider_offers(self, media_type: str, tmdb_id: int, region: str) -> list[Availability]:
        """Cached per (title, region) in the availability table; refreshed via the TMDB cache."""
        async with self._db.session() as s:
            rows = list(
                (
                    await s.execute(
                        select(Availability).where(
                            Availability.media_type == media_type,
                            Availability.tmdb_id == tmdb_id,
                            Availability.region == region,
                        )
                    )
                ).scalars()
            )
        fresh = rows and (utcnow() - rows[0].fetched_at).total_seconds() < 24 * 3600
        if fresh or not self._meta.enabled:
            return rows
        offers = await self._meta.watch_providers(media_type, tmdb_id, region)
        async with self._db.session() as s:
            await s.execute(
                delete(Availability).where(
                    Availability.media_type == media_type,
                    Availability.tmdb_id == tmdb_id,
                    Availability.region == region,
                )
            )
            new = [
                Availability(
                    media_type=media_type,
                    tmdb_id=tmdb_id,
                    region=region,
                    provider_id=o.provider_id,
                    provider_name=o.provider_name,
                    kind=o.kind,
                    logo_path=o.logo_path,
                )
                for o in offers
            ]
            s.add_all(new)
            await s.commit()
        return new

    async def handoff_badges(
        self, media_type: str, tmdb_id: int, sources: dict[str, Source]
    ) -> list[dict[str, Any]]:
        services = [s for s in sources.values() if s.type == SourceType.STREAMING_SERVICE.value and s.enabled]
        if not services:
            return []
        async with self._db.session() as s:
            reg = {r.key: r for r in (await s.execute(select(ServiceRegistry))).scalars()}
        badges: list[dict[str, Any]] = []
        offers_by_region: dict[str, list[Availability]] = {}
        for svc in services:
            row = reg.get(svc.service_key or "")
            if row is None:
                continue
            region = svc.region or "US"
            if region not in offers_by_region:
                offers_by_region[region] = await self.provider_offers(media_type, tmdb_id, region)
            pids = {int(x) for x in row.tmdb_provider_ids or []}
            hits = [o for o in offers_by_region[region] if o.provider_id in pids]
            if hits:
                best = min(hits, key=lambda o: ["flatrate", "free", "ads", "rent", "buy"].index(o.kind)
                           if o.kind in ("flatrate", "free", "ads", "rent", "buy") else 9)
                badges.append(badge_for_source(svc, in_app=False, kind=best.kind))
        return badges

    async def badges_for(
        self,
        media_type: str,
        *,
        tmdb_id: int | None,
        imdb_id: str | None,
        title: str | None,
        year: int | None,
        sources: dict[str, Source],
        is_in_app: Any,
        include_handoff: bool = True,
    ) -> tuple[list[dict[str, Any]], list[Title]]:
        matches = await self.library_matches(
            media_type, tmdb_id=tmdb_id, imdb_id=imdb_id, title=title, year=year
        )
        badges = [
            badge_for_source(sources[m.source_id], in_app=is_in_app(sources[m.source_id].type))
            for m in matches
            if m.source_id in sources
        ]
        if include_handoff and tmdb_id:
            badges += await self.handoff_badges(media_type, tmdb_id, sources)
        return sort_badges(badges, sources), matches
