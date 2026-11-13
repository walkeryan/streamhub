"""/playback/resolve, /playback/progress, /stream/{token}[/index.m3u8], images proxy."""

from __future__ import annotations

import asyncio
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
from app.api.streaming import OwnedStreamingResponse, byte_range, file_range
from app.domain.ports import ImageAuthProvider
from app.proxy.stream_proxy import HlsSourceBusy, Viewer, VodPlan
from app.services import ids
from app.services.context import absolute, is_https
from app.services.errors import BadGateway, Conflict, Forbidden, Gone, NotFound, ServiceError, Unauthorized
from app.services.playback import StreamGrant, play_key
from app.services.streams import is_recording, open_failed, panes, source_busy

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/playback/resolve", response_model=S.PlaybackDescriptor, tags=["playback"])
async def resolve(body: S.PlaybackRequest, c: C, p: P) -> dict[str, Any]:
    c.proxy.ensure_running()
    if body.multiview or body.companion_id:
        raise Conflict("Use atomic two-pane admission.", code="multiview_atomic_required")
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
        audio_track=body.audio_track,
        start_position_ms=body.start_position_ms,
    )
    c.proxy.ensure_running(generation)
    result["buffer_available"] = False
    if c.proxy.dvr.enabled and result.get("mode") == "hls" and result.get("live") and body.id.startswith("ch:"):
        capability = await c.playback.rewind_capability(p.profile_id, body.id)
        result["buffer_available"] = capability["mode"] == "unavailable"
    return result


@router.post("/playback/multiview", response_model=S.MultiviewDescriptor, tags=["playback"])
async def multiview(body: S.MultiviewRequest, c: C, p: P) -> dict[str, Any]:
    c.proxy.ensure_running()
    generation = c.proxy.generation
    if body.recovery_generation != c.proxy.recovery_epoch:
        raise Forbidden("Start multiview again manually.", code="playback_stopped")
    await c.streams.require_slot(p.user_id, p.session_id, p.limits.max_streams, slots=len(body.panes))
    if any(not pane.id.startswith("ch:") or pane.catchup_programme_id or pane.rewind_at for pane in body.panes):
        raise ServiceError("Multiview requires two live channels.", code="multiview_live_only")
    if body.panes[0].id == body.panes[1].id:
        raise Conflict("Choose two different channels.", code="multiview_same_channel")
    descriptors = []
    grants = []
    members = {}
    # Resolution performs policy/audio checks only; no playback URL is returned
    # until both producer slots have been reserved in one critical section.
    for pane in body.panes:
        with panes(len(body.panes)):  # two streams on one device: neither takes the other's place
            desc = await c.playback.resolve(
                p.profile_id,
                pane.id,
                source_id=pane.source_id,
                variant_id=pane.variant_id,
                allow_unverified_audio=pane.allow_unverified_audio,
                client="web_hls",
                https_site=is_https.get(),
            )
        if desc.get("mode") != "hls" or not desc.get("live"):
            raise Conflict("Multiview requires server-mediated live HLS.", code="multiview_unsupported")
        token = str(desc["url"]).split("/stream/", 1)[1].split("/", 1)[0]
        grant = c.tokens.verify(token)
        if grant is None or grant.transcode:
            raise Conflict("Multiview requires codec-copy HLS.", code="multiview_unsupported")
        source = await c.sources.get(grant.source_id)
        if not source.enabled:
            raise NotFound("Channel source is disabled.")
        key = f"{grant.source_id}:{grant.url}:none"
        if c.proxy.dvr.key == key:
            raise Conflict("Release buffered playback before multiview.", code="source_busy")
        members[key] = (grant.source_id, c.sources.max_connections(source))
        grant.audio_variant = pane.variant_id
        grant.audio_consent = bool(pane.variant_id and pane.allow_unverified_audio)
        grant.audio_preferred = desc["preferred_audio_languages"]
        descriptors.append(desc)
        grants.append(grant)
    lease_id = await c.proxy.reserve_pair(p.profile_id, p.session_id, members, generation=generation)
    try:
        for desc, grant in zip(descriptors, grants, strict=True):
            # Recheck after all awaits so a policy/session change cannot slip
            # between the first pane's authorization and the atomic response.
            version, session_id, content_ref = _pair_identity(grant)
            if grant.profile_id != p.profile_id or session_id != p.session_id:
                raise Forbidden("Multiview grant belongs to another session.", code="multiview_expired")
            await c.auth.session_valid(grant.profile_id, version, session_id)
            await c.content_policy.require_grant(grant, c.auth)
            await c.playback._check_channel_audio(p.profile_id, content_ref, grant.audio_variant, grant.audio_consent)
            if await c.catalog.preferred_languages(p.profile_id, None) != grant.audio_preferred:
                raise Conflict("Audio preferences changed; select the pair again.", code="audio_selection_required")
            grant.pair_lease_id = lease_id
            desc["url"] = absolute(f"/api/v1/stream/{c.tokens.issue(grant)}/index.m3u8")
            desc["buffer_available"] = False
        c.proxy.ensure_running(generation)
        return {"lease_id": lease_id, "expires_in_seconds": 60, "panes": descriptors}
    except BaseException:
        await asyncio.shield(c.proxy.release_pair(lease_id, p.profile_id, p.session_id))
        raise


