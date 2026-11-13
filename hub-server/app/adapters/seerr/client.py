"""Seerr-compatible request manager client.

- GET  /api/v1/status         reachability + version (unauthenticated upstream)
- GET  /api/v1/auth/me        proves the API key is accepted
- POST /api/v1/request        create a movie/tv request
- GET  /api/v1/request/{id}   request + nested media status and download queue
- GET  /api/v1/movie/{tmdbId} | /api/v1/tv/{tmdbId}   the same media status as ``mediaInfo``

StreamHub owns approval; the manager only executes, so requests are always created
with the admin API key and never on behalf of a manager-side user.

Overseerr, Jellyseerr and Seerr differ in which fields they send, so nothing read from a
status answer is assumed to exist: a missing or malformed piece is ``None`` (or an empty
queue), and what that means is decided by ``app.services.request_progress``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Literal

import httpx

from app.infra.logging import redact_text

# Request status. FAILED and COMPLETED exist only in newer versions.
REQUEST_PENDING, REQUEST_APPROVED, REQUEST_DECLINED, REQUEST_FAILED, REQUEST_COMPLETED = 1, 2, 3, 4, 5
# Media status, per quality (``status`` / ``status4k``) and per season.
MEDIA_UNKNOWN, MEDIA_PENDING, MEDIA_PROCESSING, MEDIA_PARTIALLY_AVAILABLE, MEDIA_AVAILABLE = 1, 2, 3, 4, 5

# .NET TimeSpan as the download clients report it: [d.]hh:mm:ss[.fraction]
_TIME_LEFT_RE = re.compile(r"^(?:(\d{1,4})\.)?(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?$")


@dataclass(frozen=True)
class QueueItem:
    """One entry of the manager's download tracker. The entry's title is never kept: it is a release name."""

    status: str  # the download client's word, lower-cased; "" when absent
    size: int | None  # bytes
    size_left: int | None
    eta: datetime | None
    time_left_s: int | None
    season: int | None
    episode: int | None


@dataclass(frozen=True)
class ManagerSnapshot:
    """What the manager said about one request, for the quality the request is for."""

    request_status: int | None = None
    media_status: int | None = None
    queue: tuple[QueueItem, ...] = ()
    seasons_requested: tuple[int, ...] = ()
    season_status: Mapping[int, int] = field(default_factory=dict)
    # False when the answer carried no media record at all.
    tracked: bool = False


def _int(value: Any) -> int | None:
    """A non-negative whole number, also when JSON carried it as a float."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not (math.isfinite(value) and value.is_integer()):
        return None
    return int(value) if value >= 0 else None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _time_left(value: Any) -> int | None:
    match = _TIME_LEFT_RE.match(value) if isinstance(value, str) else None
    if match is None:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _queue(value: Any) -> tuple[QueueItem, ...]:
    items = []
    for raw in value if isinstance(value, list) else ():
        if not isinstance(raw, dict):
            continue
        size, left = _int(raw.get("size")), _int(raw.get("sizeLeft"))
        if size is None or left is None or left > size:
            left = None
        episode = raw.get("episode")
        episode = episode if isinstance(episode, dict) else {}
        status = raw.get("status")
        items.append(
            QueueItem(
                status=status.strip().lower()[:40] if isinstance(status, str) else "",
                size=size,
                size_left=left,
                eta=_when(raw.get("estimatedCompletionTime")),
                time_left_s=_time_left(raw.get("timeLeft")),
                season=_int(episode.get("seasonNumber")),
                episode=_int(episode.get("episodeNumber")),
            )
        )
    return tuple(items)


def _seasons(value: Any, key: str) -> dict[int, int]:
    out: dict[int, int] = {}
    for raw in value if isinstance(value, list) else ():
        number = _int(raw.get("seasonNumber")) if isinstance(raw, dict) else None
        status = _int(raw.get(key)) if isinstance(raw, dict) else None
        if number is not None and status is not None:
            out[number] = status
    return out


def _snapshot(request: dict[str, Any] | None, media: Any, *, is_4k: bool | None) -> ManagerSnapshot:
    """``is_4k`` None: no request to say which quality, so the copy that is further along answers."""
    listed = request.get("seasons") if request else None
    numbers = {_int(s.get("seasonNumber")) for s in (listed if isinstance(listed, list) else ()) if isinstance(s, dict)}
    requested = tuple(sorted(n for n in numbers if n is not None))
    request_status = _int(request.get("status")) if request else None
    if not isinstance(media, dict):
        return ManagerSnapshot(request_status=request_status, seasons_requested=requested)
    if is_4k is None:
        is_4k = (_int(media.get("status4k")) or 0) > (_int(media.get("status")) or 0)
    suffix = "4k" if is_4k else ""
    return ManagerSnapshot(
        request_status=request_status,
        media_status=_int(media.get("status" + suffix)),
        queue=_queue(media.get("downloadStatus" + suffix)),
        seasons_requested=requested,
        season_status=_seasons(media.get("seasons"), "status" + suffix),
        tracked=True,
    )


