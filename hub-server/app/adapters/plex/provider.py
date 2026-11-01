"""Plex adapter.

- PIN link flow: POST https://clients.plex.tv/api/v2/pins?strong=true, poll /pins/{id}
- server discovery via https://clients.plex.tv/api/v2/resources (prefer local connections)
- library: /library/sections, /library/sections/{id}/all, /hubs/search, /library/metadata/{id}[/children]
- direct play: server_url + part.key + ?X-Plex-Token=…
- progress → /:/timeline, continue watching via /hubs/continueWatching (fallback /library/onDeck)
- TMDB/IMDb matching from Guid entries (tmdb://, imdb://)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.domain.entities import (
    CatalogItem,
    MediaKind,
    SourceDraft,
    SourceRef,
    SourceType,
    StreamTarget,
    TestResult,
)
from app.domain.ports import AdapterContext, CatalogSink
from app.infra.http import request_with_retry
from app.services.audio_selection import provider_audio_extra

log = logging.getLogger(__name__)

PLEX_TV = "https://clients.plex.tv"
PAGE = 200
_KIND = {"movie": MediaKind.MOVIE, "show": MediaKind.SHOW, "season": MediaKind.SEASON, "episode": MediaKind.EPISODE}
_MIME = {
    "mp4": "video/mp4",
    "mkv": "video/x-matroska",
    "ts": "video/mp2t",
    "mpegts": "video/mp2t",
    "avi": "video/x-msvideo",
    "mov": "video/mp4",
    "m4v": "video/mp4",
    "webm": "video/webm",
}


def plex_headers(client_id: str, product: str, token: str | None = None) -> dict[str, str]:
    h = {
        "Accept": "application/json",
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Product": product,
        "X-Plex-Version": "1.0",
        "X-Plex-Platform": "Web",
        "X-Plex-Device-Name": product,
    }
    if token:
        h["X-Plex-Token"] = token
    return h


def parse_guids(meta: dict[str, Any]) -> tuple[int | None, str | None]:
    tmdb: int | None = None
    imdb: str | None = None
    guids = meta.get("Guid") or []
    for g in guids if isinstance(guids, list) else []:
        gid = str(g.get("id") or "")
        if gid.startswith("tmdb://"):
            try:
                tmdb = int(gid[7:])
            except ValueError:
                pass
        elif gid.startswith("imdb://"):
            imdb = gid[7:]
    legacy = str(meta.get("guid") or "")
    if tmdb is None and "themoviedb://" in legacy:
        try:
            tmdb = int(legacy.split("themoviedb://", 1)[1].split("?")[0].split("/")[0])
        except ValueError:
            pass
    if imdb is None and "imdb://" in legacy:
        imdb = legacy.split("imdb://", 1)[1].split("?")[0]
    return tmdb, imdb


def pick_connection(resource: dict[str, Any]) -> str | None:
    conns = [c for c in resource.get("connections") or [] if c.get("uri")]
    if not conns:
        return None
    # prefer local, non-relay; then remote; relay last
    conns.sort(key=lambda c: (bool(c.get("relay")), not bool(c.get("local")), c.get("protocol") != "https"))
    return str(conns[0]["uri"]).rstrip("/")


class PlexLinkFlow:
    """Implements LinkFlowProvider for Plex PIN auth."""

    def __init__(self, ctx: AdapterContext) -> None:
        self._ctx = ctx

    def _h(self, token: str | None = None) -> dict[str, str]:
        return plex_headers(self._ctx.client_id, self._ctx.product, token)

    async def start(self) -> dict[str, Any]:
        async with self._ctx.http_factory(headers=self._h()) as client:
            resp = await client.post(f"{PLEX_TV}/api/v2/pins", params={"strong": "true"})
            resp.raise_for_status()
            data = resp.json()
        code = str(data["code"])
        link = "https://app.plex.tv/auth#?" + urlencode(
            {
                "clientID": self._ctx.client_id,
                "code": code,
                "context[device][product]": self._ctx.product,
            }
        )
        return {"pin_id": int(data["id"]), "code": code, "link_url": link}

    async def poll(self, pin_id: int) -> list[SourceDraft] | None:
        async with self._ctx.http_factory(headers=self._h()) as client:
            resp = await client.get(f"{PLEX_TV}/api/v2/pins/{pin_id}")
            resp.raise_for_status()
            token = resp.json().get("authToken")
            if not token:
                return None
            resp = await client.get(
                f"{PLEX_TV}/api/v2/resources",
                params={"includeHttps": 1, "includeRelay": 1},
                headers=self._h(token),
            )
            resp.raise_for_status()
            resources = resp.json()
        drafts: list[SourceDraft] = []
        for r in resources if isinstance(resources, list) else []:
            if "server" not in str(r.get("provides") or ""):
                continue
            url = pick_connection(r)
            if not url:
                continue
            drafts.append(
                SourceDraft(
                    type=SourceType.PLEX,
                    name=str(r.get("name") or "Plex"),
                    config={
                        "server_url": url,
                        "token": r.get("accessToken") or token,
                        "account_token": token,
                        "machine_id": r.get("clientIdentifier"),
                        "connections": [c.get("uri") for c in r.get("connections") or [] if c.get("uri")],
                    },
                )
            )
        return drafts


class PlexSource:
    def __init__(self, ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> None:
        self.ref = ref
        self._cfg = config
        self._ctx = ctx
        self.server = str(config.get("server_url") or "").rstrip("/")
        self.token = str(config.get("token") or "")

    def _h(self) -> dict[str, str]:
        return plex_headers(self._ctx.client_id, self._ctx.product, self.token)

    def config_summary(self) -> dict[str, Any]:
        return {"server_url": self.server, "machine_id": self._cfg.get("machine_id"), "linked": bool(self.token)}

    def secrets(self) -> list[str]:
        return [self.token, str(self._cfg.get("account_token") or "")]

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        async with self._ctx.http_factory(headers=self._h()) as client:
            resp = await request_with_retry(client, "GET", f"{self.server}{path}", params=params or None)
        if resp.status_code == 401:
            raise PermissionError("plex unauthorized")
        resp.raise_for_status()
        data = resp.json()
        return data.get("MediaContainer") or {} if isinstance(data, dict) else {}

    async def test(self) -> TestResult:
        try:
            mc = await self._get("/identity")
            return TestResult(True, f"Plex server {mc.get('version', '')}".strip())
        except PermissionError:
            return TestResult(False, "Plex token rejected")
        except httpx.HTTPError as exc:
            return TestResult(False, f"Plex unreachable: {type(exc).__name__}")

    # ---- mapping
    def _img(self, path: str | None) -> str | None:
        if not path:
            return None
        if path.startswith("http"):
            return path
        # No token in the URL: it is persisted and shown to clients. The API serves it through the
        # signed image proxy, which asks image_headers() for auth.
        return f"{self.server}{path}"

    def map_meta(self, m: dict[str, Any]) -> CatalogItem | None:
        kind = _KIND.get(str(m.get("type")))
        rk = m.get("ratingKey")
        if kind is None or rk is None:
            return None
        tmdb, imdb = parse_guids(m)
        added = m.get("addedAt")
        parent = None
        if kind == MediaKind.SEASON or kind == MediaKind.EPISODE:
            parent = m.get("parentRatingKey")
        part_key = None
        audio_extra: dict[str, Any] = {}
        container = None
        for media in m.get("Media") or []:
            for part in media.get("Part") or []:
                part_key = part.get("key")
                audio_extra = provider_audio_extra(
                    [
                        stream
                        for stream in part.get("Stream") or []
                        if isinstance(stream, dict) and str(stream.get("streamType")) == "2"
                    ]
                )
                container = part.get("container") or media.get("container")
                break
            if part_key:
                break
        return CatalogItem(
            ext_id=str(rk),
            kind=kind,
            title=str(m.get("title") or ""),
            year=m.get("year"),
            overview=m.get("summary") or None,
            poster=self._img(
                m.get("thumb") if kind != MediaKind.EPISODE else (m.get("thumb") or m.get("grandparentThumb"))
            ),
            backdrop=self._img(m.get("art")),
            rating=m.get("audienceRating") or m.get("rating"),
            genres=[g.get("tag") for g in m.get("Genre") or [] if g.get("tag")],
            tmdb_id=tmdb,
            imdb_id=imdb,
            parent_ext_id=str(parent) if parent else None,
            season_number=m.get("index") if kind == MediaKind.SEASON else m.get("parentIndex"),
            episode_number=m.get("index") if kind == MediaKind.EPISODE else None,
            duration_ms=m.get("duration"),
            added_at=datetime.fromtimestamp(int(added), UTC) if added else None,
            extra={
                **audio_extra,
                "part_key": part_key,
                "container": container,
                "view_offset": m.get("viewOffset"),
                "grandparent_title": m.get("grandparentTitle"),
                "section_id": m.get("librarySectionID"),
            },
        )

    async def sync(self, sink: CatalogSink) -> None:
        sections = (await self._get("/library/sections")).get("Directory") or []
        for sec in sections:
            stype = sec.get("type")
            if stype not in ("movie", "show"):
                continue
            key = sec.get("key")
            start = 0
            while True:
                mc = await self._get(
                    f"/library/sections/{key}/all",
                    **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": PAGE, "includeGuids": 1},
                )
                rows = mc.get("Metadata") or []
                items = [i for i in (self.map_meta(r) for r in rows) if i]
                for i in items:
                    i.group = sec.get("title")
                if items:
                    await sink.add_items(items)
                start += len(rows)
                total = int(mc.get("totalSize") or mc.get("size") or 0)
                if not rows or start >= total:
                    break

    async def item_detail(self, item: CatalogItem) -> CatalogItem:
        mc = await self._get(f"/library/metadata/{item.ext_id}", includeGuids=1)
        rows = mc.get("Metadata") or []
        if rows:
            fresh = self.map_meta(rows[0])
            if fresh:
                fresh.group = item.group
                return fresh
        return item

    async def children(self, item: CatalogItem) -> list[CatalogItem]:
        if item.kind == MediaKind.SHOW:
            mc = await self._get(f"/library/metadata/{item.ext_id}/allLeaves", includeGuids=1)
            eps = [i for i in (self.map_meta(r) for r in mc.get("Metadata") or []) if i]
            smc = await self._get(f"/library/metadata/{item.ext_id}/children")
            seasons = [i for i in (self.map_meta(r) for r in smc.get("Metadata") or []) if i]
            return seasons + eps
        mc = await self._get(f"/library/metadata/{item.ext_id}/children", includeGuids=1)
        return [i for i in (self.map_meta(r) for r in mc.get("Metadata") or []) if i]

    async def search(self, query: str, limit: int = 20) -> list[CatalogItem]:
        mc = await self._get("/hubs/search", query=query, limit=limit, includeGuids=1)
        out: list[CatalogItem] = []
        for hub in mc.get("Hub") or []:
            for m in hub.get("Metadata") or []:
                it = self.map_meta(m)
                if it and it.kind in (MediaKind.MOVIE, MediaKind.SHOW, MediaKind.EPISODE):
                    out.append(it)
        return out[:limit]

    async def continue_watching(self, limit: int = 20) -> list[tuple[CatalogItem, int]]:
        rows: list[dict[str, Any]] = []
        try:
            mc = await self._get("/hubs/continueWatching", count=limit, includeGuids=1)
            for hub in mc.get("Hub") or []:
                rows.extend(hub.get("Metadata") or [])
            rows.extend(mc.get("Metadata") or [])
        except (httpx.HTTPError, PermissionError):
            rows = []
        if not rows:
            try:
                rows = (await self._get("/library/onDeck", includeGuids=1)).get("Metadata") or []
            except (httpx.HTTPError, PermissionError):
                rows = []
        out: list[tuple[CatalogItem, int]] = []
        for r in rows[:limit]:
            it = self.map_meta(r)
            if it:
                out.append((it, int(r.get("viewOffset") or 0)))
        return out

    async def stream_for_item(self, item: CatalogItem) -> StreamTarget:
        part_key = item.extra.get("part_key")
        if not part_key:
            item = await self.item_detail(item)
            part_key = item.extra.get("part_key")
        if not part_key:
            raise LookupError("no playable part")
        url = f"{self.server}{part_key}?{urlencode({'X-Plex-Token': self.token})}"
        container = str(item.extra.get("container") or "mp4")
        return StreamTarget(
            url=url,
            mime=_MIME.get(container, "video/mp4"),
            duration_ms=item.duration_ms,
            resume_position_ms=int(item.extra.get("view_offset") or 0),
            title=item.title,
            subtitle=item.extra.get("grandparent_title"),
            poster=item.poster,
        )

    def image_headers(self, url: str) -> dict[str, str] | None:
        """ImageAuthProvider: headers needed to fetch one of our image URLs (None = not ours)."""
        if not url.startswith(self.server + "/"):
            return None
        return {"X-Plex-Token": self.token, "Accept": "image/*"}

    async def report_progress(self, item: CatalogItem, position_ms: int, duration_ms: int | None, state: str) -> None:
        params = {
            "ratingKey": item.ext_id,
            "key": f"/library/metadata/{item.ext_id}",
            "state": state,
            "time": position_ms,
            "duration": duration_ms or item.duration_ms or 0,
            "identifier": "com.plexapp.plugins.library",
        }
        async with self._ctx.http_factory(headers=self._h()) as client:
            await client.get(f"{self.server}/:/timeline", params=params)


def factory(ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> PlexSource:
    return PlexSource(ref, config, ctx)