@router.post("/playback/multiview/release", status_code=204, tags=["playback"])
async def multiview_release(body: S.MultiviewRelease, c: C, p: P) -> Response:
    await c.proxy.release_pair(body.lease_id, p.profile_id, p.session_id)
    return Response(status_code=204)


@router.get("/playback/options", tags=["playback"])
async def playback_options(id: str, c: C, p: P, source_id: str | None = None) -> dict[str, Any]:
    """Source variants and their audio. A VOD file whose source lists no audio tracks is probed here, once."""
    return await c.playback.options(p.profile_id, id, source_id)


@router.get("/playback/rewind", response_model=S.RewindCapability, tags=["playback"])
async def rewind_capability(id: str, c: C, p: P) -> dict[str, Any]:
    return await c.playback.rewind_capability(p.profile_id, id)


async def _buffer_grant(token: str, c: C, p: P) -> tuple[Any, str, Any]:
    g = await _grant(c, token)
    if g.pair_lease_id:
        raise Conflict("Multiview does not support buffered recording.", code="buffer_unsupported")
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
            viewer=_viewer(g),
        )
    except HlsSourceBusy as exc:
        raise await _source_busy(c, g) from exc


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
    if body.expected_profile_id is not None and body.expected_profile_id != p.profile_id:
        raise Forbidden("Playback belongs to another profile.", code="profile_changed")
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
    # The player's own word on its stream: still playing (it counts toward the user's limit even
    # when no bytes pass through this server), or stopped (it no longer does).
    if body.state == "stopped":
        c.streams.release(p.user_id, p.session_id, body.id)
    else:
        c.streams.heartbeat(p.user_id, p.session_id, body.id)
    return Response(status_code=204)


# ---------------------------------------------------------------- streaming
def _pair_identity(grant: StreamGrant) -> tuple[int, str, str]:
    if (
        grant.profile_version is None
        or not grant.session_id
        or not grant.content_ref
        or not grant.content_ref.startswith("ch:")
    ):
        raise Forbidden("Multiview grant is missing its session or channel binding.", code="multiview_expired")
    return grant.profile_version, grant.session_id, grant.content_ref


async def _grant(c: C, token: str) -> Any:
    c.proxy.ensure_running()
    g = c.tokens.verify(token)
    if g is None:
        raise Unauthorized("invalid or expired stream token")
    try:
        await c.guard.require_grant(g)
        if g.pair_lease_id:
            version, session_id, content_ref = _pair_identity(g)
            await c.auth.session_valid(g.profile_id, version, session_id)
            source = await c.sources.get(g.source_id)
            maximum = c.sources.max_connections(source)
            if not maximum or maximum < 1 or c.proxy._source_use(g.source_id) > maximum:
                raise Conflict("Provider capacity changed.", code="multiview_capacity")
            if not source.enabled or await c.catalog.preferred_languages(g.profile_id, None) != g.audio_preferred:
                raise Forbidden("Multiview source or audio preferences changed.", code="multiview_expired")
            await c.playback._check_channel_audio(g.profile_id, content_ref, g.audio_variant, g.audio_consent)
            await c.proxy.validate_pair(g.pair_lease_id, f"{g.source_id}:{g.url}:none", g.profile_id, g.session_id)
    except BaseException:
        if g.pair_lease_id:
            await asyncio.shield(c.proxy.release_pair(g.pair_lease_id, g.profile_id, g.session_id))
        raise
    g.recovery_generation = c.proxy.generation
    return g


