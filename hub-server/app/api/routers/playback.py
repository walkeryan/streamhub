"""/playback/resolve, /playback/progress, /stream/{token}[/index.m3u8], images proxy."""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from app.api import schemas as S
from app.api.deps import Admin, C, P
from app.domain.ports import ImageAuthProvider
from app.proxy.stream_proxy import HlsSourceBusy
from app.services import ids
from app.services.context import absolute, is_https
from app.services.errors import BadGateway, Conflict, Forbidden, Gone, NotFound, ServiceError, Unauthorized

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/playback/resolve", response_model=S.PlaybackDescriptor, tags=["playback"])
async def resolve(body: S.PlaybackRequest, c: C, p: P) -> dict[str, Any]:
    c.proxy.ensure_running()
    if body.client == "web":
        if body.recovery_generation is None:
            raise Forbidden("Reload Streamhub to update playback recovery support.", code="playback_reload_required")
        if body.recovery_generation != c.proxy.recovery_epoch:
            raise Forbidden(
                "This playback attempt was stopped. Start playback again manually.", code="playback_stopped"
            )
    generation = c.proxy.generation
    result = await c.playback.resolve(
        p.profile_id,
        body.id,
        source_id=body.source_id,
        variant_id=body.variant_id,
        allow_unverified_audio=body.allow_unverified_audio,
        client="web_hls" if body.client == "web" and body.prefer_hls else body.client,
        catchup_programme_id=body.catchup_programme_id,
        rewind_at=body.rewind_at,
        https_site=is_https.get(),
    )
    c.proxy.ensure_running(generation)
    result["buffer_available"] = False
    if c.proxy.dvr.enabled and result.get("mode") == "hls" and result.get("live") and body.id.startswith("ch:"):
        capability = await c.playback.rewind_capability(p.profile_id, body.id)
        result["buffer_available"] = capability["mode"] == "unavailable"
    return result


@router.get("/playback/options", tags=["playback"])
async def playback_options(id: str, c: C, p: P, source_id: str | None = None) -> dict[str, Any]:
    return await c.playback.options(p.profile_id, id, source_id)


@router.get("/playback/rewind", response_model=S.RewindCapability, tags=["playback"])
async def rewind_capability(id: str, c: C, p: P) -> dict[str, Any]:
    return await c.playback.rewind_capability(p.profile_id, id)


async def _buffer_grant(token: str, c: C, p: P) -> tuple[Any, str, Any]:
    g = await _grant(c, token)
    if g.profile_id != p.profile_id or g.mode != "hls" or not g.live or not g.channel_key or g.transcode:
        raise Forbidden("DVR requires this profile's authorized live codec-copy HLS playback grant.")
    # This shared channel gate is also the integration point for current parental policy.
    channel = await c.playback._authorized_channel(p.profile_id, "ch:" + g.channel_key)
    if channel.source_id != g.source_id:
        raise Forbidden("DVR channel does not belong to this playback source.")
    source = await c.sources.get(g.source_id)
    return g, f"{g.source_id}:{g.url}:none", source


@router.post("/playback/buffer/lease", response_model=S.BufferLeaseResponse, tags=["playback"])
async def buffer_lease(body: S.BufferLeaseRequest, c: C, p: P) -> dict[str, Any]:
    g, key, source = await _buffer_grant(body.stream_token, c, p)
    try:
        return await c.proxy.buffer_lease(
            key,
            p.profile_id,
            g.url,
            g.headers,
            source_id=g.source_id,
            max_connections=c.sources.max_connections(source),
            generation=g.recovery_generation,
        )
    except HlsSourceBusy as exc:
        raise Conflict("Source connection limit is occupied.", code="source_busy") from exc


@router.post("/playback/buffer/heartbeat", response_model=S.BufferWindow, tags=["playback"])
async def buffer_heartbeat(body: S.BufferViewerRequest, c: C, p: P) -> dict[str, Any]:
    _, key, _ = await _buffer_grant(body.stream_token, c, p)
    c.proxy.dvr.heartbeat(key, p.profile_id, body.lease_id, body.paused)
    return await c.proxy.buffer_window(key)


@router.post("/playback/buffer/seek", response_model=S.BufferSeekResponse, tags=["playback"])
async def buffer_seek(body: S.BufferSeekRequest, c: C, p: P) -> dict[str, Any]:
    g, key, _ = await _buffer_grant(body.stream_token, c, p)
    if body.at is not None and g.content_ref:
        await c.content_policy.require(p.profile_id, g.content_ref, at=body.at)
    c.proxy.dvr.check(key, p.profile_id, body.lease_id)
    await c.proxy.buffer_manifest(key, body.at)  # Reject expired/gap positions before changing playback.
    query = {"lease_id": body.lease_id}
    if body.at is not None:
        query["at"] = body.at.isoformat()
    return {
        "url": absolute(f"/api/v1/stream/{body.stream_token}/index.m3u8?" + urlencode(query)),
        "window": await c.proxy.buffer_window(key),
    }


