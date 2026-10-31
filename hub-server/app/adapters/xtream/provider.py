"""Xtream Codes API adapter.

- player_api.php actions for auth, live/VOD/series categories, streams, info, short EPG
- xmltv.php as EPG source
- stream URLs /{live|movie|series}/{user}/{pass}/{id}.{ext}
- catch-up via timeshift.php (tv_archive / tv_archive_duration)

Credentials only ever appear in playback URLs handed to the PlaybackResolver; the
config summary and logs are redacted.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.adapters.xtream import fields as F
from app.domain.entities import (
    CatalogItem,
    ChannelInfo,
    EpgSourceSpec,
    MediaKind,
    ProgrammeInfo,
    SourceRef,
    StreamTarget,
    TestResult,
)
from app.domain.ports import AdapterContext, CatalogSink
from app.infra.http import request_with_retry
from app.infra.logging import redact_url

log = logging.getLogger(__name__)

BATCH = 500
_MIME = {
    "ts": "video/mp2t",
    "m3u8": "application/x-mpegURL",
    "mp4": "video/mp4",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
}


class XtreamError(Exception):
    pass


def normalise_server(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    # tolerate users pasting player_api.php or get.php URLs
    for suffix in ("/player_api.php", "/get.php", "/xmltv.php"):
        if suffix in url:
            url = url.split(suffix, 1)[0]
    return url


class XtreamClient:
    def __init__(self, ctx: AdapterContext, server: str, username: str, password: str, ua: str | None) -> None:
        self._ctx = ctx
        self.server = normalise_server(server)
        self.username = username
        self.password = password
        self.ua = ua
        self.server_info: dict[str, Any] = {}

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self.ua} if self.ua else {}

    async def api(self, action: str | None = None, **params: Any) -> Any:
        q: dict[str, Any] = {"username": self.username, "password": self.password}
        if action:
            q["action"] = action
        q.update({k: v for k, v in params.items() if v is not None})
        url = f"{self.server}/player_api.php"
        async with self._ctx.http_factory(timeout=60.0, headers=self._headers()) as client:
            resp = await request_with_retry(client, "GET", url, params=q)
        if resp.status_code in (401, 403):
            raise XtreamError("unauthorized")
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as exc:
            raise XtreamError(f"invalid JSON from {redact_url(url)}") from exc

    async def authenticate(self) -> dict[str, Any]:
        data = await self.api()
        if not isinstance(data, dict):
            raise XtreamError("unexpected auth response")
        ui = data.get("user_info") or {}
        if not F.as_bool(ui.get("auth", 1 if ui else 0)):
            raise XtreamError("authentication failed")
        status = (F.as_str(ui.get("status")) or "active").lower()
        if status not in ("active", "trial"):
            raise XtreamError(f"account status {status}")
        self.server_info = data.get("server_info") or {}
        return data

    # ---- url builders
    def stream_url(self, kind: str, stream_id: str, ext: str) -> str:
        return f"{self.server}/{kind}/{self.username}/{self.password}/{stream_id}.{ext}"

    def timeshift_url(self, stream_id: str, start: datetime, duration_min: int, ext: str = "ts") -> str:
        start_s = start.astimezone(UTC).strftime("%Y-%m-%d:%H-%M")
        # Panels expect a literal "YYYY-MM-DD:HH-MM" (several reject "%3A"), so keep ':' unescaped.
        q = urlencode(
            {
                "username": self.username,
                "password": self.password,
                "stream": stream_id,
                "start": start_s,
                "duration": duration_min,
            },
            safe=":",
        )
        return f"{self.server}/streaming/timeshift.php?{q}"

    def xmltv_url(self) -> str:
        return f"{self.server}/xmltv.php?" + urlencode({"username": self.username, "password": self.password})


def _cat_map(cats: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(cats, list):
        for c in cats:
            if isinstance(c, dict):
                cid = F.as_str(F.first(c, "category_id", "id", "categoryId"))
                name = F.as_str(F.first(c, "category_name", "name", "title"))
                if cid:
                    out[cid] = name or cid
    return out


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):  # some panels return {"1": {...}, "2": {...}}
        return [x for x in data.values() if isinstance(x, dict)]
    return []


def map_live(row: dict[str, Any], cats: dict[str, str], output: str, client: XtreamClient) -> ChannelInfo | None:
    sid = F.as_str(F.first(row, "stream_id", "id", "streamId", "num"))
    if not sid:
        return None
    name = F.as_str(F.first(row, "name", "title", "stream_name")) or f"Channel {sid}"
    cat_id = F.as_str(F.first(row, "category_id", "categoryId"))
    archive = F.as_bool(F.first(row, "tv_archive", "tvArchive", "archive") or 0)
    days = F.as_int(F.first(row, "tv_archive_duration", "archive_duration", "tvArchiveDuration"), 0) or 0
    ext = output if output in ("ts", "m3u8") else "ts"
    return ChannelInfo(
        ext_id=sid,
        name=name,
        stream_url=None,  # rebuilt at playback time; never persist credentials in catalog rows
        logo=F.as_str(F.first(row, "stream_icon", "icon", "logo", "cover")),
        group=cats.get(cat_id or "") or F.as_str(row.get("category_name")) or "Uncategorized",
        tvg_id=F.as_str(F.first(row, "epg_channel_id", "epg_id", "tvg_id")),
        tvg_name=name,
        number=F.as_int(F.first(row, "num", "number")),
        catchup_days=days if archive else 0,
        catchup_type="xtream" if archive else None,
        headers={"User-Agent": client.ua} if client.ua else {},
        extra={"stream_id": sid, "ext": ext, "added": F.as_int(row.get("added"))},
    )


def map_vod(row: dict[str, Any], cats: dict[str, str], client: XtreamClient) -> CatalogItem | None:
    sid = F.as_str(F.first(row, "stream_id", "id", "streamId", "vod_id"))
    if not sid:
        return None
    name = F.as_str(F.first(row, "name", "title", "o_name")) or f"VOD {sid}"
    ext = F.as_str(F.first(row, "container_extension", "extension", "ext")) or "mp4"
    cat_id = F.as_str(F.first(row, "category_id", "categoryId"))
    return CatalogItem(
        ext_id=sid,
        kind=MediaKind.VOD,
        title=name,
        year=F.as_int(row.get("year")) or F.year_from(row.get("releaseDate"), row.get("release_date"), name),
        poster=F.as_str(F.first(row, "stream_icon", "cover", "movie_image", "icon", "cover_big")),
        rating=F.as_float(F.first(row, "rating", "rating_5based")),
        tmdb_id=F.tmdb_from(row),
        imdb_id=F.imdb_from(row),
        group=cats.get(cat_id or "") or None,
        genres=F.genres_from(row.get("genre")),
        added_at=F.as_epoch(row.get("added")),
        headers={"User-Agent": client.ua} if client.ua else {},
        extra={"ext": ext, "stream_id": sid},
    )


def map_series(row: dict[str, Any], cats: dict[str, str]) -> CatalogItem | None:
    sid = F.as_str(F.first(row, "series_id", "id", "seriesId"))
    if not sid:
        return None
    name = F.as_str(F.first(row, "name", "title")) or f"Series {sid}"
    backdrop = row.get("backdrop_path")
    cat_id = F.as_str(F.first(row, "category_id", "categoryId"))
    return CatalogItem(
        ext_id=sid,
        kind=MediaKind.SERIES,
        title=name,
        year=F.year_from(row.get("releaseDate"), row.get("release_date"), row.get("year"), name),
        overview=F.as_str(F.first(row, "plot", "description", "overview")),
        poster=F.as_str(F.first(row, "cover", "stream_icon", "cover_big")),
        backdrop=F.as_str(backdrop),
        rating=F.as_float(F.first(row, "rating", "rating_5based")),
        genres=F.genres_from(row.get("genre")),
        tmdb_id=F.tmdb_from(row),
        group=cats.get(cat_id or "") or None,
        added_at=F.as_epoch(F.first(row, "last_modified", "added")),
        extra={"series_id": sid},
    )


def map_episodes(series_id: str, info: dict[str, Any], client: XtreamClient) -> list[CatalogItem]:
    out: list[CatalogItem] = []
    episodes = info.get("episodes") or {}
    if isinstance(episodes, list):  # some panels: list of lists
        episodes = {str(i + 1): eps for i, eps in enumerate(episodes)}
    seasons_meta = {
        str(F.as_int(s.get("season_number"), 0)): s for s in _as_list(info.get("seasons")) if isinstance(s, dict)
    }
    if not isinstance(episodes, dict):
        return out
    for season_key, eps in episodes.items():
        season_no = F.as_int(season_key, 0) or 0
        smeta = seasons_meta.get(str(season_no), {})
        out.append(
            CatalogItem(
                ext_id=f"{series_id}:s{season_no}",
                kind=MediaKind.SEASON,
                title=F.as_str(smeta.get("name")) or f"Season {season_no}",
                poster=F.as_str(F.first(smeta, "cover", "cover_big")),
                parent_ext_id=series_id,
                season_number=season_no,
            )
        )
        for ep in _as_list(eps):
            eid = F.as_str(F.first(ep, "id", "episode_id", "stream_id"))
            if not eid:
                continue
            ext = F.as_str(F.first(ep, "container_extension", "extension")) or "mp4"
            raw_info = ep.get("info")
            einfo: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
            dur_s = F.as_int(F.first(einfo, "duration_secs", "durationSecs"))
            out.append(
                CatalogItem(
                    ext_id=eid,
                    kind=MediaKind.EPISODE,
                    title=F.as_str(F.first(ep, "title", "name")) or f"Episode {ep.get('episode_num')}",
                    overview=F.as_str(F.first(einfo, "plot", "description")),
                    poster=F.as_str(F.first(einfo, "movie_image", "cover_big", "cover")),
                    rating=F.as_float(einfo.get("rating")),
                    parent_ext_id=f"{series_id}:s{season_no}",
                    season_number=F.as_int(ep.get("season"), season_no),
                    episode_number=F.as_int(F.first(ep, "episode_num", "episode", "num")),
                    duration_ms=dur_s * 1000 if dur_s else None,
                    added_at=F.as_epoch(ep.get("added")),
                    headers={"User-Agent": client.ua} if client.ua else {},
                    extra={"ext": ext, "series_id": series_id},
                )
            )
    return out


class XtreamSource:
    def __init__(self, ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> None:
        self.ref = ref
        self._cfg = config
        self._ctx = ctx
        self.output = str(config.get("output") or "ts").lower()
        self.client = XtreamClient(
            ctx,
            str(config.get("server_url") or ""),
            str(config.get("username") or ""),
            str(config.get("password") or ""),
            config.get("user_agent") or None,
        )

    def config_summary(self) -> dict[str, Any]:
        parts = urlsplit(self.client.server)
        return {
            "server_url": f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}",
            "username_set": bool(self.client.username),
            "output": self.output,
            "user_agent": self.client.ua,
            "refresh_hours": int(self._cfg.get("refresh_hours") or 24),
            "proxy": self._cfg.get("proxy", "auto"),
            "max_connections": self._cfg.get("max_connections"),
        }

    def secrets(self) -> list[str]:
        return [self.client.password, self.client.username]

    async def test(self) -> TestResult:
        try:
            data = await self.client.authenticate()
        except (XtreamError, httpx.HTTPError) as exc:
            return TestResult(False, f"Xtream: {redact_url(str(exc))}")
        ui = data.get("user_info") or {}
        exp = F.as_epoch(ui.get("exp_date"))
        msg = "Authenticated"
        if exp:
            msg += f"; expires {exp.date().isoformat()}"
        mc = F.as_int(ui.get("max_connections"))
        if mc:
            msg += f"; max connections {mc}"
        return TestResult(True, msg)

    async def sync(self, sink: CatalogSink) -> None:
        data = await self.client.authenticate()
        ui = data.get("user_info") or {}
        mc = F.as_int(ui.get("max_connections"))
        if mc:
            await sink.set_config_hint("max_connections", mc)
        allowed = ui.get("allowed_output_formats")
        if isinstance(allowed, list) and allowed:
            await sink.set_config_hint("allowed_output_formats", allowed)

        live_cats = _cat_map(await self.client.api("get_live_categories"))
        batch: list[ChannelInfo] = []
        for row in _as_list(await self.client.api("get_live_streams")):
            ch = map_live(row, live_cats, self.output, self.client)
            if ch:
                batch.append(ch)
            if len(batch) >= BATCH:
                await sink.add_channels(batch)
                batch = []
        if batch:
            await sink.add_channels(batch)

        if self._cfg.get("include_vod", True):
            vod_cats = _cat_map(await self.client.api("get_vod_categories"))
            items: list[CatalogItem] = []
            for row in _as_list(await self.client.api("get_vod_streams")):
                it = map_vod(row, vod_cats, self.client)
                if it:
                    items.append(it)
                if len(items) >= BATCH:
                    await sink.add_items(items)
                    items = []
            if items:
                await sink.add_items(items)

        if self._cfg.get("include_series", True):
            ser_cats = _cat_map(await self.client.api("get_series_categories"))
            items = []
            for row in _as_list(await self.client.api("get_series")):
                it = map_series(row, ser_cats)
                if it:
                    items.append(it)
                if len(items) >= BATCH:
                    await sink.add_items(items)
                    items = []
            if items:
                await sink.add_items(items)

    # ---- detail
    async def item_detail(self, item: CatalogItem) -> CatalogItem:
        if item.kind == MediaKind.VOD:
            info = await self.client.api("get_vod_info", vod_id=item.ext_id)
            if isinstance(info, dict):
                raw = info.get("info")
                i: dict[str, Any] = raw if isinstance(raw, dict) else {}
                item.overview = F.as_str(F.first(i, "plot", "description")) or item.overview
                item.tmdb_id = item.tmdb_id or F.tmdb_from(i)
                item.imdb_id = item.imdb_id or F.imdb_from(i)
                bd = i.get("backdrop_path")
                item.backdrop = F.as_str(bd) or item.backdrop
                item.genres = F.genres_from(i.get("genre")) or item.genres
                dur = F.as_int(F.first(i, "duration_secs", "durationSecs"))
                if dur:
                    item.duration_ms = dur * 1000
                item.year = item.year or F.year_from(i.get("releasedate"), i.get("release_date"))
        return item

    async def children(self, item: CatalogItem) -> list[CatalogItem]:
        series_id = str(item.extra.get("series_id") or item.ext_id).split(":")[0]
        info = await self.client.api("get_series_info", series_id=series_id)
        if not isinstance(info, dict):
            return []
        eps = map_episodes(series_id, info, self.client)
        if item.kind == MediaKind.SEASON:
            return [e for e in eps if e.parent_ext_id == item.ext_id]
        return eps

    # ---- EPG
    async def epg_sources(self) -> list[EpgSourceSpec]:
        if self._cfg.get("epg_url"):
            return [EpgSourceSpec(url=str(self._cfg["epg_url"]))]
        hdrs = {"User-Agent": self.client.ua} if self.client.ua else {}
        return [EpgSourceSpec(url=self.client.xmltv_url(), headers=hdrs)]

    async def short_epg(self, channel: ChannelInfo, limit: int = 4) -> list[ProgrammeInfo]:
        data = await self.client.api("get_short_epg", stream_id=channel.ext_id, limit=limit)
        rows = _as_list(data.get("epg_listings") if isinstance(data, dict) else data)
        out: list[ProgrammeInfo] = []
        for r in rows:
            start = F.as_epoch(r.get("start_timestamp"))
            stop = F.as_epoch(r.get("stop_timestamp"))
            if not start or not stop:
                start = _parse_dt(r.get("start"))
                stop = _parse_dt(r.get("end") or r.get("stop"))
            if not start or not stop:
                continue
            out.append(
                ProgrammeInfo(
                    channel=channel.tvg_id or channel.ext_id,
                    start=start,
                    stop=stop,
                    title=F.maybe_b64(r.get("title")) or "",
                    description=F.maybe_b64(r.get("description")),
                )
            )
        return out

    # ---- playback
    async def stream_for_channel(self, channel: ChannelInfo) -> StreamTarget:
        ext = str(channel.extra.get("ext") or self.output)
        url = self.client.stream_url("live", channel.ext_id, ext)
        return StreamTarget(url=url, mime=_MIME.get(ext, "video/mp2t"), headers=dict(channel.headers), live=True)

    async def catchup_for_channel(self, channel: ChannelInfo, programme: ProgrammeInfo) -> StreamTarget | None:
        if channel.catchup_days <= 0:
            return None
        now = datetime.now(UTC)
        if programme.start < now - timedelta(days=channel.catchup_days) or programme.start > now:
            return None
        dur_min = max(int((programme.stop - programme.start).total_seconds() // 60), 1)
        url = self.client.timeshift_url(channel.ext_id, programme.start, dur_min)
        return StreamTarget(
            url=url,
            mime="video/mp2t",
            headers=dict(channel.headers),
            live=False,
            duration_ms=dur_min * 60_000,
        )

    async def stream_for_item(self, item: CatalogItem) -> StreamTarget:
        ext = str(item.extra.get("ext") or "mp4")
        kind = "series" if item.kind == MediaKind.EPISODE else "movie"
        url = self.client.stream_url(kind, item.ext_id, ext)
        return StreamTarget(
            url=url,
            mime=_MIME.get(ext, "video/mp4"),
            headers=dict(item.headers),
            duration_ms=item.duration_ms,
            title=item.title,
            poster=item.poster,
        )


def _parse_dt(v: Any) -> datetime | None:
    s = F.as_str(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def factory(ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> XtreamSource:
    return XtreamSource(ref, config, ctx)
