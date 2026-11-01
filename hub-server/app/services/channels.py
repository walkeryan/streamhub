"""ChannelService: live channel lists, groups, languages, overrides, EPG grid.

Channel lists are stored server-side under a ``list_id`` (hash of the filter
params + profile) so a client can re-fetch the exact ordered list later
(``GET /channels?list_id=…``) and zap Up/Down using ``channel_context``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select, tuple_

from app.adapters.language.country import channel_country, group_country
from app.infra.db import Database
from app.infra.models import Channel, ChannelGroup, Favorite, Profile, Programme, Progress, Setting, Source
from app.services import ids
from app.services.errors import NotFound, ServiceError
from app.services.sources import SourceService, apply_overrides

LIST_PREFIX = "chlist:"
MAX_GRID_HOURS = 24


def programme_dict(p: Programme, ch: Channel | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    catchup = False
    if ch is not None and ch.catchup_days > 0:
        catchup = p.stop <= now and p.start >= now - timedelta(days=ch.catchup_days)
    return {
        "id": ids.prog_id(p.id),
        "channel_id": ids.channel_id(ch.source_id, ch.ext_id) if ch else p.epg_channel,
        "start": p.start,
        "stop": p.stop,
        "title": p.title,
        "description": p.description,
        "category": p.category,
        "icon": p.icon,
        "catchup_available": catchup,
    }


async def now_next(
    db: Database, chans: Sequence[Channel]
) -> dict[tuple[str, str], tuple[Programme | None, Programme | None]]:
    pairs = list({(c.source_id, c.epg_id) for c in chans if c.epg_id})
    out: dict[tuple[str, str], tuple[Programme | None, Programme | None]] = {}
    if not pairs:
        return out
    now = datetime.now(UTC)
    async with db.session() as s:
        for i in range(0, len(pairs), 250):
            chunk = pairs[i : i + 250]
            # Select one current and one future programme per exact source/EPG
            # pair in SQL. Policy channel checks still inspect every overlapping
            # programme independently; this is presentation, never authorization.
            ranked = (
                select(
                    Programme.id,
                    func.row_number()
                    .over(
                        partition_by=(
                            Programme.feed,
                            Programme.epg_channel,
                            case((Programme.start <= now, 0), else_=1),
                        ),
                        order_by=(Programme.start, Programme.id),
                    )
                    .label("position"),
                )
                .where(
                    Programme.epg_channel.in_({epg for _, epg in chunk}),
                    tuple_(Programme.feed, Programme.epg_channel).in_(chunk),
                    Programme.stop > now,
                    Programme.start < now + timedelta(hours=12),
                )
                .subquery()
            )
            rows = (
                await s.execute(
                    select(Programme)
                    .where(Programme.id.in_(select(ranked.c.id).where(ranked.c.position == 1)))
                    .order_by(Programme.epg_channel, Programme.start, Programme.id)
                )
            ).scalars()
            for p in rows:
                cur, nxt = out.get((p.feed, p.epg_channel), (None, None))
                if p.start <= now < p.stop:
                    cur = p
                else:
                    nxt = p
                out[(p.feed, p.epg_channel)] = (cur, nxt)
    return out


class ChannelService:
    def __init__(self, db: Database, sources: SourceService) -> None:
        self._db = db
        self._sources = sources

    # ------------------------------------------------------------------ lists
    async def _profile(self, profile_id: str) -> Profile | None:
        async with self._db.session() as s:
            return await s.get(Profile, profile_id)

    async def _store_list(self, profile_id: str, params: dict[str, Any], channel_ids: list[int]) -> str:
        key = json.dumps({"p": profile_id, **params}, sort_keys=True, default=str)
        list_id = hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:16]
        async with self._db.session() as s:
            row = await s.get(Setting, LIST_PREFIX + list_id)
            value = {
                "profile_id": profile_id,
                "params": params,
                "ids": channel_ids,
                "at": datetime.now(UTC).isoformat(),
            }
            if row is None:
                s.add(Setting(key=LIST_PREFIX + list_id, value=value))
            else:
                row.value = value
            await s.commit()
        return list_id

    async def load_list(self, list_id: str, profile_id: str) -> list[int]:
        async with self._db.session() as s:
            row = await s.get(Setting, LIST_PREFIX + list_id)
        if row is None or not isinstance(row.value, dict) or row.value.get("profile_id") != profile_id:
            raise NotFound("unknown list_id")
        return [int(x) for x in row.value.get("ids") or []]

    async def list_channels(
        self,
        profile_id: str,
        *,
        source_id: str | None = None,
        group: str | None = None,
        language: str | None = None,
        country: str | None = None,
        favorites: bool = False,
        q: str | None = None,
        all_languages: bool = False,
        limit: int | None = None,
        list_id: str | None = None,
        offset: int = 0,
        has_epg: bool = False,
    ) -> dict[str, Any]:
        if list_id:
            order = await self.load_list(list_id, profile_id)
            async with self._db.session() as s:
                rows = list((await s.execute(select(Channel).where(Channel.id.in_(order)))).scalars()) if order else []
            by_id = {r.id: r for r in rows}
            chans = [by_id[i] for i in order if i in by_id]
            return {"list_id": list_id, "channels": await self.to_api(chans, profile_id)}

        query, norm_langs = await self._channel_query(
            profile_id,
            source_id=source_id,
            group=group,
            language=language,
            country=country,
            favorites=favorites,
            q=q,
            all_languages=all_languages,
            has_epg=has_epg,
        )
        async with self._db.session() as s:
            total = int((await s.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
            query = query.order_by(Channel.number, ChannelGroup.sort, Channel.id)
            if offset:
                query = query.offset(offset)
            if limit is not None:
                query = query.limit(limit)
            chans = list((await s.execute(query)).scalars())
        params = {
            "source_id": source_id,
            "group": group,
            "language": sorted(norm_langs),
            "favorites": favorites,
            "q": q or None,
            "offset": offset,
            "limit": limit,
            "has_epg": has_epg,
        }
        if country is not None:
            params["country"] = country.upper()
        lid = await self._store_list(profile_id, params, [c.id for c in chans])
        return {
            "list_id": lid,
            "channels": await self.to_api(chans, profile_id),
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    async def _channel_query(
        self,
        profile_id: str | None,
        *,
        source_id: str | None = None,
        group: str | None = None,
        language: str | None = None,
        country: str | None = None,
        favorites: bool = False,
        q: str | None = None,
        all_languages: bool = False,
        has_epg: bool = False,
        include_hidden: bool = False,
    ) -> tuple[Any, list[str]]:
        prof = await self._profile(profile_id) if profile_id else None
        raw = (
            language.split(",")
            if language is not None
            else (list(prof.languages) if prof and not all_languages else [])
        )
        langs = (
            []
            if any(x.strip().lower() == "all" for x in raw)
            else [self._sources.language.normalise(x.strip()) or x.strip() for x in raw if x.strip()]
        )
        query = (
            select(Channel)
            .join(ChannelGroup, ChannelGroup.id == Channel.group_id)
            .join(Source, Source.id == Channel.source_id)
            .where(Source.enabled.is_(True))
        )
        if not group and not include_hidden:
            query = query.where(ChannelGroup.hidden.is_(False))
        if country and country.lower() != "all":
            codes = [code.strip().upper() for code in country.split(",") if code.strip()]
            known = [code for code in codes if code not in ("UND", "UNKNOWN")]
            missing = or_(Channel.country.is_(None), func.trim(Channel.country) == "")
            async with self._db.session() as session:
                groups = list((await session.execute(select(Channel.group_name).distinct())).scalars())
            inferred = {label: group_country(label)[0] for label in groups}
            matched = [label for label, code in inferred.items() if code in known]
            country_terms: list[Any] = [func.upper(func.trim(Channel.country)).in_(known)]
            if matched:
                country_terms.append(missing & Channel.group_name.in_(matched))
            if any(code in ("UND", "UNKNOWN") for code in codes):
                unknown = [label for label, code in inferred.items() if code is None]
                country_terms.append(missing & Channel.group_name.in_(unknown))
            query = query.where(or_(*country_terms))
        if source_id:
            query = query.where(Channel.source_id == source_id)
        if group:
            query = query.where(
                or_(ChannelGroup.id == group, ChannelGroup.name == group, Channel.group_override == group)
            )
        if langs:
            query = query.where(Channel.language.in_(langs))
        if q and q.strip():
            from app.services.catalog import _escape_like

            like = "%" + _escape_like(ids.normalise_title(q)) + "%"
            conds: list[Any] = [Channel.norm_name.like(like, escape="\\")]
            if q.strip().isdigit():
                conds.append(Channel.number == int(q.strip()))
            query = query.where(or_(*conds))
        if favorites and profile_id:
            # Correlated EXISTS keeps filtering in SQL even for large favorite collections.
            ref = "ch:" + Channel.source_id + ":" + Channel.ext_id
            query = query.where(
                select(Favorite.id)
                .where(
                    Favorite.profile_id == profile_id,
                    Favorite.kind == "channel",
                    Favorite.ref_id == ref,
                )
                .exists()
            )
        if has_epg:
            now = datetime.now(UTC)
            query = query.where(
                select(Programme.id)
                .where(
                    Programme.epg_channel == Channel.epg_id,
                    Programme.feed == Channel.source_id,
                    Programme.stop > now,
                    Programme.start < now + timedelta(hours=12),
                )
                .exists()
            )
        return query, langs

    async def context_for_channel(self, profile_id: str, channel_id: int) -> dict[str, Any] | None:
        """Resolve zap order without serializing thousands of channels or querying programme rows."""
        query, langs = await self._channel_query(profile_id)
        async with self._db.session() as s:
            order = list(
                (
                    await s.execute(
                        query.with_only_columns(Channel.id).order_by(Channel.number, ChannelGroup.sort, Channel.id)
                    )
                ).scalars()
            )
        if channel_id not in order:
            return None
        params = {
            "source_id": None,
            "group": None,
            "language": sorted(langs),
            "favorites": False,
            "q": None,
            "offset": 0,
            "limit": None,
            "has_epg": False,
        }
        list_id = await self._store_list(profile_id, params, order)
        return {"list_id": list_id, "index": order.index(channel_id), "count": len(order)}

    async def to_api(self, chans: Sequence[Channel], profile_id: str) -> list[dict[str, Any]]:
        async with self._db.session() as s:
            favs = set(
                (
                    await s.execute(
                        select(Favorite.ref_id).where(Favorite.profile_id == profile_id, Favorite.kind == "channel")
                    )
                ).scalars()
            )
        nn = await now_next(self._db, chans)
        out = []
        for c in chans:
            cid = ids.channel_id(c.source_id, c.ext_id)
            cur, nxt = nn.get((c.source_id, c.epg_id or ""), (None, None))
            country, country_source = channel_country(c.country, c.group_name)
            out.append(
                {
                    "id": cid,
                    "source_id": c.source_id,
                    "ext_id": c.ext_id,
                    "number": c.number,
                    "name": c.name,
                    "logo": c.logo,
                    "group": c.group_override or c.group_name,
                    "language": c.language or "und",
                    "language_source": c.language_source,
                    "country": country,
                    "country_source": country_source,
                    "epg_id": c.epg_id,
                    "catchup_days": c.catchup_days,
                    "is_favorite": cid in favs,
                    "now": programme_dict(cur, c) if cur else None,
                    "next": programme_dict(nxt, c) if nxt else None,
                }
            )
        return out

    async def channel_by_hub_id(self, hub_id: str) -> Channel:
        try:
            h = ids.parse(hub_id)
        except ValueError as exc:
            raise NotFound("unknown channel") from exc
        if h.scheme != "ch":
            raise NotFound("not a channel id")
        async with self._db.session() as s:
            ch = (
                await s.execute(select(Channel).where(Channel.source_id == h.a, Channel.ext_id == h.b))
            ).scalar_one_or_none()
        if ch is None:
            raise NotFound("channel not found")
        return ch

    # ------------------------------------------------------------------ overrides
    async def update_channel(self, hub_id: str, patch: dict[str, Any], profile_id: str) -> dict[str, Any]:
        ch = await self.channel_by_hub_id(hub_id)
        async with self._db.session() as s:
            row = await s.get(Channel, ch.id)
            assert row is not None
            if "number" in patch:
                n = patch["number"]
                row.number_override = int(n) if n not in (None, "", 0) else None
            if "language" in patch:
                lang = patch["language"]
                if lang in (None, ""):
                    row.language_override = None
                    row.language = (row.data or {}).get("inferred_lang") or "und"
                    row.language_source = (row.data or {}).get("inferred_src")
                else:
                    code = self._sources.language.normalise(str(lang)) or str(lang)
                    row.language_override = code
            if "epg_id" in patch:
                e = patch["epg_id"]
                row.epg_override = str(e) if e else None
                if not e:
                    row.epg_id = row.tvg_id
            if "group" in patch:
                g = patch["group"]
                row.group_override = str(g) if g else None
            if patch.get("name"):
                data = dict(row.data or {})
                data["name_override"] = str(patch["name"])
                row.data = data
                row.name = str(patch["name"])
            await s.commit()
        await apply_overrides(self._db, ch.source_id)
        ch = await self.channel_by_hub_id(hub_id)
        return (await self.to_api([ch], profile_id))[0]

    async def groups(
        self,
        profile_id: str | None = None,
        *,
        language: str | None = None,
        country: str | None = None,
        source_id: str | None = None,
        q: str | None = None,
        favorites: bool = False,
        include_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        query, _ = await self._channel_query(
            profile_id,
            language=language,
            country=country,
            source_id=source_id,
            q=q,
            favorites=favorites,
            include_hidden=include_hidden,
        )
        matched = query.with_only_columns(Channel.id, Channel.group_id).subquery()
        async with self._db.session() as s:
            counts: dict[str, int] = dict(
                (await s.execute(select(matched.c.group_id, func.count()).group_by(matched.c.group_id))).all()
            )
            rows = list(
                (
                    await s.execute(
                        select(ChannelGroup)
                        .join(Source, Source.id == ChannelGroup.source_id)
                        .where(Source.enabled.is_(True))
                        .order_by(Source.priority, ChannelGroup.sort, ChannelGroup.name)
                    )
                ).scalars()
            )
        return [
            {
                "id": g.id,
                "source_id": g.source_id,
                "name": g.name,
                "channel_count": int(counts.get(g.id, 0)),
                "hidden": g.hidden,
                "language_override": g.language_override,
                "sort": g.sort,
            }
            for g in rows
            if counts.get(g.id, 0) or profile_id is None
        ]

    async def update_group(self, group_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._db.session() as s:
            g = await s.get(ChannelGroup, group_id)
            if g is None:
                raise NotFound("group not found")
            if "hidden" in patch and patch["hidden"] is not None:
                g.hidden = bool(patch["hidden"])
            if "sort" in patch and patch["sort"] is not None:
                g.sort = int(patch["sort"])
            clear_lang = False
            if "language_override" in patch:
                lo = patch["language_override"]
                if lo:
                    code = self._sources.language.normalise(str(lo))
                    if not code:
                        raise ServiceError("unknown language")
                    g.language_override = code
                else:
                    g.language_override = None
                    clear_lang = True
            source_id = g.source_id
            await s.commit()
        if clear_lang:
            # restore inferred languages for channels in the group without their own override
            async with self._db.session() as s:
                chans = (
                    await s.execute(
                        select(Channel).where(Channel.group_id == group_id, Channel.language_override.is_(None))
                    )
                ).scalars()
                for c in chans:
                    c.language = (c.data or {}).get("inferred_lang") or "und"
                    c.language_source = (c.data or {}).get("inferred_src")
                await s.commit()
        await apply_overrides(self._db, source_id)
        return next(x for x in await self.groups() if x["id"] == group_id)

    async def languages(self) -> list[dict[str, Any]]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(Channel.language, func.count(Channel.id))
                    .join(ChannelGroup, ChannelGroup.id == Channel.group_id)
                    .join(Source, Source.id == Channel.source_id)
                    .where(ChannelGroup.hidden.is_(False), Source.enabled.is_(True))
                    .group_by(Channel.language)
                    .order_by(func.count(Channel.id).desc())
                )
            ).all()
        lang = self._sources.language
        return [{"code": code or "und", "name": lang.language_name(code or "und"), "count": int(n)} for code, n in rows]

    # ------------------------------------------------------------------ EPG
    async def epg_grid(
        self,
        profile_id: str,
        channel_ids: list[str] | None,
        start: datetime | None,
        stop: datetime | None,
        country: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        now = datetime.now(UTC)
        start = (start or now - timedelta(minutes=30)).astimezone(UTC)
        stop = (stop or start + timedelta(hours=3)).astimezone(UTC)
        if stop <= start:
            raise ServiceError("'to' must be after 'from'")
        if stop - start > timedelta(hours=MAX_GRID_HOURS):
            stop = start + timedelta(hours=MAX_GRID_HOURS)
        if channel_ids:
            requested = []
            for cid in channel_ids[:500]:
                try:
                    ref = ids.parse(cid)
                except ValueError:
                    continue
                if ref.scheme == "ch":
                    requested.append((ref.a, ref.b))
            by_key: dict[tuple[str, str], Channel] = {}
            async with self._db.session() as s:
                unique = list(dict.fromkeys(requested))
                for index in range(0, len(unique), 250):
                    # ORM SELECT retains the active profile's loader criteria.
                    channel_rows = (
                        await s.execute(
                            select(Channel).where(
                                tuple_(Channel.source_id, Channel.ext_id).in_(unique[index : index + 250])
                            )
                        )
                    ).scalars()
                    by_key.update(((ch.source_id, ch.ext_id), ch) for ch in channel_rows)
            chans = [by_key[key] for key in dict.fromkeys(requested) if key in by_key]
        else:
            res = await self.list_channels(profile_id, limit=300, country=country)
            chans = []
            async with self._db.session() as s:
                order = await self.load_list(res["list_id"], profile_id)
                if order:
                    by_id = {r.id: r for r in (await s.execute(select(Channel).where(Channel.id.in_(order)))).scalars()}
                    chans = [by_id[i] for i in order if i in by_id]
        if country and country.lower() != "all" and channel_ids:
            eligible, _ = await self._channel_query(profile_id, country=country, all_languages=True)
            async with self._db.session() as session:
                allowed = set(
                    (
                        await session.execute(
                            eligible.with_only_columns(Channel.id).where(Channel.id.in_([ch.id for ch in chans]))
                        )
                    ).scalars()
                )
            chans = [ch for ch in chans if ch.id in allowed]
        by_epg: dict[tuple[str, str], list[Channel]] = {}
        for c in chans:
            if c.epg_id:
                by_epg.setdefault((c.source_id, c.epg_id), []).append(c)
        out: dict[str, list[dict[str, Any]]] = {ids.channel_id(c.source_id, c.ext_id): [] for c in chans}
        if not by_epg:
            return out
        async with self._db.session() as s:
            keys = list({epg_id for _, epg_id in by_epg})
            for i in range(0, len(keys), 500):
                rows = (
                    await s.execute(
                        select(Programme)
                        .where(
                            Programme.epg_channel.in_(keys[i : i + 500]), Programme.stop > start, Programme.start < stop
                        )
                        .order_by(Programme.start)
                    )
                ).scalars()
                for p in rows:
                    for c in by_epg.get((p.feed, p.epg_channel), []):
                        out[ids.channel_id(c.source_id, c.ext_id)].append(programme_dict(p, c, now))
        return out

    async def programme(self, prog_hub_id: str) -> Programme:
        try:
            h = ids.parse(prog_hub_id)
            pk = int(h.a)
        except ValueError as exc:
            raise NotFound("unknown programme") from exc
        async with self._db.session() as s:
            p = await s.get(Programme, pk)
        if p is None:
            raise NotFound("programme not found")
        return p

    async def on_now(self, profile_id: str, limit: int = 20) -> tuple[list[tuple[Channel, Programme | None]], bool]:
        """Currently scheduled channels, prioritizing this profile's recent viewing."""
        now = datetime.now(UTC)
        query, _ = await self._channel_query(profile_id)
        history = (
            select(Progress.ref_id, Progress.updated_at)
            .where(
                Progress.profile_id == profile_id,
                Progress.ref_id.like("ch:%"),
                Progress.updated_at >= now - timedelta(days=90),
            )
            .subquery()
        )
        ref = "ch:" + Channel.source_id + ":" + Channel.ext_id
        query = (
            query.outerjoin(history, history.c.ref_id == ref)
            .where(
                select(Programme.id)
                .where(
                    Programme.feed == Channel.source_id,
                    Programme.epg_channel == Channel.epg_id,
                    Programme.start <= now,
                    Programme.stop > now,
                )
                .exists()
            )
            .add_columns(history.c.updated_at)
            .order_by(history.c.updated_at.desc().nullslast(), Channel.number, ChannelGroup.sort, Channel.id)
            .limit(min(max(limit, 1), 100))
        )
        async with self._db.session() as session:
            rows = (await session.execute(query)).all()
        if not rows:
            # A missing guide must not leave an otherwise playable live library empty.
            # Favorites are explicit choices, but still obey language/hidden filters.
            fallback, _ = await self._channel_query(profile_id, favorites=True)
            async with self._db.session() as session:
                chans = list(
                    (
                        await session.execute(
                            fallback.order_by(Channel.number, Channel.id).limit(min(max(limit, 1), 100))
                        )
                    ).scalars()
                )
                if not chans:
                    fallback, _ = await self._channel_query(profile_id)
                    chans = list(
                        (
                            await session.execute(
                                fallback.order_by(Channel.number, Channel.id).limit(min(max(limit, 1), 100))
                            )
                        ).scalars()
                    )
            return [(ch, None) for ch in chans], False
        chans = [row[0] for row in rows]
        nn = await now_next(self._db, chans)
        pairs = [(c, nn.get((c.source_id, c.epg_id or ""), (None, None))[0]) for c in chans]
        return pairs, any(row[1] is not None for row in rows)
