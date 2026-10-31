"""Shared outbound HTTP helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.infra.logging import redact_url

log = logging.getLogger(__name__)

DEFAULT_UA = "StreamHub/1.0 (+https://github.com/streamhub)"


def make_client(timeout: float = 20.0, **kw: Any) -> httpx.AsyncClient:
    headers = {"User-Agent": DEFAULT_UA}
    headers.update(kw.pop("headers", {}) or {})
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
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
        except (httpx.TransportError,) as exc:
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
            log.info(
                "http retryable status", extra={"url": redact_url(url), "status": resp.status_code}
            )
        attempt += 1
        await asyncio.sleep(delay)
