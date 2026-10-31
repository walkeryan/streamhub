"""PlaybackResolver: id → PlaybackDescriptor (direct | proxy | hls | handoff).

Mode decision for in-app sources (``decide_mode``):
- android_tv: ``direct`` unless the source policy is ``proxy=always``/``hls`` or the global
  proxy_mode is ``always`` (ExoPlayer handles http, mpeg-ts and custom headers).
- web:
  - global ``proxy_mode=never`` → ``direct``
  - global ``always`` or source ``proxy=always`` → ``proxy`` (``hls`` for raw MPEG-TS live)
  - source ``proxy=hls`` → ``hls``
  - ``auto``: proxy when the upstream is http:// on an https deployment (mixed content,
    X-Forwarded-Proto aware), when custom headers are required (browsers cannot set UA/Referer),
    or when the source caps connections at 1 and another client is already streaming;
    when the source is flagged ``cors: false`` raw MPEG-TS live is remuxed to ``hls``,
    anything else is passed through ``proxy`` (mpegts.js plays TS directly otherwise).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, quote_plus

from app.domain.entities import (
    LIBRARY_SOURCE_TYPES,
    PlaybackMode,
    ProgrammeInfo,
    SourceType,
    StreamTarget,
)
from app.domain.ports import DeeplinkProvider, LiveProvider, MetadataProvider, PlaybackProvider
from app.infra.crypto import Crypto
from app.infra.models import Channel, Source, Title
from app.services import ids
from app.services.availability import AvailabilityService
from app.services.catalog import CatalogService
from app.services.channels import ChannelService
from app.services.context import absolute
from app.services.errors import BadGateway, NotFound, ServiceError
from app.services.settings_service import SettingsService
from app.services.sources import SourceService
from app.services.tiles import row_to_channel, row_to_item
from app.services.user_state import ProgressService

log = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamGrant:
    """Decoded contents of a /stream/{token}."""

    profile_id: str
    source_id: str
    url: str
    headers: dict[str, str]
    mode: str
    live: bool
    channel_key: str | None
    transcode: bool


class StreamTokens:
    def __init__(self, crypto: Crypto, ttl_s: int) -> None:
        self._crypto = crypto
        self.ttl_s = ttl_s

    def issue(self, grant: StreamGrant) -> str:
        return self._crypto.encrypt_json(
            {
                "p": grant.profile_id,
                "s": grant.source_id,
                "u": grant.url,
                "h": grant.headers,
                "m": grant.mode,
                "l": grant.live,
                "c": grant.channel_key,
                "t": grant.transcode,
                "iat": int(time.time()),
            }
        )

    def verify(self, token: str) -> StreamGrant | None:
        d = self._crypto.try_decrypt_json(token, ttl=self.ttl_s)
        if not isinstance(d, dict):
            return None
        return StreamGrant(
            profile_id=str(d["p"]),
            source_id=str(d["s"]),
            url=str(d["u"]),
            headers=dict(d.get("h") or {}),
            mode=str(d.get("m") or "proxy"),
            live=bool(d.get("l")),
            channel_key=d.get("c"),
            transcode=bool(d.get("t")),
        )


class ActiveStreams:
    """Port-free counter the proxy updates; lets the resolver see concurrent use per source."""

    def __init__(self) -> None:
        self._by_source: dict[str, int] = {}

    def inc(self, source_id: str) -> None:
        self._by_source[source_id] = self._by_source.get(source_id, 0) + 1

    def dec(self, source_id: str) -> None:
        n = self._by_source.get(source_id, 0) - 1
        if n <= 0:
            self._by_source.pop(source_id, None)
        else:
            self._by_source[source_id] = n

    def count(self, source_id: str) -> int:
        return self._by_source.get(source_id, 0)


def decide_mode(
    *,
    client: str,
    url: str,
    mime: str | None,
    live: bool,
    headers: dict[str, str],
    source_policy: str,
    global_mode: str,
    https_site: bool,
    max_connections: int | None,
    active_on_source: int,
    cors_ok: bool = True,
) -> PlaybackMode:
    """Pure decision function (see module docstring)."""
    policy = (source_policy or "auto").lower()
    gmode = (global_mode or "auto").lower()
    ts_live = live and (mime or "").lower() == "video/mp2t"
    if client == "web_hls" and ts_live:
        if policy == "never" or gmode == "never":
            return PlaybackMode.DIRECT
        return PlaybackMode.HLS
    if policy == "hls":
        return PlaybackMode.HLS
    if policy == "always" or gmode == "always":
        return PlaybackMode.PROXY
    if policy == "never" or gmode == "never":
        return PlaybackMode.DIRECT
    # auto
    if max_connections == 1 and active_on_source >= 1:
        return PlaybackMode.PROXY  # one upstream, fanned out by the shared proxy
    if client not in ("web", "web_hls"):
        return PlaybackMode.DIRECT
    if not cors_ok:
        # browser can't fetch it directly; raw MPEG-TS is remuxed so hls.js can play it
        return PlaybackMode.HLS if ts_live else PlaybackMode.PROXY
    if https_site and url.startswith("http://"):
        return PlaybackMode.PROXY
    if any(k.lower() in ("user-agent", "referer", "origin", "cookie") for k in headers):
        return PlaybackMode.PROXY
    return PlaybackMode.DIRECT


class PlaybackResolver:
    def __init__(
        self,
        *,
        sources: SourceService,
        catalog: CatalogService,
        channels: ChannelService,
        availability: AvailabilityService,
        progress: ProgressService,
        settings: SettingsService,
        deeplinks: DeeplinkProvider,
        meta: MetadataProvider,
        tokens: StreamTokens,
        active: ActiveStreams,
    ) -> None:
        self._sources = sources
        self._catalog = catalog
        self._channels = channels
        self._availability = availability
        self._progress = progress
        self._settings = settings
        self._deeplinks = deeplinks
        self._meta = meta
        self.tokens = tokens
        self.active = active

    async def resolve(
        self,
        profile_id: str,
        hub_id: str,
        *,
        source_id: str | None = None,
        client: str = "web",
        catchup_programme_id: str | None = None,
        https_site: bool = False,
    ) -> dict[str, Any]:
        client = client if client in ("web", "web_hls", "android_tv") else "web"
        if hub_id.startswith("trailer:"):
            return await self._resolve_trailer(profile_id, hub_id)
        if hub_id.startswith("prog:") and not catchup_programme_id:
            catchup_programme_id = hub_id
            prog = await self._channels.programme(hub_id)
            chans = await self._channels_for_epg(prog.epg_channel, prog.feed)
            if not chans:
                raise NotFound("no channel for programme")
            hub_id = ids.channel_id(chans[0].source_id, chans[0].ext_id)
        try:
            h = ids.parse(hub_id)
        except ValueError as exc:
            raise NotFound("unknown id") from exc
        if h.scheme == "ch":
            return await self._resolve_channel(profile_id, hub_id, client, catchup_programme_id, https_site)
        if h.scheme == "lib":
            row = await self._catalog.title_row(h.a, h.b)
            if row is None:
                raise NotFound("title not found")
            if row.kind in ("show", "series", "season"):
                row = await self._next_episode(profile_id, row)
            return await self._resolve_title(profile_id, row, hub_id, client, https_site)
        if h.scheme == "tmdb" and h.b.isdigit():
            return await self._resolve_tmdb(profile_id, h.a, int(h.b), hub_id, source_id, client, https_site)
        raise NotFound("unknown id")

    async def _resolve_trailer(self, profile_id: str, hub_id: str) -> dict[str, Any]:
        """``trailer:<title id>`` → hand-off to the YouTube app (specific video if TMDB knows one)."""
        title_id = hub_id.split(":", 1)[1]
        try:
            h = ids.parse(title_id)
        except ValueError as exc:
            raise NotFound("unknown id") from exc
        title: str | None = None
        year: int | None = None
        poster: str | None = None
        media_type: str | None = None
        tmdb_id: int | None = None
        if h.scheme == "tmdb" and h.b.isdigit():
            media_type, tmdb_id = h.a, int(h.b)
            meta = await self._meta.details(media_type, tmdb_id) if self._meta.enabled else None
            if meta:
                title, year, poster = meta.title, meta.year, meta.poster
        elif h.scheme == "lib":
            row = await self._catalog.title_row(h.a, h.b)
            if row is None:
                raise NotFound("title not found")
            title, year = row.title, row.year
            if row.tmdb_id:
                media_type, tmdb_id = ids.tmdb_type_for_kind(row.kind), row.tmdb_id
        else:
            raise NotFound("unknown id")
        if not title:
            raise NotFound("title not found")
        key = None
        if media_type and tmdb_id and self._meta.enabled:
            try:
                key = await self._meta.trailer_key(media_type, tmdb_id)
            except Exception:
                key = None
        if key:
            url = f"https://www.youtube.com/watch?v={quote(key, safe='')}"
        else:
            query = " ".join(x for x in (title, str(year) if year else "", "trailer") if x)
            url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        return {
            "id": hub_id,
            "mode": PlaybackMode.HANDOFF.value,
            "url": None,
            "mime": None,
            "headers": {},
            "live": False,
            "resume_position_ms": 0,
            "duration_ms": None,
            "title": title,
            "subtitle": "Trailer on YouTube",
            "poster": poster,
            "channel_context": None,
            "handoff": {
                "service_key": "youtube",
                "android_package": "com.google.android.youtube.tv",
                "android_uri": url,
                "android_component": None,
                "android_extras": {},
                "web_url": url,
                "supports_title_link": key is not None,
            },
        }

    async def _channels_for_epg(self, epg_channel: str, feed: str) -> list[Channel]:
        from sqlalchemy import select

        async with self._sources._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(Channel).where(Channel.epg_id == epg_channel).order_by(Channel.source_id != feed)
                    )
                ).scalars()
            )

    # ------------------------------------------------------------------ channels
    async def _resolve_channel(
        self, profile_id: str, hub_id: str, client: str, catchup_programme_id: str | None, https_site: bool
    ) -> dict[str, Any]:
        ch = await self._channels.channel_by_hub_id(hub_id)
        src, prov = await self._sources.provider_for(ch.source_id)
        if not isinstance(prov, LiveProvider):
            raise ServiceError("source cannot play live channels")
        info = row_to_channel(ch)
        target: StreamTarget | None
        title = ch.name
        subtitle = None
        if catchup_programme_id:
            prog = await self._channels.programme(catchup_programme_id)
            pinfo = ProgrammeInfo(channel=prog.epg_channel, start=prog.start, stop=prog.stop, title=prog.title)
            target = await prov.catchup_for_channel(info, pinfo)
            if target is None:
                raise ServiceError("catch-up not available for this programme", code="catchup_unavailable")
            subtitle = prog.title
        else:
            target = await prov.stream_for_channel(info)
        # channel context: position within the profile's current default list
        ctx = None
        try:
            ctx = await self._channels.context_for_channel(profile_id, ch.id)
        except Exception:
            ctx = None
        desc = self._descriptor(
            profile_id,
            src,
            hub_id,
            target,
            client,
            https_site,
            channel_key=None if catchup_programme_id else f"{src.id}:{ch.ext_id}",
            title=title,
            subtitle=subtitle,
            poster=ch.logo,
        )
        desc["channel_context"] = ctx
        return desc

    # ------------------------------------------------------------------ titles
    async def _next_episode(self, profile_id: str, show: Title) -> Title:
        kids = await self._catalog.children_rows(show)
        eps = [k for k in kids if k.kind == "episode"]
        if not eps:
            raise NotFound("no episodes")
        for e in eps:
            p = await self._progress.get(profile_id, ids.lib_id(e.source_id, e.ext_id))
            if p is None or not p.completed:
                return e
        return eps[0]

    async def _resolve_title(
        self,
        profile_id: str,
        row: Title,
        hub_id: str,
        client: str,
        https_site: bool,
        progress_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        src, prov = await self._sources.provider_for(row.source_id)
        if not isinstance(prov, PlaybackProvider):
            raise ServiceError("source cannot play titles")
        try:
            target = await prov.stream_for_item(row_to_item(row))
        except LookupError as exc:
            raise NotFound(str(exc)) from exc
        except Exception as exc:
            raise BadGateway("could not resolve stream from source") from exc
        lib = ids.lib_id(row.source_id, row.ext_id)
        keys = [hub_id, lib, *(progress_ids or [])]
        p = await self._progress.get(profile_id, *keys)
        # Provider resume belongs to the shared source account, not this profile.
        resume = 0
        if p is not None:
            resume = 0 if p.completed else p.position_ms
        target.resume_position_ms = resume
        subtitle = target.subtitle
        if row.kind == "episode":
            subtitle = f"S{row.season_number or 0:02d}E{row.episode_number or 0:02d}" + (
                f" · {target.subtitle}" if target.subtitle else ""
            )
        image = self._catalog.image_fn({src.id: src})
        return self._descriptor(
            profile_id,
            src,
            hub_id,
            target,
            client,
            https_site,
            channel_key=None,
            title=row.title,
            subtitle=subtitle,
            poster=image(src, row.poster),
        )

    async def _resolve_tmdb(
        self,
        profile_id: str,
        media_type: str,
        tmdb_id: int,
        hub_id: str,
        source_id: str | None,
        client: str,
        https_site: bool,
    ) -> dict[str, Any]:
        sources = await self._sources.rows_by_id()
        meta = await self._meta.details(media_type, tmdb_id) if self._meta.enabled else None
        badges, matches = await self._availability.badges_for(
            media_type,
            tmdb_id=tmdb_id,
            imdb_id=meta.imdb_id if meta else None,
            title=meta.title if meta else None,
            year=meta.year if meta else None,
            sources=sources,
            is_in_app=self._sources.registry.is_in_app,
        )
        by_source = {m.source_id: m for m in matches}
        chosen = None
        if source_id:
            chosen = next((b for b in badges if b["source_id"] == source_id), None)
            if chosen is None:
                raise NotFound("title not available on that source")
        elif badges:
            chosen = badges[0]
        if chosen is None:
            raise NotFound("no source can play this title")
        if chosen["playable_in_app"] and chosen["source_id"] in by_source:
            row = by_source[chosen["source_id"]]
            if row.kind in ("show", "series"):
                row = await self._next_episode(profile_id, row)
                return await self._resolve_title(
                    profile_id, row, ids.lib_id(row.source_id, row.ext_id), client, https_site
                )
            return await self._resolve_title(profile_id, row, hub_id, client, https_site)
        # hand-off
        svc = sources[chosen["source_id"]]
        link = await self._deeplinks.link_for(
            svc.service_key or "", tmdb_type=media_type, tmdb_id=tmdb_id, title=meta.title if meta else ""
        )
        if link is None:
            raise NotFound("service not in registry")
        return {
            "id": hub_id,
            "mode": PlaybackMode.HANDOFF.value,
            "url": None,
            "mime": None,
            "headers": {},
            "live": False,
            "resume_position_ms": 0,
            "duration_ms": (meta.runtime_min * 60000) if meta and meta.runtime_min else None,
            "title": meta.title if meta else svc.name,
            "subtitle": f"Open in {link.name}",
            "poster": meta.poster if meta else None,
            "channel_context": None,
            "handoff": {
                "service_key": link.service_key,
                "android_package": link.android_package or "",
                "android_uri": link.android_uri,
                "android_component": link.android_component,
                "android_extras": link.android_extras,
                "web_url": link.web_url,
                "supports_title_link": link.supports_title_link,
            },
        }

    # ------------------------------------------------------------------ descriptor
    def _descriptor(
        self,
        profile_id: str,
        src: Source,
        hub_id: str,
        target: StreamTarget,
        client: str,
        https_site: bool,
        *,
        channel_key: str | None,
        title: str,
        subtitle: str | None,
        poster: str | None,
    ) -> dict[str, Any]:
        mode = decide_mode(
            client=client,
            url=target.url,
            mime=target.mime,
            live=target.live,
            headers=target.headers,
            source_policy=self._sources.proxy_policy(src),
            global_mode=str(self._settings.get("proxy_mode") or "auto"),
            https_site=https_site,
            max_connections=self._sources.max_connections(src),
            active_on_source=self.active.count(src.id),
            cors_ok=self._sources.decrypt_config(src).get("cors", True) is not False,
        )
        transcode = str(self._settings.get("transcode_hw") or "none") != "none" and bool(
            self._sources.decrypt_config(src).get("transcode")
        )
        url = target.url
        mime = target.mime
        headers = dict(target.headers)
        if mode in (PlaybackMode.PROXY, PlaybackMode.HLS):
            token = self.tokens.issue(
                StreamGrant(
                    profile_id=profile_id,
                    source_id=src.id,
                    url=target.url,
                    headers=target.headers,
                    mode=mode.value,
                    live=target.live,
                    channel_key=channel_key if mode == PlaybackMode.PROXY else None,
                    transcode=transcode,
                )
            )
            if mode == PlaybackMode.HLS:
                url = absolute(f"/api/v1/stream/{token}/index.m3u8")
                mime = "application/x-mpegURL"
            else:
                url = absolute(f"/api/v1/stream/{token}")
            headers = {}
        return {
            "id": hub_id,
            "mode": mode.value,
            "url": url,
            "mime": mime,
            "headers": headers,
            "live": target.live,
            "resume_position_ms": int(target.resume_position_ms or 0),
            "duration_ms": target.duration_ms,
            "title": title,
            "subtitle": subtitle,
            "poster": poster,
            "channel_context": None,
            "handoff": None,
            "drm": None,
        }


def is_library(src: Source) -> bool:
    return SourceType(src.type) in LIBRARY_SOURCE_TYPES
