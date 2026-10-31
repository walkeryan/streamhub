"""Hand-off deep-link registry.

Seed rows are parsed from docs/DEEPLINKS.md (bundled copy in app/resources).
Netflix / Disney+ title ids are resolved through Wikidata SPARQL from TMDB ids
(P4947 = TMDB movie id, P4983 = TMDB TV id → P1874 Netflix id, P13902 Disney+ id)
and cached. When no id is known we fall back to the service's web search URL.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

from app.domain.entities import ServiceLink
from app.domain.ports import AdapterContext

log = logging.getLogger(__name__)

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_TTL = 14 * 24 * 3600
WIKIDATA_MISS_TTL = 2 * 24 * 3600

# service key → (template placeholder, Wikidata property holding the service's title id)
WIKIDATA_SERVICE_IDS: dict[str, tuple[str, str]] = {
    "netflix": ("netflix_id", "P1874"),
    "disney": ("id", "P13902"),
}

# Fallback search pages per service (used when no title id is known).
WEB_SEARCH_URLS: dict[str, str] = {
    "netflix": "https://www.netflix.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "hulu": "https://www.hulu.com/search?q={q}",
    "prime": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
    "disney": "https://www.disneyplus.com/search?q={q}",
    "max": "https://play.max.com/search?q={q}",
    "peacock": "https://www.peacocktv.com/search?q={q}",
    "paramount": "https://www.paramountplus.com/search/?q={q}",
    "appletv": "https://tv.apple.com/search?term={q}",
    "tubi": "https://tubitv.com/search/{q}",
    "pluto": "https://pluto.tv/search/details?query={q}",
}


@dataclass(slots=True)
class ServiceRow:
    key: str
    name: str
    tmdb_provider_ids: list[int]
    android_package: str | None
    android_component: str | None
    android_title_uri: str | None
    web_title_url: str | None
    web_search_url: str | None
    supports_title_link: str  # true|false|partial|unverified
    notes: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "tmdb_provider_ids": self.tmdb_provider_ids,
            "android_package": self.android_package,
            "android_component": self.android_component,
            "android_title_uri": self.android_title_uri,
            "web_title_url": self.web_title_url,
            "web_search_url": self.web_search_url,
            "supports_title_link": self.supports_title_link,
            "notes": self.notes,
        }


def _clean(cell: str) -> str | None:
    c = cell.strip()
    return None if c in ("", "—", "-", "–") else c


def parse_deeplinks_md(text: str) -> list[ServiceRow]:
    rows: list[ServiceRow] = []
    header: list[str] | None = None
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        d = dict(zip(header, cells, strict=False))
        key = _clean(d.get("key", ""))
        if not key:
            continue
        uri = _clean(d.get("android_title_uri", ""))
        component = None
        if uri:
            m = re.search(r"\(component\s+([^)]+)\)", uri)
            if m:
                component = m.group(1).strip()
                uri = uri[: m.start()].strip()
        ids = [int(x) for x in re.findall(r"\d+", d.get("tmdb_provider_ids", ""))]
        rows.append(
            ServiceRow(
                key=key,
                name=_clean(d.get("name", "")) or key,
                tmdb_provider_ids=ids,
                android_package=_clean(d.get("android_package", "")),
                android_component=component,
                android_title_uri=uri,
                web_title_url=_clean(d.get("web_title_url", "")),
                web_search_url=WEB_SEARCH_URLS.get(key),
                supports_title_link=(_clean(d.get("supports_title_link", "")) or "false").lower(),
                notes=_clean(d.get("notes", "")),
            )
        )
    return rows


def load_seed() -> list[ServiceRow]:
    path = Path(__file__).resolve().parents[2] / "resources" / "DEEPLINKS.md"
    return parse_deeplinks_md(path.read_text(encoding="utf-8"))


def _placeholders(tpl: str | None) -> set[str]:
    return set(re.findall(r"\{([a-z_]+)\}", tpl or ""))


class WikidataResolver:
    def __init__(self, ctx: AdapterContext) -> None:
        self._ctx = ctx

    async def resolve(self, prop: str, tmdb_type: str, tmdb_id: int) -> str | None:
        tmdb_prop = "P4947" if tmdb_type == "movie" else "P4983"
        cache_key = f"wikidata:{prop}:{tmdb_type}:{tmdb_id}"
        cached = await self._ctx.cache.get(cache_key)
        if isinstance(cached, dict):
            v = cached.get("v")
            return str(v) if v else None
        query = f'SELECT ?v WHERE {{ ?item wdt:{tmdb_prop} "{int(tmdb_id)}" . ?item wdt:{prop} ?v . }} LIMIT 1'
        value: str | None = None
        try:
            async with self._ctx.http_factory(
                timeout=15.0, headers={"Accept": "application/sparql-results+json"}
            ) as client:
                resp = await client.get(WIKIDATA_SPARQL, params={"query": query, "format": "json"})
            if resp.status_code == 200:
                bindings = resp.json().get("results", {}).get("bindings", [])
                if bindings:
                    value = bindings[0].get("v", {}).get("value")
            else:
                log.info("wikidata non-200", extra={"status": resp.status_code})
                return None  # do not cache transient failures
        except Exception as exc:
            log.info("wikidata lookup failed", extra={"err": type(exc).__name__})
            return None
        await self._ctx.cache.set(cache_key, {"v": value}, WIKIDATA_TTL if value else WIKIDATA_MISS_TTL)
        return value


RowLoader = Callable[[str], Awaitable[ServiceRow | None]]


class DeeplinkRegistry:
    """Implements the DeeplinkProvider port."""

    def __init__(self, ctx: AdapterContext, row_loader: RowLoader) -> None:
        self._row_loader = row_loader
        self._wikidata = WikidataResolver(ctx)

    async def link_for(
        self,
        service_key: str,
        *,
        tmdb_type: str | None,
        tmdb_id: int | None,
        title: str,
    ) -> ServiceLink | None:
        row = await self._row_loader(service_key)
        if row is None:
            return None
        values: dict[str, str] = {}
        needed = _placeholders(row.android_title_uri) | _placeholders(row.web_title_url)
        wd = WIKIDATA_SERVICE_IDS.get(row.key)
        if wd and wd[0] in needed and tmdb_type in ("movie", "tv") and tmdb_id:
            v = await self._wikidata.resolve(wd[1], tmdb_type, tmdb_id)
            if v:
                values[wd[0]] = v

        def fill(tpl: str | None) -> str | None:
            if not tpl:
                return None
            phs = _placeholders(tpl)
            if any(p not in values for p in phs):
                return None
            out = tpl
            for p in phs:
                out = out.replace("{" + p + "}", quote(values[p], safe=""))
            return out

        android_uri = fill(row.android_title_uri)
        web_url = fill(row.web_title_url)
        has_title_link = web_url is not None and bool(_placeholders(row.web_title_url))
        if web_url is None:
            search = row.web_search_url or WEB_SEARCH_URLS.get(row.key)
            web_url = search.replace("{q}", quote_plus(title)) if search else (row.web_title_url or "")
        # For hand-off without a deep link, android_uri stays None → client uses leanback launch intent.
        extras: dict[str, str] = {}
        if row.android_component:
            extras["component"] = row.android_component
        if row.key == "netflix" and android_uri:
            extras["source"] = "30"
        return ServiceLink(
            service_key=row.key,
            name=row.name,
            android_package=row.android_package,
            android_uri=android_uri,
            web_url=web_url,
            supports_title_link=has_title_link and row.supports_title_link in ("true", "partial"),
            android_extras=extras,
        )