class SeerrError(Exception):
    """``status`` is the upstream HTTP status, or ``None`` when no response arrived."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _existing_request_id(body: Any) -> int | None:
    if not isinstance(body, dict):
        return None
    nested = body.get("request")
    for candidate in (body.get("id"), body.get("requestId"), nested.get("id") if isinstance(nested, dict) else None):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


class SeerrClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._key = api_key

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            # Never follow redirects: the API key header would travel to the new origin.
            return await self._http.request(
                method,
                f"{self._base}/api/v1{path}",
                headers={"X-Api-Key": self._key, "Accept": "application/json"},
                follow_redirects=False,
                **kw,
            )
        except httpx.HTTPError as exc:
            raise SeerrError(f"Could not reach the request manager ({type(exc).__name__}).") from exc

    @staticmethod
    def _json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError as exc:
            raise SeerrError("The request manager returned an unreadable response.", resp.status_code) from exc

    def _failure(self, resp: httpx.Response) -> SeerrError:
        detail = ""
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("message"), str):
            detail = redact_text(body["message"]).replace(self._key, "***")[:200]
        if resp.status_code in (401, 403):
            return SeerrError("The request manager rejected the API key.", resp.status_code)
        message = f"The request manager answered {resp.status_code}"
        return SeerrError(f"{message}: {detail}" if detail else f"{message}.", resp.status_code)

    async def test(self) -> tuple[bool, str]:
        try:
            resp = await self._call("GET", "/status")
            if resp.status_code != 200:
                raise self._failure(resp)
            body = self._json(resp)
            if not isinstance(body, dict) or not isinstance(body.get("version"), str):
                raise SeerrError("That address did not answer like a request manager.", resp.status_code)
            # /status is public upstream, so it cannot prove the key works.
            me = await self._call("GET", "/auth/me")
            if me.status_code != 200:
                raise self._failure(me)
        except SeerrError as exc:
            return False, exc.message
        return True, f"Connected to request manager {body['version'][:40]}."

    async def create_request(
        self,
        media_type: Literal["movie", "tv"],
        tmdb_id: int,
        seasons: list[int] | Literal["all"] | None = None,
    ) -> int:
        payload: dict[str, Any] = {"mediaType": media_type, "mediaId": tmdb_id}
        if media_type == "tv":
            payload["seasons"] = seasons or "all"
        resp = await self._call("POST", "/request", json=payload)
        if resp.status_code == 409:
            try:
                existing = _existing_request_id(resp.json())
            except ValueError:
                existing = None
            if existing is not None:
                return existing
        if resp.status_code not in (200, 201):
            raise self._failure(resp)
        body = self._json(resp)
        created = body.get("id") if isinstance(body, dict) else None
        if not isinstance(created, int) or isinstance(created, bool):
            raise SeerrError("The request manager did not return a request id.", resp.status_code)
        return created

    async def _status_body(self, path: str) -> dict[str, Any]:
        resp = await self._call("GET", path)
        if resp.status_code != 200:
            raise self._failure(resp)
        body = self._json(resp)
        if not isinstance(body, dict):
            raise SeerrError("The request manager returned an unreadable response.", resp.status_code)
        return body

    async def request_snapshot(self, request_id: int) -> ManagerSnapshot:
        body = await self._status_body(f"/request/{int(request_id)}")
        return _snapshot(body, body.get("media"), is_4k=body.get("is4k") is True)

    async def title_snapshot(self, media_type: Literal["movie", "tv"], tmdb_id: int) -> ManagerSnapshot:
        """For a request the manager already had and gave no id for: its media record, by title."""
        body = await self._status_body(f"/{'tv' if media_type == 'tv' else 'movie'}/{int(tmdb_id)}")
        return _snapshot(None, body.get("mediaInfo"), is_4k=None)


class SeerrConnection(AbstractAsyncContextManager[SeerrClient]):
    """Opens an HTTP client for one burst of manager calls and closes it afterwards."""

    def __init__(self, http_factory: Callable[..., httpx.AsyncClient], base_url: str, api_key: str) -> None:
        self._http_factory = http_factory
        self._base_url = base_url
        self._api_key = api_key
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> SeerrClient:
        self._http = self._http_factory()
        return SeerrClient(self._http, self._base_url, self._api_key)

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
