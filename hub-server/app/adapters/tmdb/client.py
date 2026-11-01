"""TMDB adapter (implements the MetadataProvider port).

- search/multi, movie|tv details (append images, credits, external_ids)
- watch/providers per region (JustWatch data)
- trending/all/week for the hero when the user has no sources
- find/{imdb_id}
- responses cached in the SQLite cache table with TTL; 429 handled with backoff
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.domain.entities import MetaTitle, ProviderOffer
from app.domain.ports import AdapterContext
from app.infra.http import request_with_retry
from app.services.classification import tmdb_age_evidence

log = logging.getLogger(__name__)

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
TMDB_ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."
JUSTWATCH_ATTRIBUTION = "Availability data by JustWatch"

TTL_DETAILS = 3 * 24 * 3600
TTL_SEARCH = 6 * 3600
TTL_PROVIDERS = 24 * 3600
TTL_TRENDING = 6 * 3600


def image_url(path: str | None, size: str = "w500") -> str | None:
    if not path:
        return None
    if path.startswith("http"):
        return path
    return f"{IMG}/{size}{path}"


def _year(date: str | None) -> int | None:
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def to_meta(d: dict[str, Any], media_type: str | None = None) -> MetaTitle | None:
    mt = media_type or d.get("media_type")
    if mt not in ("movie", "tv"):
        return None
    title = d.get("title") or d.get("name") or d.get("original_title") or d.get("original_name") or ""
    genres = [g["name"] for g in d.get("genres") or [] if isinstance(g, dict) and g.get("name")]
    runtime = d.get("runtime")
    if not runtime and d.get("episode_run_time"):
        runtime = (d.get("episode_run_time") or [None])[0]
    ext = d.get("external_ids") or {}
    cast = []
    for c in ((d.get("credits") or {}).get("cast") or [])[:20]:
        cast.append(
            {
                "name": c.get("name") or "",
                "character": c.get("character") or "",
                "photo": image_url(c.get("profile_path"), "w185"),
            }
        )
    logo = None
    logos = (d.get("images") or {}).get("logos") or []
    if logos:
        en = [x for x in logos if x.get("iso_639_1") in ("en", None)] or logos
        logo = image_url(en[0].get("file_path"), "w500")
    return MetaTitle(
        tmdb_id=int(d["id"]),
        media_type=mt,
        title=title,
        year=_year(d.get("release_date") or d.get("first_air_date")),
        overview=d.get("overview") or None,
        poster=image_url(d.get("poster_path"), "w342"),
        backdrop=image_url(d.get("backdrop_path"), "w1280"),
        rating=d.get("vote_average"),
        age_evidence=tmdb_age_evidence(d, mt),
        genres=genres,
        runtime_min=runtime,
        imdb_id=ext.get("imdb_id") or d.get("imdb_id"),
        cast=cast,
        logo=logo,
        popularity=float(d.get("popularity") or 0.0),
    )


def _meta_to_json(m: MetaTitle) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(m)


def _meta_from_json(d: dict[str, Any]) -> MetaTitle:
    return MetaTitle(**d)


class TmdbClient:
    def __init__(
        self,
        ctx: AdapterContext,
        key_getter: Callable[[], str | None],
        evidence_writer: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._ctx = ctx
        self._key_getter = key_getter
        self._evidence_writer = evidence_writer

    @property
    def enabled(self) -> bool:
        return bool(self._key_getter())

    def image_url(self, path: str | None, size: str = "w500") -> str | None:
        return image_url(path, size)

    async def _get(self, path: str, ttl: int, **params: Any) -> dict[str, Any] | None:
        key = self._key_getter()
        if not key:
            return None
        cache_key = "tmdb:" + path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = await self._ctx.cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
        headers: dict[str, str] = {"Accept": "application/json"}
        q = dict(params)
        if len(key) > 40:  # v4 read access token
            headers["Authorization"] = f"Bearer {key}"
        else:
            q["api_key"] = key
        try:
            async with self._ctx.http_factory(timeout=15.0, headers=headers) as client:
                resp = await request_with_retry(client, "GET", f"{API}{path}", params=q, retries=4)
        except httpx.HTTPError as exc:
            log.warning("tmdb request failed", extra={"path": path, "err": type(exc).__name__})
            return None
        if resp.status_code == 404:
            await self._ctx.cache.set(cache_key, {}, ttl)
            return {}
        if resp.status_code >= 400:
            log.warning("tmdb error", extra={"path": path, "status": resp.status_code})
            return None
        data = resp.json()
        if isinstance(data, dict):
            data["_streamhub_retrieved_at"] = datetime.now(UTC).isoformat()
            await self._ctx.cache.set(cache_key, data, ttl)
            return data
        return None

    async def search(self, query: str, limit: int = 20) -> list[MetaTitle]:
        data = await self._get("/search/multi", TTL_SEARCH, query=query, include_adult="false")
        if data is None:
            raise RuntimeError("Metadata search unavailable")
        out: list[MetaTitle] = []
        for r in data.get("results") or []:
            m = to_meta(r)
            if m:
                out.append(m)
            if len(out) >= limit:
                break
        return out

    async def discover(
        self, media_type: str, page: int = 1, sort: str = "popular", year: int | None = None
    ) -> tuple[list[MetaTitle], int]:
        if media_type not in ("movie", "tv") or sort not in ("popular", "rating", "newest"):
            raise ValueError("Unsupported discovery type or sort")
        date_field = "primary_release_date" if media_type == "movie" else "first_air_date"
        order = {"popular": "popularity.desc", "rating": "vote_average.desc", "newest": f"{date_field}.desc"}[sort]
        params: dict[str, Any] = {"page": page, "sort_by": order, "include_adult": "false"}
        if sort == "rating":
            params["vote_count.gte"] = 100
        if sort == "newest":
            params[f"{date_field}.lte"] = datetime.now(UTC).date().isoformat()
        if year is not None:
            params["primary_release_year" if media_type == "movie" else "first_air_date_year"] = year
        data = await self._get(f"/discover/{media_type}", TTL_TRENDING, **params)
        if data is None:
            raise RuntimeError("Metadata discovery unavailable")
        metas = [meta for row in data.get("results", []) if (meta := to_meta(row, media_type))]
        return metas[:20], max(0, int(data.get("total_results") or 0))

    async def _record_age_evidence(self, subject: str, evidence: dict[str, Any]) -> None:
        if self._evidence_writer is None:
            return
        try:
            await self._evidence_writer(subject, evidence)
        except Exception as exc:
            # Evidence ingestion must not turn an otherwise working metadata or
            # playback request into an outage. Missing durable evidence is unknown.
            log.warning("classification persistence unavailable", extra={"error_type": type(exc).__name__})

    async def details(self, media_type: str, tmdb_id: int) -> MetaTitle | None:
        if media_type not in ("movie", "tv"):
            return None
        data = await self._get(
            f"/{media_type}/{tmdb_id}",
            TTL_DETAILS,
            append_to_response="images,credits,external_ids,"
            + ("release_dates" if media_type == "movie" else "content_ratings"),
            include_image_language="en,null",
        )
        if not data:
            if self._evidence_writer:
                evidence = tmdb_age_evidence({"id": tmdb_id}, media_type)
                evidence["reason"] = "fetch_unavailable"
                await self._record_age_evidence(f"tmdb:{media_type}:{tmdb_id}", evidence)
            return None
        meta = to_meta(data, media_type)
        if meta and meta.tmdb_id == tmdb_id and self._evidence_writer:
            await self._record_age_evidence(f"tmdb:{media_type}:{tmdb_id}", meta.age_evidence)
        return meta

    async def trailer_key(self, media_type: str, tmdb_id: int) -> str | None:
        if media_type not in ("movie", "tv"):
            return None
        data = await self._get(f"/{media_type}/{tmdb_id}/videos", TTL_DETAILS)
        vids = [
            v
            for v in (data or {}).get("results") or []
            if isinstance(v, dict) and v.get("type") == "Trailer" and v.get("site") == "YouTube" and v.get("key")
        ]
        if not vids:
            return None
        vids.sort(key=lambda v: (not v.get("official"), (v.get("iso_639_1") or "") != "en"))
        return str(vids[0]["key"])

    async def season(self, tmdb_id: int, season: int) -> dict[str, Any] | None:
        return await self._get(f"/tv/{tmdb_id}/season/{season}", TTL_DETAILS)

    async def watch_providers(self, media_type: str, tmdb_id: int, region: str) -> list[ProviderOffer]:
        data = await self._get(f"/{media_type}/{tmdb_id}/watch/providers", TTL_PROVIDERS)
        reg = ((data or {}).get("results") or {}).get(region.upper()) or {}
        out: list[ProviderOffer] = []
        for kind in ("flatrate", "free", "ads", "rent", "buy"):
            for p in reg.get(kind) or []:
                out.append(
                    ProviderOffer(
                        provider_id=int(p["provider_id"]),
                        provider_name=p.get("provider_name") or "",
                        kind=kind,
                        logo_path=image_url(p.get("logo_path"), "w92"),
                    )
                )
        return out

    async def trending(self, limit: int = 20) -> list[MetaTitle]:
        data = await self._get("/trending/all/week", TTL_TRENDING)
        out = [m for r in (data or {}).get("results") or [] if (m := to_meta(r))]
        return out[:limit]

    async def find_by_imdb(self, imdb_id: str) -> MetaTitle | None:
        data = await self._get(f"/find/{imdb_id}", TTL_DETAILS, external_source="imdb_id")
        if not data:
            return None
        for key, mt in (("movie_results", "movie"), ("tv_results", "tv")):
            for r in data.get(key) or []:
                m = to_meta(r, mt)
                if m:
                    return m
        return None