@router.post("/playback/buffer/release", status_code=204, tags=["playback"])
async def buffer_release(body: S.BufferViewerRequest, c: C, p: P) -> Response:
    _, key, _ = await _buffer_grant(body.stream_token, c, p)
    await c.proxy.buffer_release(key, p.profile_id, body.lease_id)
    return Response(status_code=204)


@router.get("/playback/generation", tags=["playback"])
async def playback_generation(c: C, p: P) -> dict[str, str]:
    c.proxy.ensure_running()
    return {"generation": c.proxy.recovery_epoch}


@router.get("/playback/recovery", tags=["playback"])
async def recovery_status(c: C, p: Admin) -> dict[str, Any]:
    return c.proxy.recovery_status()


@router.post("/playback/recovery/stop", tags=["playback"])
async def recovery_stop(body: S.StreamRecoveryConfirm, c: C, p: Admin) -> dict[str, Any]:
    async with c.proxy.recovery_lock:
        c.proxy.pause()
        c.tokens.revoke_all()
        # Persist before stopping: a restart must not silently resume playback.
        await c.app_settings.set_recovery_paused(True)
        return await c.proxy.stop_owned()


@router.post("/playback/recovery/resume", tags=["playback"])
async def recovery_resume(body: S.StreamRecoveryConfirm, c: C, p: Admin) -> dict[str, Any]:
    async with c.proxy.recovery_lock:
        if c.proxy.paused and c.proxy.recovery_status()["active_sessions"]:
            raise Conflict(
                "Some Streamhub sessions could not stop. Retry Stop all streams first.", code="recovery_incomplete"
            )
        await c.app_settings.set_recovery_paused(False)
        c.proxy.paused = False
        return c.proxy.recovery_status()


@router.post("/playback/progress", status_code=204, tags=["playback"])
async def progress(body: S.ProgressReport, c: C, p: P) -> Response:
    await c.content_policy.require(p.profile_id, body.id)
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
async def _grant(c: C, token: str) -> Any:
    c.proxy.ensure_running()
    g = c.tokens.verify(token)
    if g is None:
        raise Unauthorized("invalid or expired stream token")
    await c.content_policy.require_grant(g, c.auth)
    g.recovery_generation = c.proxy.generation
    return g


def _proxy_url(token: str, upstream: str, c: C) -> str:
    sub = c.crypto.encrypt_json({"u": upstream, "grant": hashlib.sha256(token.encode()).hexdigest()})
    return f"/api/v1/stream/{token}/seg/{sub}"


@router.get("/stream/{token}", tags=["playback"], response_class=StreamingResponse)
async def stream(token: str, request: Request, c: C) -> Response:
    g = await _grant(c, token)
    if g.mode == "hls":
        return Response(status_code=307, headers={"Location": f"/api/v1/stream/{token}/index.m3u8"})
    is_hls_upstream = ".m3u8" in g.url.split("?", 1)[0].lower()
    try:
        if is_hls_upstream:
            final, text = await c.proxy.fetch_text(g.url, g.headers, generation=g.recovery_generation)
            body = c.proxy.rewrite_playlist(text, final, lambda u: _proxy_url(token, u, c))
            return Response(body, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})
        if g.live and g.channel_key:
            ctype, gen = await c.proxy.live_shared(
                g.channel_key, g.url, g.headers, g.source_id, generation=g.recovery_generation
            )
            return StreamingResponse(
                c.content_policy.guard_stream(g, c.auth, gen), media_type=ctype, headers={"Cache-Control": "no-cache"}
            )
        status, headers, gen = await c.proxy.passthrough(
            g.url, g.headers, g.source_id, request.headers.get("range"), live=g.live, generation=g.recovery_generation
        )
    except (ConnectionError, OSError) as exc:
        raise BadGateway("upstream unavailable") from exc
    except ServiceError:
        raise
    except Exception as exc:
        log.warning("stream proxy failed", exc_info=True, extra={"err": type(exc).__name__})
        raise BadGateway("upstream unavailable") from exc
    return StreamingResponse(
        c.content_policy.guard_stream(g, c.auth, gen),
        status_code=status,
        headers=headers,
        media_type=headers.get("content-type", "application/octet-stream"),
    )


@router.get("/stream/{token}/seg/{sub}", tags=["playback"], include_in_schema=False)
async def stream_segment(token: str, sub: str, request: Request, c: C) -> Response:
    g = await _grant(c, token)
    d = c.crypto.try_decrypt_json(sub, ttl=c.tokens.ttl_s)
    if (
        not isinstance(d, dict)
        or not isinstance(d.get("u"), str)
        or not isinstance(d.get("grant"), str)
        or not hmac.compare_digest(d["grant"], hashlib.sha256(token.encode()).hexdigest())
    ):
        raise Unauthorized("invalid segment token")
    url = str(d["u"])
    try:
        if ".m3u8" in url.split("?", 1)[0].lower():
            final, text = await c.proxy.fetch_text(url, g.headers, generation=g.recovery_generation)
            body = c.proxy.rewrite_playlist(text, final, lambda u: _proxy_url(token, u, c))
            return Response(body, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})
        status, headers, gen = await c.proxy.passthrough(
            url, g.headers, g.source_id, request.headers.get("range"), generation=g.recovery_generation
        )
    except (ConnectionError, OSError) as exc:
        raise BadGateway("upstream unavailable") from exc
    except ServiceError:
        raise
    except Exception as exc:
        log.warning("stream segment proxy failed", exc_info=True, extra={"err": type(exc).__name__})
        raise BadGateway("upstream unavailable") from exc
    return StreamingResponse(
        c.content_policy.guard_stream(g, c.auth, gen),
        status_code=status,
        headers=headers,
        media_type=headers.get("content-type", "video/mp2t"),
    )


@router.get("/stream/{token}/index.m3u8", tags=["playback"])
async def stream_hls(token: str, c: C, at: datetime | None = None, lease_id: str | None = None) -> Response:
    g = await _grant(c, token)
    if at is not None and g.content_ref:
        await c.content_policy.require(g.profile_id, g.content_ref, at=at)
    transcode = str(c.app_settings.get("transcode_hw") or "none") if g.transcode else "none"
    if g.transcode and transcode == "none":
        transcode = "software"
    key = f"{g.source_id}:{g.url}:{transcode}"
    if at is not None or lease_id is not None:
        if lease_id is None or c.proxy.dvr.key != key:
            raise Gone("Buffered session expired.", code="buffer_expired")
        c.proxy.dvr.check(key, g.profile_id, lease_id)
    try:
        source = await c.sources.get(g.source_id)
        sess = await c.proxy.hls_session(
            key,
            g.url,
            g.headers,
            live=g.live,
            transcode=transcode,
            source_id=g.source_id,
            max_connections=c.sources.max_connections(source),
            generation=g.recovery_generation,
        )
    except HlsSourceBusy as exc:
        raise Conflict("Another channel is using this source. Stop it and retry.", code="source_busy") from exc
    except (ConnectionError, TimeoutError, OSError) as exc:
        raise BadGateway("could not start HLS session") from exc
    except ServiceError:
        raise
    except Exception as exc:
        log.warning("hls session failed", exc_info=True, extra={"err": type(exc).__name__})
        raise BadGateway("could not start HLS session") from exc
    if c.proxy.dvr.key == key:
        if at is not None or lease_id is not None:
            if lease_id is None:
                raise Forbidden("DVR seek requires a viewer lease.")
            c.proxy.dvr.check(key, g.profile_id, lease_id)
        text = await c.proxy.buffer_manifest(key, at)
    else:
        if at is not None or lease_id is not None:
            raise NotFound("Buffered session expired.")
        text = (sess.workdir / "index.m3u8").read_text(errors="ignore")
    base = f"/api/v1/stream/{token}/hls/{sess.sid}/"
    lines = [base + ln if ln and not ln.startswith("#") else ln for ln in text.splitlines()]
    return Response(
        "\n".join(lines) + "\n", media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"}
    )


@router.get("/stream/{token}/hls/{sid}/{name}", tags=["playback"], include_in_schema=False)
async def stream_hls_segment(token: str, sid: str, name: str, c: C) -> Response:
    g = await _grant(c, token)
    transcode = str(c.app_settings.get("transcode_hw") or "none") if g.transcode else "none"
    if g.transcode and transcode == "none":
        transcode = "software"
    key = f"{g.source_id}:{g.url}:{transcode}"
    if sid != c.proxy.session_id(key):
        raise Forbidden("Segment does not belong to this playback grant.")
    sess = c.proxy.hls_by_sid(sid)
    if sess is None or "/" in name or ".." in name or not name.endswith((".ts", ".m4s", ".mp4", ".m3u8")):
        raise NotFound("segment not found")
    path: Path = sess.workdir / name
    if not path.is_file():
        raise NotFound("segment not found")
    await c.content_policy.require_segment(g, c.auth, path, sess.workdir / "index.m3u8")
    return FileResponse(
        path,
        media_type="video/mp2t" if name.endswith(".ts") else "application/octet-stream",
        headers={"Cache-Control": "private, no-store"} if c.content_policy.enabled else {"Cache-Control": "max-age=60"},
    )


@router.get("/images/{token}", tags=["catalog"], include_in_schema=False)
async def image_proxy(token: str, c: C) -> Response:
    """Artwork from authenticated library servers (Plex) without exposing tokens to clients."""
    d = c.crypto.verify_json(token)
    if not isinstance(d, dict):
        raise NotFound("image not found")
    from app.services.image_grants import validate_image_grant

    await validate_image_grant(d, c.content_policy, c.auth)
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
    await validate_image_grant(d, c.content_policy, c.auth)
    return Response(
        resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "private, no-store" if c.content_policy.enabled else "public, max-age=86400"},
    )
