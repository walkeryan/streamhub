"""Shared outbound HTTP helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.infra.logging import redact_url

log = logging.getLogger(__name__)

DEFAULT_UA = "StreamHub/1.0 (+https://github.com/streamhub)"
REDIRECTS = (301, 302, 303, 307, 308)


def make_client(timeout: float | httpx.Timeout | None = 20.0, **kw: Any) -> httpx.AsyncClient:
    """Build the shared outbound client.

    ``timeout`` may be a number (seconds; ``None`` = no limit) or a ready-made ``httpx.Timeout``,
    which is used as-is. A plain number gets a 10 s connect timeout.
    """
    headers = {"User-Agent": DEFAULT_UA}
    headers.update(kw.pop("headers", {}) or {})
    to = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout, connect=10.0)
    return httpx.AsyncClient(
        timeout=to,
        follow_redirects=True,
        headers=headers,
        **kw,
    )


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff: float = 0.5,
    retry_statuses: tuple[int, ...] = (429, 502, 503, 504),
    **kw: Any,
) -> httpx.Response:
    """Request with exponential backoff; honours ``Retry-After`` on 429."""
    attempt = 0
    while True:
        try:
            resp = await client.request(method, url, **kw)
        except httpx.TransportError as exc:
            if attempt >= retries:
                raise
            delay = backoff * (2**attempt)
            log.warning("http transport error, retrying", extra={"url": redact_url(url), "err": str(exc)})
        else:
            if resp.status_code not in retry_statuses or attempt >= retries:
                return resp
            delay = backoff * (2**attempt)
            ra = resp.headers.get("Retry-After")
            if ra:
                try:
                    delay = max(delay, min(float(ra), 30.0))
                except ValueError:
                    pass
            log.info("http retryable status", extra={"url": redact_url(url), "status": resp.status_code})
        attempt += 1
        await asyncio.sleep(delay)


async def pin_upstream_session(http: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None) -> str:
    """The URL one viewer keeps using for a file: its same-origin redirect target, when there is one.

    Some panels answer the first request for a file with a redirect that carries a per-viewer
    session in its query and count every later request without it as another viewer. A player that
    asks for the original URL again on each seek (ffmpeg does) is then refused at the source's
    connection limit. One request, not followed, finds the target; ``Range: bytes=0-0`` keeps a
    server that answers directly from sending the file. One hop, same origin: a redirect elsewhere
    is ordinary and left to normal redirect handling. Anything else returns ``url`` unchanged.
    """
    resp: httpx.Response | None = None
    try:
        request = http.build_request("GET", url, headers={**(headers or {}), "Range": "bytes=0-0"})
        resp = await http.send(request, stream=True, follow_redirects=False)
        location = resp.headers.get("location")
        if resp.status_code in REDIRECTS and location:
            target = request.url.join(location)
            if (target.scheme, target.host, target.port) == (request.url.scheme, request.url.host, request.url.port):
                return str(target)
    except (httpx.HTTPError, httpx.InvalidURL, OSError) as exc:
        # Both URLs embed credentials; the target is never logged at all.
        log.info("upstream session not pinned", extra={"url": redact_url(url), "err": type(exc).__name__})
    finally:
        if resp is not None:
            await resp.aclose()
    return url
