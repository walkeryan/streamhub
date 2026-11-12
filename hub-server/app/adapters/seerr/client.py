"""Seerr-compatible request manager client.

- GET  /api/v1/status         reachability + version (unauthenticated upstream)
- GET  /api/v1/auth/me        proves the API key is accepted
- POST /api/v1/request        create a movie/tv request
- GET  /api/v1/request/{id}   request + nested media status

StreamHub owns approval; the manager only executes, so requests are always created
with the admin API key and never on behalf of a manager-side user.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any, Literal

import httpx

from app.infra.logging import redact_text

ManagerStatus = Literal["pending", "approved", "declined", "available", "failed", "unknown"]

REQUEST_PENDING, REQUEST_APPROVED, REQUEST_DECLINED, REQUEST_FAILED, REQUEST_COMPLETED = 1, 2, 3, 4, 5
MEDIA_AVAILABLE = 5


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


def map_status(body: dict[str, Any]) -> ManagerStatus:
    request = body.get("status")
    media = body.get("media")
    media_status = media.get("status") if isinstance(media, dict) else None
    if request == REQUEST_DECLINED:
        return "declined"
    if media_status == MEDIA_AVAILABLE or request == REQUEST_COMPLETED:
        return "available"
    if request == REQUEST_FAILED:
        return "failed"
    if request == REQUEST_PENDING:
        return "pending"
    if request == REQUEST_APPROVED:
        return "approved"
    return "unknown"


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

    async def request_status(self, request_id: int) -> ManagerStatus:
        resp = await self._call("GET", f"/request/{int(request_id)}")
        if resp.status_code != 200:
            raise self._failure(resp)
        body = self._json(resp)
        if not isinstance(body, dict):
            raise SeerrError("The request manager returned an unreadable response.", resp.status_code)
        return map_status(body)


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