def _viewer(g: StreamGrant) -> Viewer:
    return Viewer(
        g.profile_id, g.title, g.kind, g.content_ref, user_id=g.user_id, session_id=g.session_id, client=g.client
    )


async def _source_busy(c: C, g: StreamGrant, message: str = "Source connection limit is occupied.") -> Conflict:
    return await source_busy(c, g.source_id, g.profile_id, message)


async def _open_failed(c: C, g: StreamGrant, exc: Exception, maximum: int | None, message: str) -> ServiceError:
    return await open_failed(c, g.source_id, g.profile_id, exc, maximum, message)


async def _recording_file(c: C, g: StreamGrant, request: Request) -> Response:
    """A recording's file, whole or the range asked for, checked like any stream while it is sent."""
    path = await c.recorder.stored(str(g.content_ref)[4:])
    size = path.stat().st_size
    asked = request.headers.get("range")
    span = byte_range(asked, size)
    if span is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
    start, end = span
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store",
    }
    if asked:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        c.guard.guard_stream(g, file_range(path, start, end)),
        status_code=206 if asked else 200,
        headers=headers,
        media_type="video/x-matroska",
    )


def _proxy_url(token: str, upstream: str, c: C) -> str:
    sub = c.crypto.encrypt_json({"u": upstream, "grant": hashlib.sha256(token.encode()).hexdigest()})
    return f"/api/v1/stream/{token}/seg/{sub}"


@router.get("/stream/{token}", tags=["playback"], response_class=StreamingResponse)
async def stream(token: str, request: Request, c: C) -> Response:
    g = await _grant(c, token)
    if g.mode == "hls":
        return Response(status_code=307, headers={"Location": f"/api/v1/stream/{token}/index.m3u8"})
    if is_recording(g):
        return await _recording_file(c, g, request)
    source = await c.sources.get(g.source_id)
    maximum = c.sources.max_connections(source)
    is_hls_upstream = ".m3u8" in g.url.split("?", 1)[0].lower()
    try:
        if is_hls_upstream:
            final, text = await c.proxy.fetch_text(
                g.url, g.headers, generation=g.recovery_generation, source_id=g.source_id, max_connections=maximum
            )
            body = c.proxy.rewrite_playlist(text, final, lambda u: _proxy_url(token, u, c))
            return Response(body, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})
        if g.live and g.channel_key:
            ctype, gen = await c.proxy.live_shared(
                g.channel_key,
                g.url,
                g.headers,
                g.source_id,
                generation=g.recovery_generation,
                max_connections=maximum,
                viewer=_viewer(g),
            )
            return OwnedStreamingResponse(
                c.guard.guard_stream(g, gen),
                owned_stream=gen,
                media_type=ctype,
                headers={"Cache-Control": "no-cache"},
            )
        status, headers, gen = await c.proxy.passthrough(
            g.url,
            g.headers,
            g.source_id,
            request.headers.get("range"),
            live=g.live,
            generation=g.recovery_generation,
            max_connections=maximum,
            viewer=_viewer(g),
            pin_key=None if g.live else play_key(g.source_id, g.url, g.profile_id, g.session_id),
        )
    except (ConnectionError, OSError) as exc:
        raise await _open_failed(c, g, exc, maximum, "upstream unavailable") from exc
    except ServiceError as exc:
        if exc.code != "source_busy":
            raise
        raise await _source_busy(c, g, exc.message) from exc
    except Exception as exc:
        log.warning("stream proxy failed", exc_info=True, extra={"err": type(exc).__name__})
        raise BadGateway("upstream unavailable") from exc
    return OwnedStreamingResponse(
        c.guard.guard_stream(g, gen),
        owned_stream=gen,
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
    source = await c.sources.get(g.source_id)
    maximum = c.sources.max_connections(source)
    url = str(d["u"])
    try:
        if ".m3u8" in url.split("?", 1)[0].lower():
            final, text = await c.proxy.fetch_text(
                url, g.headers, generation=g.recovery_generation, source_id=g.source_id, max_connections=maximum
            )
            body = c.proxy.rewrite_playlist(text, final, lambda u: _proxy_url(token, u, c))
            return Response(body, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})
        status, headers, gen = await c.proxy.passthrough(
            url,
            g.headers,
            g.source_id,
            request.headers.get("range"),
            generation=g.recovery_generation,
            max_connections=maximum,
            viewer=_viewer(g),
        )
    except (ConnectionError, OSError) as exc:
        raise await _open_failed(c, g, exc, maximum, "upstream unavailable") from exc
    except ServiceError as exc:
        if exc.code != "source_busy":
            raise
        raise await _source_busy(c, g, exc.message) from exc
    except Exception as exc:
        log.warning("stream segment proxy failed", exc_info=True, extra={"err": type(exc).__name__})
        raise BadGateway("upstream unavailable") from exc
    return OwnedStreamingResponse(
        c.guard.guard_stream(g, gen),
        owned_stream=gen,
        status_code=status,
        headers=headers,
        media_type=headers.get("content-type", "video/mp2t"),
    )


