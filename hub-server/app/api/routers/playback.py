"""/playback/resolve, /playback/progress, /stream/{token}[/index.m3u8], images proxy."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from app.api import schemas as S
from app.api.deps import C, P
from app.domain.ports import ImageAuthProvider
from app.services import ids
from app.services.context import is_https
from app.services.errors import BadGateway, NotFound, Unauthorized

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/playback/resolve", response_model=S.PlaybackDescriptor, tags=["playback"])
async def resolve(body: S.PlaybackRequest, c: C, p: P) -> dict[str, Any]:
    return await c.playback.resolve(
        p.profile_id,
        body.id,
        source_id=body.source_id,
        client=body.client,
        catchup_programme_id=body.catchup_programme_id,
        https_site=is_https.get(),
    )


@router.post("/playback/progress", status_code=204, tags=["playback"])
async def progress(body: S.ProgressReport, c: C, p: P) -> Response:
    ref_id = body.id
    source_id = body.source_id
    forward = None
    aliases: list[str] = []
    try:
        h = ids.parse(ref_id)
    except ValueError as exc:
        raise NotFound("unknown id") from exc
    if h.scheme == "lib":
        row = await c.catalog.title_row(h.a, h.b)
        if row is not None:
            source_id = source_id or row.source_id
            forward = row
            hub = c.catalog.item_hub_id(row)
            if hub != ref_id:
                aliases.append(hub)
    elif h.scheme == "tmdb" and h.b.isdigit():
        rows = await c.catalog.rows_for_tmdb(h.a, int(h.b))
        pick = next((r for r in rows if r.source_id == source_id), rows[0] if rows else None)
        if pick is not None:
            source_id = source_id or pick.source_id
            forward = pick
            aliases.append(ids.lib_id(pick.source_id, pick.ext_id))
    elif h.scheme == "ch":
        source_id = source_id or h.a
    await c.progress.report(
        p.profile_id,
        ref_id,
        position_ms=body.position_ms,
        duration_ms=body.duration_ms,
        state=body.state,
        source_id=source_id,
        forward_item=forward,
        aliases=aliases,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------- streaming
def _grant(c: C, token: str) -> Any:
    g = c.tokens.verify(token)
    if g is None:
        raise Unauthorized("invalid or expired stream token")
    return g


def _proxy_url(token: str, upstream: str, c: C) -> str:
    sub = c.crypto.encrypt_json({"u": upstream})
    return f"/api/v1/stream/{token}/seg/{sub}"


@router.get("/stream/{token}", tags=["playback"], response_class=StreamingResponse)
async def stream(token: str, request: Request, c: C) -> Response:
    g = _grant(c, token)
    if g.mode == "hls":
        return Response(status_code=307, headers={"Location": f"/api/v1/stream/{token}/index.m3u8"})
    is_hls_upstream = ".m3u8" in g.url.split("?", 1)[0].lower()
    try:
        if is_hls_upstream:
            final, text = await c.proxy.fetch_text(g.url, g.headers)
            body = c.proxy.rewrite_playlist(text, final, lambda u: _proxy_url(token, u, c))
            return Response(body, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})
        if g.live and g.channel_key:
            ctype, gen = await c.proxy.live_shared(g.channel_key, g.url, g.headers, g.source_id)
            return StreamingResponse(gen, media_type=ctype, headers={"Cache-Control": "no-cache"})
        status, headers, gen = await c.proxy.passthrough(
            g.url, g.headers, g.source_id, request.headers.get("range"), live=g.live
        )
    except (ConnectionError, OSError) as exc:
        raise BadGateway("upstream unavailable") from exc
    except Exception as exc:
        log.info("stream proxy failed", extra={"err": type(exc).__name__})
        raise BadGateway("upstream unavailable") from exc
    return StreamingResponse(
        gen, status_code=status, headers=headers, media_type=headers.get("content-type", "application/octet-stream")
    )


@router.get("/stream/{token}/seg/{sub}", tags=["playback"], include_in_schema=False)
async def stream_segment(token: str, sub: str, request: Request, c: C) -> Response:
    g = _grant(c, token)
    d = c.crypto.try_decrypt_json(sub, ttl=c.tokens.ttl_s)
    if not isinstance(d, dict) or "u" not in d:
        raise Unauthorized("invalid segment token")
    url = str(d["u"])
    try:
        if ".m3u8" in url.split("?", 1)[0].lower():
            final, text = await c.proxy.fetch_text(url, g.headers)
            body = c.proxy.rewrite_playlist(text, final, lambda u: _proxy_url(token, u, c))
            return Response(body, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})
        status, headers, gen = await c.proxy.passthrough(url, g.headers, g.source_id, request.headers.get("range"))
    except (ConnectionError, OSError) as exc:
        raise BadGateway("upstream unavailable") from exc
    return StreamingResponse(
        gen, status_code=status, headers=headers, media_type=headers.get("content-type", "video/mp2t")
    )


@router.get("/stream/{token}/index.m3u8", tags=["playback"])
async def stream_hls(token: str, c: C) -> Response:
    g = _grant(c, token)
    transcode = str(c.app_settings.get("transcode_hw") or "none") if g.transcode else "none"
    if g.transcode and transcode == "none":
        transcode = "software"
    key = f"{g.source_id}:{g.url}:{transcode}"
    try:
        sess = await c.proxy.hls_session(key, g.url, g.headers, live=g.live, transcode=transcode)
    except (ConnectionError, TimeoutError, OSError) as exc:
        raise BadGateway("could not start HLS session") from exc
    text = (sess.workdir / "index.m3u8").read_text(errors="ignore")
    base = f"/api/v1/stream/{token}/hls/{sess.sid}/"
    lines = [base + ln if ln and not ln.startswith("#") else ln for ln in text.splitlines()]
    return Response(
        "\n".join(lines) + "\n", media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"}
    )


@router.get("/stream/{token}/hls/{sid}/{name}", tags=["playback"], include_in_schema=False)
async def stream_hls_segment(token: str, sid: str, name: str, c: C) -> Response:
    _grant(c, token)
    sess = c.proxy.hls_by_sid(sid)
    if sess is None or "/" in name or ".." in name or not name.endswith((".ts", ".m4s", ".mp4", ".m3u8")):
        raise NotFound("segment not found")
    path: Path = sess.workdir / name
    if not path.is_file():
        raise NotFound("segment not found")
    return FileResponse(
        path,
        media_type="video/mp2t" if name.endswith(".ts") else "application/octet-stream",
        headers={"Cache-Control": "max-age=60"},
    )


@router.get("/images/{token}", tags=["catalog"], include_in_schema=False)
async def image_proxy(token: str, c: C) -> Response:
    """Artwork from authenticated library servers (Plex) without exposing tokens to clients."""
    d = c.crypto.verify_json(token)
    if not isinstance(d, dict):
        raise NotFound("image not found")
    try:
        _src, prov = await c.sources.provider_for(str(d.get("s")))
    except NotFound:
        raise
    url = str(d.get("u") or "")
    if not isinstance(prov, ImageAuthProvider):
        raise NotFound("image not found")
    headers = prov.image_headers(url)
    if headers is None:
        raise NotFound("image not found")
    try:
        async with c.http_factory(timeout=20.0, headers=headers) as client:
            resp = await client.get(url)
    except Exception as exc:
        raise BadGateway("image unavailable") from exc
    if resp.status_code >= 400:
        raise NotFound("image not found")
    return Response(
        resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )
