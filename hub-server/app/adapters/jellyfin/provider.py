"""Jellyfin adapter.

- auth: /Users/AuthenticateByName or Quick Connect (/QuickConnect/Initiate → /QuickConnect/Connect →
  /Users/AuthenticateWithQuickConnect)
- library: /Users/{id}/Items (Recursive, Movie/Series), children via ParentId
- playback: /Items/{id}/PlaybackInfo, direct /Videos/{id}/stream?static=true&api_key=…
- progress: /Sessions/Playing/Progress (+ /Sessions/Playing/Stopped); resume: /Users/{id}/Items/Resume
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.domain.entities import CatalogItem, MediaKind, SourceRef, StreamTarget, TestResult
from app.domain.ports import AdapterContext, CatalogSink
from app.infra.http import request_with_retry

log = logging.getLogger(__name__)

PAGE = 300
TICKS_PER_MS = 10_000
_KIND = {"Movie": MediaKind.MOVIE, "Series": MediaKind.SHOW, "Season": MediaKind.SEASON, "Episode": MediaKind.EPISODE}
_FIELDS = "ProviderIds,Overview,Genres,DateCreated,MediaSources,ProductionYear,CommunityRating,RunTimeTicks"


def auth_header(client_id: str, product: str, token: str | None = None) -> str:
    parts = [
        f'Client="{product}"',
        'Device="StreamHub Server"',
        f'DeviceId="{client_id}"',
        'Version="1.0.0"',
    ]
    if token:
        parts.append(f'Token="{token}"')
    return "MediaBrowser " + ", ".join(parts)


def _parse_date(v: Any) -> datetime | None:
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    if "." in s:  # Jellyfin uses 7 fractional digits
        head, _, tail = s.partition(".")
        frac = "".join(ch for ch in tail if ch.isdigit())[:6]
        tz = tail[len("".join(ch for ch in tail if ch.isdigit())) :]
        s = f"{head}.{frac}{tz}"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class JellyfinSource:
    def __init__(self, ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> None:
        self.ref = ref
        self._cfg = config
        self._ctx = ctx
        self.server = str(config.get("server_url") or "").rstrip("/")
        self.token: str | None = config.get("access_token") or None
        self.user_id: str | None = config.get("user_id") or None

    def _h(self) -> dict[str, str]:
        return {
            "Authorization": auth_header(self._ctx.client_id, self._ctx.product, self.token),
            "Accept": "application/json",
        }

    def config_summary(self) -> dict[str, Any]:
        return {
            "server_url": self.server,
            "username": self._cfg.get("username"),
            "linked": bool(self.token),
            "quick_connect_code": self._cfg.get("quick_connect_code") if not self.token else None,
        }

    def secrets(self) -> list[str]:
        return [x for x in (self.token, self._cfg.get("password"), self._cfg.get("quick_connect_secret")) if x]

    async def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        async with self._ctx.http_factory(headers=self._h()) as client:
            return await request_with_retry(client, method, f"{self.server}{path}", **kw)

    async def _json(self, path: str, **params: Any) -> Any:
        resp = await self._req("GET", path, params=params or None)
        if resp.status_code == 401:
            raise PermissionError("jellyfin unauthorized")
        resp.raise_for_status()
        return resp.json()

    # ---- auth
    async def authenticate(self) -> dict[str, Any]:
        """Password auth. Returns config updates."""
        resp = await self._req(
            "POST",
            "/Users/AuthenticateByName",
            json={"Username": self._cfg.get("username") or "", "Pw": self._cfg.get("password") or ""},
        )
        if resp.status_code in (400, 401):
            raise PermissionError("jellyfin login failed")
        resp.raise_for_status()
        data = resp.json()
        self.token = data["AccessToken"]
        self.user_id = data["User"]["Id"]
        return {"access_token": self.token, "user_id": self.user_id, "password": None}

    async def start_quick_connect(self) -> dict[str, Any]:
        resp = await self._req("POST", "/QuickConnect/Initiate")
        if resp.status_code == 401:
            raise PermissionError("quick connect disabled")
        resp.raise_for_status()
        data = resp.json()
        return {"quick_connect_secret": data["Secret"], "quick_connect_code": data["Code"]}

    async def complete_auth(self) -> tuple[bool, dict[str, Any]]:
        if self.token and self.user_id:
            return True, {}
        if self._cfg.get("username") and self._cfg.get("password"):
            return True, await self.authenticate()
        secret = self._cfg.get("quick_connect_secret")
        if not secret:
            if not self._cfg.get("quick_connect"):
                return False, {}
            upd = await self.start_quick_connect()
            self._cfg.update(upd)
            return False, upd
        resp = await self._req("GET", "/QuickConnect/Connect", params={"secret": secret})
        if resp.status_code in (400, 404):  # secret expired → start over
            upd = await self.start_quick_connect()
            self._cfg.update(upd)
            return False, upd
        resp.raise_for_status()
        if not resp.json().get("Authenticated"):
            return False, {}
        resp = await self._req("POST", "/Users/AuthenticateWithQuickConnect", json={"Secret": secret})
        resp.raise_for_status()
        data = resp.json()
        self.token = data["AccessToken"]
        self.user_id = data["User"]["Id"]
        return True, {
            "access_token": self.token,
            "user_id": self.user_id,
            "quick_connect_secret": None,
            "quick_connect_code": None,
            "quick_connect": None,
        }

    async def _ensure(self) -> None:
        if not self.token or not self.user_id:
            done, _ = await self.complete_auth()
            if not done or not self.token:
                raise PermissionError("jellyfin not linked")

    async def test(self) -> TestResult:
        try:
            info = await self._json("/System/Info/Public")
            await self._ensure()
            return TestResult(True, f"Jellyfin {info.get('Version', '')}".strip())
        except PermissionError as exc:
            return TestResult(False, str(exc))
        except httpx.HTTPError as exc:
            return TestResult(False, f"Jellyfin unreachable: {type(exc).__name__}")

    # ---- mapping
    def _img(self, item_id: str, kind: str = "Primary", tag: str | None = None) -> str:
        # Jellyfin serves item images without auth; keep tokens out of persisted/client URLs.
        q = {"tag": tag} if tag else {}
        return f"{self.server}/Items/{item_id}/Images/{kind}" + (f"?{urlencode(q)}" if q else "")

    def map_item(self, d: dict[str, Any]) -> CatalogItem | None:
        kind = _KIND.get(str(d.get("Type")))
        iid = d.get("Id")
        if kind is None or not iid:
            return None
        prov = {str(k).lower(): v for k, v in (d.get("ProviderIds") or {}).items()}
        tmdb = None
        try:
            tmdb = int(prov["tmdb"]) if prov.get("tmdb") else None
        except ValueError:
            tmdb = None
        tags = d.get("ImageTags") or {}
        backdrops = d.get("BackdropImageTags") or []
        parent = (
            d.get("SeasonId") if kind == MediaKind.EPISODE else d.get("SeriesId") if kind == MediaKind.SEASON else None
        )
        ticks = d.get("RunTimeTicks")
        ud = d.get("UserData") or {}
        container = None
        for ms in d.get("MediaSources") or []:
            container = ms.get("Container")
            break
        return CatalogItem(
            ext_id=str(iid),
            kind=kind,
            title=str(d.get("Name") or ""),
            year=d.get("ProductionYear"),
            overview=d.get("Overview") or None,
            poster=self._img(iid, "Primary", tags.get("Primary")) if tags.get("Primary") else None,
            backdrop=self._img(iid, "Backdrop", backdrops[0]) if backdrops else None,
            rating=d.get("CommunityRating"),
            genres=list(d.get("Genres") or []),
            tmdb_id=tmdb,
            imdb_id=prov.get("imdb"),
            parent_ext_id=str(parent) if parent else None,
            season_number=d.get("IndexNumber") if kind == MediaKind.SEASON else d.get("ParentIndexNumber"),
            episode_number=d.get("IndexNumber") if kind == MediaKind.EPISODE else None,
            duration_ms=int(ticks) // TICKS_PER_MS if ticks else None,
            added_at=_parse_date(d.get("DateCreated")),
            extra={
                "container": container,
                "resume_ms": int(ud.get("PlaybackPositionTicks") or 0) // TICKS_PER_MS,
                "series_name": d.get("SeriesName"),
            },
        )

    async def sync(self, sink: CatalogSink) -> None:
        await self._ensure()
        start = 0
        while True:
            data = await self._json(
                f"/Users/{self.user_id}/Items",
                Recursive="true",
                IncludeItemTypes="Movie,Series",
                Fields=_FIELDS,
                StartIndex=start,
                Limit=PAGE,
                SortBy="DateCreated",
                SortOrder="Descending",
            )
            rows = data.get("Items") or []
            items = [i for i in (self.map_item(r) for r in rows) if i]
            if items:
                await sink.add_items(items)
            start += len(rows)
            if not rows or start >= int(data.get("TotalRecordCount") or 0):
                break

    async def item_detail(self, item: CatalogItem) -> CatalogItem:
        await self._ensure()
        d = await self._json(f"/Users/{self.user_id}/Items/{item.ext_id}")
        return self.map_item(d) or item

    async def children(self, item: CatalogItem) -> list[CatalogItem]:
        await self._ensure()
        if item.kind == MediaKind.SHOW:
            seasons = await self._json(f"/Shows/{item.ext_id}/Seasons", UserId=self.user_id, Fields=_FIELDS)
            eps = await self._json(f"/Shows/{item.ext_id}/Episodes", UserId=self.user_id, Fields=_FIELDS)
            rows = (seasons.get("Items") or []) + (eps.get("Items") or [])
        else:
            data = await self._json(f"/Users/{self.user_id}/Items", ParentId=item.ext_id, Fields=_FIELDS)
            rows = data.get("Items") or []
        return [i for i in (self.map_item(r) for r in rows) if i]

    async def search(self, query: str, limit: int = 20) -> list[CatalogItem]:
        await self._ensure()
        data = await self._json(
            f"/Users/{self.user_id}/Items",
            SearchTerm=query,
            Recursive="true",
            IncludeItemTypes="Movie,Series,Episode",
            Fields=_FIELDS,
            Limit=limit,
        )
        return [i for i in (self.map_item(r) for r in data.get("Items") or []) if i]

    async def continue_watching(self, limit: int = 20) -> list[tuple[CatalogItem, int]]:
        await self._ensure()
        data = await self._json(f"/Users/{self.user_id}/Items/Resume", Limit=limit, Fields=_FIELDS)
        out: list[tuple[CatalogItem, int]] = []
        for r in data.get("Items") or []:
            it = self.map_item(r)
            if it:
                out.append((it, int(it.extra.get("resume_ms") or 0)))
        return out

    async def stream_for_item(self, item: CatalogItem) -> StreamTarget:
        await self._ensure()
        container = item.extra.get("container")
        try:
            resp = await self._req(
                "POST", f"/Items/{item.ext_id}/PlaybackInfo", params={"UserId": self.user_id}, json={}
            )
            if resp.status_code < 400:
                srcs = resp.json().get("MediaSources") or []
                if srcs:
                    container = srcs[0].get("Container") or container
                    item.extra["media_source_id"] = srcs[0].get("Id")
        except httpx.HTTPError:
            pass
        q = {"static": "true", "api_key": self.token or ""}
        if item.extra.get("media_source_id"):
            q["MediaSourceId"] = str(item.extra["media_source_id"])
        url = f"{self.server}/Videos/{item.ext_id}/stream?{urlencode(q)}"
        mime = {"mkv": "video/x-matroska", "ts": "video/mp2t", "webm": "video/webm"}.get(
            str(container or "").split(",")[0], "video/mp4"
        )
        return StreamTarget(
            url=url,
            mime=mime,
            duration_ms=item.duration_ms,
            resume_position_ms=int(item.extra.get("resume_ms") or 0),
            title=item.title,
            subtitle=item.extra.get("series_name"),
            poster=item.poster,
        )

    def image_headers(self, url: str) -> dict[str, str] | None:
        if not url.startswith(self.server + "/"):
            return None
        return {"X-Emby-Token": self.token or "", "Accept": "image/*"}

    async def report_progress(self, item: CatalogItem, position_ms: int, duration_ms: int | None, state: str) -> None:
        await self._ensure()
        body = {
            "ItemId": item.ext_id,
            "PositionTicks": position_ms * TICKS_PER_MS,
            "IsPaused": state == "paused",
            "PlayMethod": "DirectStream",
        }
        path = "/Sessions/Playing/Stopped" if state == "stopped" else "/Sessions/Playing/Progress"
        await self._req("POST", path, json=body)


def factory(ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> JellyfinSource:
    return JellyfinSource(ref, config, ctx)