def _hls_session_key(c: C, g: StreamGrant) -> tuple[str, str, VodPlan | None]:
    """Producer key, transcode mode and (for web VOD) plan of the HLS session a grant plays."""
    hw = str(c.app_settings.get("transcode_hw") or "none")
    if g.vod_video:
        # One session per viewer, file, audio track and start offset; the codec alone decides
        # whether the video is transcoded, so the per-source ``transcode`` flag is not consulted.
        family = play_key(g.source_id, g.url, g.profile_id, g.session_id)
        plan = VodPlan(
            video=g.vod_video,
            hw=hw if g.vod_video == "transcode" else "none",
            codec=g.vod_codec,
            width=g.vod_width,
            audio_track=g.audio_track,
            start_s=g.start_s,
            family=family,
        )
        return f"{family}:vod:{plan.video}:{plan.hw}:{plan.audio_track}:{plan.start_s}", "none", plan
    transcode = hw if g.transcode else "none"
    if g.transcode and transcode == "none":
        transcode = "software"
    return f"{g.source_id}:{g.url}:{transcode}", transcode, None


async def _start_hls(c: C, g: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await c.proxy.hls_session(*args, **kwargs)
    except BaseException:
        if g.pair_lease_id:
            await asyncio.shield(c.proxy.release_pair(g.pair_lease_id, g.profile_id, g.session_id))
        raise


@router.get("/stream/{token}/index.m3u8", tags=["playback"])
async def stream_hls(token: str, c: C, at: datetime | None = None, lease_id: str | None = None) -> Response:
    g = await _grant(c, token)
    if at is not None and g.content_ref:
        await c.content_policy.require(g.profile_id, g.content_ref, at=at)
    # A watch room's member reads the room's session; the room decides its key, start and audio.
    key, transcode, vod = ("", "none", None) if g.room else _hls_session_key(c, g)
    if at is not None or lease_id is not None:
        if lease_id is None or g.room or c.proxy.dvr.key != key:
            raise Gone("Buffered session expired.", code="buffer_expired")
        c.proxy.dvr.check(key, g.profile_id, lease_id)
    # A recording is a file of this server's: ffmpeg reads it from disk, no source and no limit.
    recording = await c.recorder.stored(str(g.content_ref)[4:]) if is_recording(g) else None
    maximum = None if recording else c.sources.max_connections(await c.sources.get(g.source_id))
    try:
        if g.room:
            sess = await c.room_playback.open(g)
        else:
            sess = await _start_hls(
                c,
                g,
                key,
                str(recording) if recording else g.url,
                g.headers,
                live=g.live,
                transcode=transcode,
                source_id=None if recording else g.source_id,
                max_connections=maximum,
                generation=g.recovery_generation,
                pair_lease_id=g.pair_lease_id,
                vod=vod,
                viewer=_viewer(g),
                # A file remuxed without a plan is one process for everyone watching it: one upstream viewer.
                pin_key=None if g.live or recording else vod.family if vod is not None else key,
            )
    except HlsSourceBusy as exc:
        raise await _source_busy(c, g, "Another stream is using this source. Stop it and retry.") from exc
    except (ConnectionError, TimeoutError, OSError) as exc:
        raise await _open_failed(c, g, exc, maximum, "could not start HLS session") from exc
    except ServiceError as exc:
        if exc.code != "source_busy":
            raise
        raise await _source_busy(c, g, exc.message) from exc
    except Exception as exc:
        log.warning("hls session failed", exc_info=True, extra={"err": type(exc).__name__})
        raise BadGateway("could not start HLS session") from exc
    if not g.room and c.proxy.dvr.key == key:
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
    if (vod is not None or g.room) and lines and lines[0].startswith("#EXTM3U"):
        # The playlist grows while ffmpeg runs, which players treat as live and join at its end.
        # It is a film from this session's start offset: begin at its first segment.
        lines.insert(1, "#EXT-X-START:TIME-OFFSET=0")
    return Response(
        "\n".join(lines) + "\n", media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"}
    )


@router.get("/stream/{token}/hls/{sid}/{name}", tags=["playback"], include_in_schema=False)
async def stream_hls_segment(token: str, sid: str, name: str, c: C) -> Response:
    g = await _grant(c, token)
    # After a room seek the room's session has a new id: segments of the old one are not served.
    expected = c.room_playback.sid(g) if g.room else c.proxy.session_id(_hls_session_key(c, g)[0])
    if sid != expected:
        raise Forbidden("Segment does not belong to this playback grant.")
    sess = c.proxy.hls_by_sid(sid)
    if sess is None or "/" in name or ".." in name or not name.endswith((".ts", ".m4s", ".mp4", ".m3u8")):
        raise NotFound("segment not found")
    path: Path = sess.workdir / name
    if not path.is_file():
        raise NotFound("segment not found")
    await c.guard.require_segment(g, path, sess.workdir / "index.m3u8")
    return FileResponse(
        path,
        media_type="video/mp2t" if name.endswith(".ts") else "application/octet-stream",
        headers={"Cache-Control": "private, no-store"} if c.content_policy.enabled else {"Cache-Control": "max-age=60"},
    )


@router.get("/images/{token}", tags=["catalog"], include_in_schema=False)
async def image_proxy(token: str, c: C) -> Response:
    """Artwork clients cannot fetch themselves, behind a signed grant.

    Either the server needs a credential the client must never see (Plex), or the
    source is ``proxy: "always"`` and its hosts are unreachable from the client.
    """
    d = c.crypto.verify_json(token)
    if not isinstance(d, dict):
        raise NotFound("image not found")
    from app.services.image_grants import WEB_SCHEMES, validate_image_grant

    await validate_image_grant(d, c.content_policy, c.auth)
    source_id = str(d.get("s"))
    try:
        src, prov = await c.sources.provider_for(source_id)
    except NotFound:
        raise
    url = str(d.get("u") or "")
    headers = prov.image_headers(url) if isinstance(prov, ImageAuthProvider) else None
    # Proxied artwork URLs are provider data: fetch only web URLs, and below, serve only images.
    foreign = headers is None
    if foreign and src is not None and url.startswith(WEB_SCHEMES):
        headers = c.sources.proxied_image_headers(src)
    if headers is None:
        raise NotFound("image not found")
    cache = getattr(c, "image_cache", None)
    hit = await cache.get(source_id, url) if cache is not None else None
    if hit is not None:
        body, media_type = hit
    else:
        try:
            async with c.http_factory(timeout=20.0, headers=headers) as client:
                resp = await client.get(url)
        except Exception as exc:
            raise BadGateway("image unavailable") from exc
        if resp.status_code >= 400:
            raise NotFound("image not found")
        body, media_type = resp.content, resp.headers.get("content-type", "image/jpeg")
        if foreign and not media_type.lower().startswith("image/"):
            raise NotFound("image not found")
        # The fetch can outlive the grant (session revoked, policy changed): check again.
        await validate_image_grant(d, c.content_policy, c.auth)
        if cache is not None and media_type.lower().startswith("image/"):
            await cache.put(source_id, url, body, media_type)
    return Response(
        body,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store" if c.content_policy.enabled else "public, max-age=86400",
            # Never let a browser reinterpret or run what a provider called an image (SVG included).
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
