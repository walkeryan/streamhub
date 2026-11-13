"""StreamProxy.

- **Passthrough** (``/stream/{token}``): async httpx streaming with upstream headers and
  reconnect-with-backoff for live streams. Live channels share one upstream connection
  fanned out to N clients (``SharedUpstream``), closed 10 s after the last client leaves.
  HLS upstreams have their playlists rewritten so segments also flow through the proxy.
- **HLS remux / transcode** (``/stream/{token}/index.m3u8``): ffmpeg ``-c copy -f hls``
  (or libx264 / VAAPI when enabled) into ``<transcode>/<id>/``; idle sessions are reaped.
- **Web VOD** (``VodPlan``): a file a browser cannot decode becomes an HLS session of its own,
  with the video copied or transcoded and one audio track encoded to AAC stereo.
- Every session carries the ``Viewer`` its grant named, so a profile turned away by a source's
  connection limit can be told who holds the connections (``active_viewers``).
- **Pinned upstream sessions** (``pin``): panels that hand out a per-viewer session on redirect see
  one viewer for a probe, a play and every seek of one file, because all of them use the redirect
  target that the first request was given (see ``pin_upstream_session``). Live streams are not pinned.
- **Watch rooms** read one HLS session that is kept (``keep``) while the room has members, so it is
  neither reaped as idle nor evicted for another viewer; a seek stops it and starts it again under
  the same key with a new session id (``hls_session(sid=…)``).
- **Recordings** either subscribe to a channel's shared upstream (``Tap``) or hold a connection of
  their own (``capture_slot``); both count against the source's limit and are named to a profile
  it turns away. The capture itself lives in ``app.proxy.capture``.

Seeking in a web VOD session is restart-at-offset. A session starts at ``start_s`` (``-ss`` before
``-i``) and its playlist only ever lists what ffmpeg has produced since, as an ``EVENT`` playlist
whose time zero is that offset; the full length comes from the playback descriptor. A seek outside
the produced range starts a new session at the new offset and stops the viewer's old one. Limits:
- every such seek pays a session start (an upstream reconnect and, when transcoding, encoder startup);
- with copied video the session really starts at the keyframe before ``start_s``, so the position
  shown can be ahead of the picture by up to one keyframe interval;
- input is read at ``VOD_READRATE`` times playback speed after a short burst, so the produced range
  grows only that fast and the session holds its upstream connection until the film is read;
- segments are kept until the session is reaped, so a watched film occupies its transcoded size.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from app.infra.http import pin_upstream_session
from app.infra.logging import redact_url
from app.proxy.dvr import MAX_SEGMENTS, DvrManager
from app.services.errors import Conflict, Forbidden, ServiceError

log = logging.getLogger(__name__)

CHUNK = 64 * 1024
CLIENT_QUEUE = 256  # ~16 MB of backlog per client before it's dropped
LINGER_S = 10.0
MAX_BACKOFF = 15.0
HLS_IDLE_S = 60.0
HLS_READY_TIMEOUT = 20.0
HLS_SWITCH_GRACE_S = 5.0
HLS_ADMISSION_WAIT_S = 5.0
HLS_STOP_TIMEOUT_S = 0.5
PAIR_TTL_S = 60.0
MAX_PAIR_LEASES = 16
VOD_SEGMENT_S = 4
VOD_READRATE = "2"
VOD_BURST_S = "30"
# A finished VOD session holds no upstream connection, only files: keep it through a pause.
VOD_DONE_IDLE_S = 600.0
# A pinned upstream URL is kept this long after its last use, and while a session is using it.
PIN_TTL_S = 2 * 3600.0
# An upstream that gave no session (it answers directly, or was busy or down) is asked again after this.
PIN_MISS_TTL_S = 60.0
PIN_TIMEOUT_S = 8.0
MAX_PINS = 512
# How a panel answers a session it no longer knows; ffmpeg names 410 only by its class.
PIN_EXPIRED = {"403", "404", "410", "4XX"}
# Codecs NVDEC decodes; anything else is decoded in software and only encoded on the GPU.
CUDA_DECODE = {"h264", "hevc", "vp8", "vp9", "av1", "mpeg2video", "mpeg4", "vc1"}
# ffmpeg's last line when the upstream refuses the request, e.g. "Server returned 403 Forbidden".
UPSTREAM_STATUS = re.compile(rb"Server returned (\d[\dX]{2})")


class HlsSourceBusy(Exception):
    """Another recently polled HLS channel occupies this source’s connection budget."""


class UpstreamRefused(ConnectionError):
    """The source answered a stream open with an HTTP error, or hung up before answering.

    ``status`` is the code as text ("403"; ffmpeg names some only by class, "4XX" or "5XX"),
    or ``None`` when the connection closed without a response.
    """

    def __init__(self, status: str | None) -> None:
        super().__init__(f"upstream {status or 'closed'}")
        self.status = status


@dataclass(frozen=True, slots=True)
class Viewer:
    """Who a stream is for, as its playback grant said; named in a ``source_busy`` answer."""

    profile_id: str
    title: str | None = None
    kind: str | None = None
    ref: str | None = None  # content id, so the answer can hide what the refused profile may not see
    since: datetime = field(default_factory=lambda: datetime.now(UTC))
    # The user behind the profile, the signed-in device and the kind of client, when a grant named them.
    user_id: str | None = None
    session_id: str | None = None
    client: str | None = None


HOP_HEADERS = {"content-type", "content-length", "accept-ranges", "content-range", "last-modified", "etag"}


def _backoff(attempt: int) -> float:
    return float(min(MAX_BACKOFF, 0.5 * (2**attempt)))


class OwnedStream(AsyncIterator[bytes]):
    """An acquired upstream has a lifetime even before its body is iterated."""

    def __init__(self, stream: AsyncIterator[bytes], close: Callable[[], Awaitable[None]]) -> None:
        self._stream = stream
        self._close = close
        self._closed = False
        self._close_lock = asyncio.Lock()

    def __aiter__(self) -> OwnedStream:
        return self

    async def __anext__(self) -> bytes:
        if self._closed:
            raise StopAsyncIteration
        try:
            return await anext(self._stream)
        except BaseException:
            # A second caller cancellation cannot abandon the owned cleanup.
            await asyncio.shield(self.aclose())
            raise

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            try:
                close_body = getattr(self._stream, "aclose", None)
                if close_body is not None:
                    await close_body()
            finally:
                await self._close()
            self._closed = True


class Tap:
    """A recording's seat on a shared upstream.

    It is handed the bytes viewers are, and can never slow them: its queue is bounded like a
    viewer's, and a recording that falls that far behind is dropped (``dropped``), not waited for.
    While it is subscribed the upstream stays open, with viewers or without.
    """

    def __init__(self, upstream: SharedUpstream, queue: asyncio.Queue[bytes | None]) -> None:
        self.upstream = upstream
        self.queue = queue
        self.dropped = False
        self.closed = False

    @property
    def alone(self) -> bool:
        """Nobody is watching: the upstream's connection is held for the recording only."""
        return not self.closed and self.upstream.subscribers == len(self.upstream.taps)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.upstream.unsubscribe(self.queue)


class SharedUpstream:
    """One upstream HTTP connection broadcast to many subscribers."""

    def __init__(
        self,
        key: str,
        url: str,
        headers: dict[str, str],
        client_factory: Callable[..., Any],
        on_close: Callable[[str], None],
    ) -> None:
        self.key = key
        self.url = url
        self.headers = headers
        self._client_factory = client_factory
        self._on_close = on_close
        self._subs: set[asyncio.Queue[bytes | None]] = set()
        self._task: asyncio.Task[None] | None = None
        self._linger: asyncio.TimerHandle | None = None
        self.content_type = "video/mp2t"
        self._ready = asyncio.Event()
        self.failed: str | None = None
        self.closed = False
        self.source_id: str | None = None
        self.viewers: dict[asyncio.Queue[bytes | None], Viewer] = {}
        self.taps: dict[asyncio.Queue[bytes | None], Tap] = {}

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def tap(self, viewer: Viewer | None = None) -> Tap:
        """Subscribe a recording (see ``Tap``). ``viewer`` is how a ``source_busy`` answer names it."""
        q = self.subscribe()
        tap = self.taps[q] = Tap(self, q)
        if viewer is not None:
            self.viewers[q] = viewer
        return tap

    def subscribe(self) -> asyncio.Queue[bytes | None]:
        q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=CLIENT_QUEUE)
        self._subs.add(q)
        if self._linger is not None:
            self._linger.cancel()
            self._linger = None
        if self._task is None or self._task.done():
            self._ready.clear()
            self.failed = None
            self._task = asyncio.create_task(self._pump())
        return q

    def unsubscribe(self, q: asyncio.Queue[bytes | None]) -> None:
        self._subs.discard(q)
        self.viewers.pop(q, None)
        self.taps.pop(q, None)
        if not self.closed and not self._subs and self._linger is None:
            loop = asyncio.get_running_loop()
            self._linger = loop.call_later(LINGER_S, self._close_if_idle)

    def _close_if_idle(self) -> None:
        self._linger = None
        if self._subs:
            return
        self.closed = True
        if self._task is not None:
            self._task.cancel()
        if self._task is None or self._task.done():
            self._on_close(self.key)
        else:
            self._task.add_done_callback(lambda _: self._on_close(self.key))

    async def wait_ready(self, timeout: float = 15.0) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout)

    def _broadcast(self, item: bytes | None) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                # slow client: drop it rather than stall everyone
                self._subs.discard(q)
                self.viewers.pop(q, None)
                tap = self.taps.pop(q, None)
                if tap is not None:
                    tap.dropped = True
                with contextlib.suppress(asyncio.QueueFull):
                    q.get_nowait()
                    q.put_nowait(None)

    async def _pump(self) -> None:
        attempt = 0
        try:
            async with self._client_factory(timeout=None, headers=self.headers) as client:
                while self._subs:
                    try:
                        async with client.stream("GET", self.url) as resp:
                            if resp.status_code >= 400:
                                raise httpx.HTTPStatusError(
                                    f"upstream {resp.status_code}", request=resp.request, response=resp
                                )
                            self.content_type = resp.headers.get("content-type", self.content_type)
                            self._ready.set()
                            attempt = 0
                            async for chunk in resp.aiter_bytes(CHUNK):
                                if not self._subs:
                                    return
                                self._broadcast(chunk)
                        # upstream closed cleanly: live streams reconnect
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403, 404):
                            self.failed = f"upstream {exc.response.status_code}"
                            self._ready.set()
                            break
                        log.info(
                            "upstream reconnect",
                            extra={"url": redact_url(self.url), "err": type(exc).__name__, "attempt": attempt},
                        )
                    if attempt >= 8:
                        self.failed = "upstream unavailable"
                        self._ready.set()
                        break
                    await asyncio.sleep(_backoff(attempt))
                    attempt += 1
        finally:
            self._ready.set()
            self._broadcast(None)


@dataclass
class HlsSession:
    sid: str
    workdir: Path
    proc: asyncio.subprocess.Process
    last_access: float = field(default_factory=time.monotonic)
    source_id: str | None = None
    stderr_bytes: int = 0
    stderr_task: asyncio.Task[None] | None = None
    # Web VOD sessions: the viewer+file they belong to, the attempt running and those left to try.
    family: str | None = None
    label: str = ""
    fallbacks: list[tuple[str, list[str]]] = field(default_factory=list)
    upstream_status: str | None = None
    pin_key: str | None = None  # the pinned upstream session this one uses, if any (``StreamProxy.pin``)
    # profile id -> (viewer, when it last asked for the playlist); a live channel can have several.
    viewers: dict[str, tuple[Viewer, float]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """ffmpeg read a VOD file to its end: the segments remain, the upstream connection is gone."""
        return self.family is not None and self.proc.returncode == 0


@dataclass(frozen=True, slots=True)
class VodPlan:
    """One web VOD session: what to do with the video, which audio track, where to start."""

    video: str  # "copy" | "transcode"
    hw: str = "none"  # transcode_hw setting: none | vaapi | nvenc | qsv
    codec: str | None = None  # input video codec, when the probe knew it
    width: int | None = None
    audio_track: int | None = None
    start_s: int = 0
    family: str = ""  # sessions of one viewer for one file replace each other


@dataclass
class PairLease:
    profile_id: str
    session_id: str | None
    generation: int
    members: dict[str, str]  # producer key -> source id
    expires: float
    closing: bool = False


@dataclass(eq=False)
class HttpSession:
    client: Any
    task: asyncio.Task[Any] | None
    response: Any = None
    source_id: str | None = None
    counted: bool = False
    close_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    viewer: Viewer | None = None
    pin_key: str | None = None


@dataclass(eq=False)
class CaptureSlot:
    """One source connection held by a recording that reads its stream itself."""

    source_id: str
    viewer: Viewer | None = None
    # Ends the recording and gives the slot back; called when an administrator stops all streams.
    stop: Callable[[], Awaitable[None]] | None = None


def _upstream_args(url: str, headers: dict[str, str]) -> list[str]:
    """Input options that make ffmpeg's request look like the proxy's own."""
    args: list[str] = []
    if headers:
        ua = next((v for k, v in headers.items() if k.lower() == "user-agent"), None)
        if ua:
            args += ["-user_agent", ua]
        other = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() != "user-agent")
        if other:
            args += ["-headers", other]
    if url.startswith("http"):
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
    return args


def ffmpeg_args(
    ffmpeg: str,
    url: str,
    headers: dict[str, str],
    outdir: Path,
    *,
    live: bool,
    transcode: str,
    vaapi_device: str,
    buffered: bool = False,
) -> list[str]:
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if transcode == "vaapi":
        args += ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi", "-vaapi_device", vaapi_device]
    elif transcode == "qsv":
        args += ["-hwaccel", "qsv"]
    elif transcode == "nvenc":
        args += ["-hwaccel", "cuda"]
    args += _upstream_args(url, headers)
    if url.startswith("http") and live:
        args += ["-reconnect_at_eof", "1"]
    if buffered:
        # Bound DVR startup bursts to media time before the finite HLS writer
        # can roll away retained history. Ordinary live/VOD keeps its input policy.
        args += ["-re"]
    args += ["-fflags", "+genpts", "-i", url, "-map", "0:v:0?", "-map", "0:a?"]
    if transcode == "none" or not transcode:
        args += ["-c", "copy"]
    elif transcode == "vaapi":
        args += ["-vf", "format=nv12|vaapi,hwupload", "-c:v", "h264_vaapi", "-c:a", "aac", "-b:a", "160k"]
    elif transcode == "qsv":
        args += ["-c:v", "h264_qsv", "-c:a", "aac", "-b:a", "160k"]
    elif transcode == "nvenc":
        args += ["-c:v", "h264_nvenc", "-c:a", "aac", "-b:a", "160k"]
    else:  # software
        args += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-c:a", "aac", "-b:a", "160k"]
    flags = "delete_segments+independent_segments" if live else "independent_segments"
    if live:
        flags += "+program_date_time"
    if buffered:
        flags += "+temp_file"
    args += [
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        str(MAX_SEGMENTS) if buffered else "6" if live else "0",
        "-hls_flags",
        flags,
        "-hls_segment_filename",
        str(outdir / "seg_%05d.ts"),
        str(outdir / "index.m3u8"),
    ]
    return args


def vod_ffmpeg_attempts(
    ffmpeg: str, url: str, headers: dict[str, str], outdir: Path, plan: VodPlan, *, vaapi_device: str
) -> list[tuple[str, list[str]]]:
    """ffmpeg command lines for one web VOD session, in the order to try them.

    Copied video has one. A transcode starts with the configured hardware and ends with libx264,
    so a missing or busy GPU costs a restart instead of the film (``hls_session`` logs each step).
    """

    def command(decode: list[str], video: list[str]) -> list[str]:
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *decode, *_upstream_args(url, headers)]
        if plan.start_s > 0:
            args += ["-ss", str(plan.start_s)]
        # Faster than playback so seeking a little ahead is free, yet bounded: unthrottled, a remux
        # would pull the whole film from the source and onto disk within minutes.
        args += ["-readrate", VOD_READRATE, "-readrate_initial_burst", VOD_BURST_S]
        # "V" skips cover art; one audio track only, since the browser is given no way to switch.
        args += ["-fflags", "+genpts", "-i", url, "-map", "0:V:0?", "-map", f"0:a:{plan.audio_track or 0}?"]
        args += video
        args += ["-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", "160k"]
        return [
            *args,
            "-f",
            "hls",
            "-hls_time",
            str(VOD_SEGMENT_S),
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_flags",
            "independent_segments",
            "-hls_segment_filename",
            str(outdir / "seg_%05d.ts"),
            str(outdir / "index.m3u8"),
        ]

    if plan.video == "copy":
        return [("copy", command([], ["-c:v", "copy"]))]
    # H.264 for a browser: 8-bit 4:2:0, at most 1080p wide, a keyframe wherever a segment starts.
    scale = "scale=1920:-2," if (plan.width or 0) > 1920 else ""
    pace = ["-maxrate", "8M", "-bufsize", "16M", "-force_key_frames", f"expr:gte(t,n_forced*{VOD_SEGMENT_S})"]
    software = ["-vf", f"{scale}format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", *pace]
    attempts: list[tuple[str, list[str]]] = []
    if plan.hw == "nvenc":
        nvenc = ["-vf", f"{scale}format=yuv420p", "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23"]
        nvenc += ["-b:v", "0", *pace, "-forced-idr", "1"]
        if plan.codec in CUDA_DECODE:
            attempts.append(("nvenc+cuda", command(["-hwaccel", "cuda"], nvenc)))
        attempts.append(("nvenc", command([], nvenc)))
    elif plan.hw == "vaapi":
        decode = ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi", "-vaapi_device", vaapi_device]
        size = "w=1920:h=-2:" if scale else ""
        filters = f"format=nv12|vaapi,hwupload,scale_vaapi={size}format=nv12"
        attempts.append(("vaapi", command(decode, ["-vf", filters, "-c:v", "h264_vaapi", *pace])))
    elif plan.hw == "qsv":
        attempts.append(("qsv", command(["-hwaccel", "qsv"], ["-c:v", "h264_qsv", *pace])))
    return [*attempts, ("libx264", command([], software))]


class StreamProxy:
    def __init__(
        self,
        client_factory: Callable[..., Any],
        transcode_dir: Path,
        *,
        ffmpeg: str = "ffmpeg",
        vaapi_device: str = "/dev/dri/renderD128",
        on_start: Callable[[str], None] | None = None,
        on_stop: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        dvr: DvrManager | None = None,
    ) -> None:
        self._client_factory = client_factory
        self.dvr = dvr or DvrManager()
        self._clock = clock
        self._sleep = sleep
        self._shared: dict[str, SharedUpstream] = {}
        self._hls: dict[str, HlsSession] = {}
        self._hls_lock = asyncio.Lock()
        self._pairs: dict[str, PairLease] = {}
        self.transcode_dir = transcode_dir
        self.ffmpeg = ffmpeg
        self.vaapi_device = vaapi_device
        self._on_start = on_start or (lambda _s: None)
        self._on_stop = on_stop or (lambda _s: None)
        self._reaper: asyncio.Task[None] | None = None
        self.paused = False
        self.generation = 0
        self.recovery_epoch = secrets.token_hex(16)
        self.recovery_lock = asyncio.Lock()
        self._http: set[HttpSession] = set()
        self._viewers: dict[asyncio.Queue[bytes | None], tuple[SharedUpstream, str]] = {}
        self._cleanup_pending: set[Path] = set()
        self._probes: dict[object, str] = {}  # media probes in flight -> source id
        self._pins: dict[str, tuple[str, float]] = {}  # playback -> (upstream URL to keep using, expiry)
        self._pinning: dict[str, asyncio.Future[str]] = {}
        # HLS sessions someone answers for while nobody polls them (a watch room's): never reaped as idle.
        self._kept: set[str] = set()
        self._captures: set[CaptureSlot] = set()

    def ensure_running(self, generation: int | None = None) -> None:
        if self.paused or (generation is not None and generation != self.generation):
            raise Forbidden(
                "Playback was stopped by an administrator. Wait for playback to be resumed.", code="playback_paused"
            )

    def recovery_status(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "active_sessions": len(self._shared) + len(self._hls) + len(self._http) + len(self._captures),
            "shared_upstreams": len(self._shared),
            "hls_sessions": len(self._hls),
            "http_sessions": len(self._http),
            "external_sessions_tracked": False,
            "pair_reservations": len(self._pairs),
        }

    def pause(self) -> None:
        self.paused = True
        self.generation += 1
        self.recovery_epoch = secrets.token_hex(16)

    def _finish_viewer(self, q: asyncio.Queue[bytes | None]) -> None:
        viewer = self._viewers.pop(q, None)
        if viewer is not None:
            self._on_stop(viewer[1])

    async def _close_http(self, sess: HttpSession) -> None:
        async with sess.close_lock:
            if sess not in self._http:
                return
            try:
                if sess.response is not None:
                    await sess.response.aclose()
            finally:
                await sess.client.aclose()
            self._http.discard(sess)
            if sess.counted and sess.source_id is not None:
                sess.counted = False
                self._on_stop(sess.source_id)

    async def stop_owned(self) -> dict[str, Any]:
        """Caller serializes recovery and pauses first. Never inspect unrelated processes."""
        before = self.recovery_status()["active_sessions"]
        for key, up in list(self._shared.items()):
            up.closed = True
            if up._linger is not None:
                up._linger.cancel()
                up._linger = None
            if up._task is not None:
                up._task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(up._task), 1)
                except asyncio.CancelledError:
                    pass
                except (Exception, TimeoutError):
                    continue
            up._broadcast(None)
            for q, (owner, _) in list(self._viewers.items()):
                if owner is up:
                    self._finish_viewer(q)
            up._subs.clear()
            if self._shared.get(key) is up:
                self._shared.pop(key, None)
        for sess in list(self._http):
            if sess.task is not None and sess.task is not asyncio.current_task():
                sess.task.cancel()
            try:
                await asyncio.wait_for(self._close_http(sess), 1)
            except Exception:
                pass  # Retain ownership for a later stop attempt; never claim success.
        async with self._hls_lock:
            for key, hls in list(self._hls.items()):
                try:
                    await self._stop_hls(key, expected=hls)
                except Exception:
                    continue
        for slot in list(self._captures):
            if slot.stop is not None:
                try:
                    await slot.stop()
                except Exception:
                    continue  # Still counted, so the stop is not reported as complete.
        for path in list(self._cleanup_pending):
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                self._cleanup_pending.discard(path)
            except OSError:
                continue
            else:
                self._cleanup_pending.discard(path)
        async with self._hls_lock:
            for lease_id in list(self._pairs):
                with contextlib.suppress(Exception):
                    await self._release_pair_locked(lease_id)
        self._pins.clear()
        status = self.recovery_status()
        return {
            **status,
            "stopped": max(0, before - status["active_sessions"]),
            "failed": status["active_sessions"],
            "cleanup_failed": len(self._cleanup_pending),
        }

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.transcode_dir.mkdir(parents=True, exist_ok=True)
        for child in self.transcode_dir.iterdir():  # leftovers from a previous run
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
        self._reaper = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        async with self._hls_lock:
            for key in list(self._hls):
                await self._stop_hls(key)
        for up in list(self._shared.values()):
            if up._task:
                up._task.cancel()
        self._shared.clear()
        self._pins.clear()

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            async with self._hls_lock:
                now = self._clock()
                for lease_id, lease in list(self._pairs.items()):
                    failed = any(
                        key in self._hls and self._hls[key].proc.returncode is not None for key in lease.members
                    )
                    if lease.closing or lease.expires <= now or failed:
                        with contextlib.suppress(Exception):
                            await self._release_pair_locked(lease_id)
                for key, sess in list(self._hls.items()):
                    buffered = self.dvr.key == key
                    held = self.dvr.held(key) if buffered else False
                    if buffered:
                        try:
                            self.dvr.refresh(key)
                        except (ServiceError, OSError, ValueError):
                            await self._stop_hls(key, expected=sess)
                            continue
                    abandoned = buffered and self.dvr.ever_leased and not held
                    held = held or key in self._kept
                    idle = VOD_DONE_IDLE_S if sess.complete else HLS_IDLE_S
                    if (
                        abandoned
                        or (not held and now - sess.last_access > idle)
                        or (sess.proc.returncode is not None and not sess.complete)
                    ):
                        await self._stop_hls(key, expected=sess)
                        self._forget_pin(sess)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "shared_upstreams": {k: v.subscribers for k, v in self._shared.items()},
            "hls_sessions": len(self._hls),
        }

    def _source_use(self, source_id: str | None) -> int:
        if source_id is None:
            return 0
        return (
            sum(s.source_id == source_id and not s.complete for s in self._hls.values())
            + sum(s == source_id for s in self._probes.values())
            + sum(s.source_id == source_id for s in self._http)
            + sum(s.source_id == source_id for s in self._shared.values())
            + sum(s.source_id == source_id for s in self._captures)
            + sum(
                source == source_id and key not in self._hls
                for lease in self._pairs.values()
                for key, source in lease.members.items()
            )
        )

    async def reserve_pair(
        self,
        profile_id: str,
        session_id: str | None,
        members: dict[str, tuple[str, int | None]],
        *,
        generation: int,
    ) -> str:
        """Atomically reserve both new HLS producers, without opening either upstream."""
        async with self._hls_lock:
            self.ensure_running(generation)
            for lease_id, lease in list(self._pairs.items()):
                if lease.closing or lease.expires <= self._clock():
                    await self._release_pair_locked(lease_id)
            if len(members) != 2:
                raise Conflict("Choose two different channels.", code="multiview_same_channel")
            if len(self._pairs) >= MAX_PAIR_LEASES:
                raise Conflict("Multiview reservation limit reached.", code="source_busy")
            demands: dict[str, int] = {}
            limits: dict[str, int] = {}
            for key, (source, limit) in members.items():
                if not limit or limit < 1:
                    raise Conflict("Multiview requires a known provider capacity.", code="multiview_capacity")
                if key in self._hls or any(key in lease.members for lease in self._pairs.values()):
                    raise Conflict("Stop existing playback before creating a pair.", code="source_busy")
                demands[source] = demands.get(source, 0) + 1
                limits[source] = min(limits.get(source, limit), limit)
            for source, demand in demands.items():
                if self._source_use(source) + demand > limits[source]:
                    raise Conflict("Provider capacity cannot admit both panes.", code="multiview_capacity")
            lease_id = secrets.token_urlsafe(24)
            self._pairs[lease_id] = PairLease(
                profile_id,
                session_id,
                generation,
                {key: source for key, (source, _) in members.items()},
                self._clock() + PAIR_TTL_S,
            )
            return lease_id

    def _check_pair(
        self, lease_id: str, key: str, profile_id: str | None = None, session_id: str | None = None
    ) -> PairLease:
        lease = self._pairs.get(lease_id)
        if (
            lease is None
            or lease.closing
            or lease.expires <= self._clock()
            or lease.generation != self.generation
            or key not in lease.members
            or (profile_id is not None and (lease.profile_id != profile_id or lease.session_id != session_id))
        ):
            raise Forbidden("Multiview lease expired or belongs to another session.", code="multiview_expired")
        return lease

    async def validate_pair(self, lease_id: str, key: str, profile_id: str, session_id: str | None) -> None:
        async with self._hls_lock:
            lease = self._check_pair(lease_id, key, profile_id, session_id)
            lease.expires = self._clock() + PAIR_TTL_S

    async def _release_pair_locked(self, lease_id: str) -> None:
        lease = self._pairs.get(lease_id)
        if lease is None:
            return
        lease.closing = True
        for key in lease.members:
            await self._stop_hls(key)
        self._pairs.pop(lease_id, None)

    async def release_pair(self, lease_id: str, profile_id: str, session_id: str | None) -> None:
        async with self._hls_lock:
            lease = self._pairs.get(lease_id)
            if lease is None:
                return
            if lease.profile_id != profile_id or lease.session_id != session_id:
                raise Forbidden("Multiview lease belongs to another session.")
            await self._release_pair_locked(lease_id)

    def active_viewers(self, source_id: str) -> list[Viewer]:
        """Whose streams hold this source's connections right now, longest-running first."""
        now = self._clock()
        found: list[Viewer] = []
        for key, hls in self._hls.items():
            if hls.source_id == source_id and not hls.complete:
                # A kept session holds its connection whether or not anyone polled it lately.
                kept = key in self._kept
                found += [viewer for viewer, seen in hls.viewers.values() if kept or now - seen <= HLS_IDLE_S]
        found += [s.viewer for s in self._http if s.source_id == source_id and s.viewer is not None]
        for up in self._shared.values():
            if up.source_id == source_id:
                found += up.viewers.values()
        found += [s.viewer for s in self._captures if s.source_id == source_id and s.viewer is not None]
        # A player opens several connections for one title (ranges, segments): name it once. A watch
        # room or a recording of the same content is named apart, by the title it was given.
        unique: dict[tuple[str, str | None, str | None], Viewer] = {}
        for viewer in sorted(found, key=lambda v: v.since):
            unique.setdefault((viewer.profile_id, viewer.ref or viewer.title, viewer.title), viewer)
        return list(unique.values())

    def user_viewers(self, user_id: str) -> list[Viewer]:
        """The sessions this proxy is running for one user, on any source."""
        found: list[Viewer] = []
        for hls in self._hls.values():
            found += [viewer for viewer, _ in hls.viewers.values()]
        found += [s.viewer for s in self._http if s.viewer is not None]
        for up in self._shared.values():
            found += up.viewers.values()
        return [viewer for viewer in found if viewer.user_id == user_id]

    def refused_at_limit(self, source_id: str, maximum: int | None) -> bool:
        """Was a stream the source just refused its last free connection, with other streams on the rest?

        Then the limit is the likely cause: something this server does not track holds a connection.
        Without another stream of ours the refusal says nothing about the limit (a source that
        allows one connection refuses for every other reason too) and stays an upstream error.
        """
        others = self._source_use(source_id)
        return bool(maximum) and others >= 1 and others + 1 >= (maximum or 0)

    def _admit_source(self, source_id: str | None, maximum: int | None) -> None:
        if maximum and maximum > 0 and self._source_use(source_id) >= maximum:
            raise Conflict("Source connection limit is occupied.", code="source_busy")

    async def _evict_idle_hls(self, source_id: str | None, capacity: int, now: float) -> bool:
        """Caller holds _hls_lock. Stop this source's HLS sessions nobody polls any more; True if still full."""
        peers = [(k, s) for k, s in self._hls.items() if source_id and s.source_id == source_id and not s.complete]
        full = self._source_use(source_id) >= capacity
        # A long target duration can mean legitimately slow playlist polling.
        # Protect it rather than assuming every five-second gap is abandonment.
        for peer_key, peer in sorted(peers, key=lambda pair: pair[1].last_access):
            if not full:
                break
            grace = HLS_SWITCH_GRACE_S
            playlist = peer.workdir / "index.m3u8"
            if playlist.exists():
                for line in playlist.read_text(errors="ignore").splitlines():
                    if line.startswith("#EXT-X-TARGETDURATION:"):
                        with contextlib.suppress(ValueError):
                            grace = max(grace, float(line.split(":", 1)[1]) + 2)
                        break
            reserved = peer_key in self._kept or any(peer_key in lease.members for lease in self._pairs.values())
            if not reserved and not self.dvr.held(peer_key) and now - peer.last_access >= grace:
                await self._stop_hls(peer_key, expected=peer)
                self._forget_pin(peer)
                full = self._source_use(source_id) >= capacity
        return full

    @contextlib.asynccontextmanager
    async def probe_slot(self, source_id: str, max_connections: int | None) -> AsyncIterator[None]:
        """Hold one of the source's connections for a media probe, admitted as a stream would be."""
        slot = object()
        async with self._hls_lock:
            self.ensure_running()
            capacity = max_connections or 0
            if capacity > 0 and self._source_use(source_id) >= capacity:
                await self._evict_idle_hls(source_id, capacity, self._clock())
            self._admit_source(source_id, max_connections)
            self._probes[slot] = source_id
        try:
            yield
        finally:
            self._probes.pop(slot, None)

    # ------------------------------------------------------------------ recordings
    def tap_shared(self, key: str, viewer: Viewer | None = None) -> Tap | None:
        """A recording's seat on the channel's shared upstream, when viewers have one open right now."""
        up = self._shared.get(key)
        if up is None or up.closed or up.failed or not up.subscribers:
            return None
        return up.tap(viewer)

    async def capture_slot(self, source_id: str, max_connections: int | None, viewer: Viewer | None) -> CaptureSlot:
        """Hold one of the source's connections for a recording, admitted as a stream would be."""
        async with self._hls_lock:
            self.ensure_running()
            capacity = max_connections or 0
            if capacity > 0 and self._source_use(source_id) >= capacity:
                await self._evict_idle_hls(source_id, capacity, self._clock())
            self._admit_source(source_id, max_connections)
            slot = CaptureSlot(source_id, viewer)
            self._captures.add(slot)
        return slot

    def release_capture(self, slot: CaptureSlot) -> None:
        self._captures.discard(slot)

    # ------------------------------------------------------------------ pinned upstream sessions
    def _pin_held(self, key: str, now: float) -> str | None:
        held = self._pins.get(key)
        if held is None:
            return None
        # A film outlasts the TTL: a session that is still running keeps its pin, so the seek that
        # replaces it reaches the upstream session it already holds.
        in_use = any(s.pin_key == key for s in self._hls.values())
        return held[0] if held[1] > now or in_use else None

    async def pin(self, key: str, url: str, headers: dict[str, str], *, stale: str | None = None) -> str:
        """The URL one playback (``key``) keeps using for ``url``, resolved upstream once.

        ``stale`` is a pinned URL the upstream stopped honouring; it is resolved again, unless
        another request of the same playback already did that.
        """
        now = self._clock()
        held = self._pin_held(key, now)
        if held is not None and held != stale:
            if held != url:
                self._pins[key] = (held, now + PIN_TTL_S)
            return held
        running = self._pinning.get(key)
        if running is None:
            running = asyncio.ensure_future(self._resolve_pin(key, url, headers))
            self._pinning[key] = running
            running.add_done_callback(lambda _: self._pinning.pop(key, None))
        # Shielded: another request of the same playback may be waiting on this answer.
        return await asyncio.shield(running)

    async def _resolve_pin(self, key: str, url: str, headers: dict[str, str]) -> str:
        async with self._client_factory(timeout=PIN_TIMEOUT_S) as client:
            pinned = await pin_upstream_session(client, url, headers)
        now = self._clock()
        self._pins.pop(key, None)
        for other in [k for k in self._pins if self._pin_held(k, now) is None]:
            del self._pins[other]
        while len(self._pins) >= MAX_PINS:
            del self._pins[min(self._pins, key=lambda k: self._pins[k][1])]
        self._pins[key] = (pinned, now + (PIN_TTL_S if pinned != url else PIN_MISS_TTL_S))
        return pinned

    def _forget_pin(self, sess: HlsSession) -> None:
        """A reaped session takes its pin with it, unless the viewer's next session already uses it."""
        if sess.pin_key and not any(s.pin_key == sess.pin_key for s in self._hls.values()):
            self._pins.pop(sess.pin_key, None)

    # ------------------------------------------------------------------ passthrough
    def _drop_shared(self, key: str) -> None:
        self._shared.pop(key, None)

    async def live_shared(
        self,
        key: str,
        url: str,
        headers: dict[str, str],
        source_id: str,
        *,
        generation: int | None = None,
        max_connections: int | None = None,
        viewer: Viewer | None = None,
    ) -> tuple[str, AsyncIterator[bytes]]:
        self.ensure_running(generation)
        generation = self.generation
        async with self._hls_lock:
            self.ensure_running(generation)
            up = self._shared.get(key)
            if up is None:
                self._admit_source(source_id, max_connections)
                up = SharedUpstream(key, url, headers, self._client_factory, self._drop_shared)
                up.source_id = source_id
                self._shared[key] = up
            if up.closed:
                raise Conflict("Previous source connection is still closing.", code="source_busy")
            q = up.subscribe()
            if viewer is not None:
                up.viewers[q] = viewer
        try:
            await up.wait_ready()
            self.ensure_running(generation)
        except BaseException:
            up.unsubscribe(q)
            raise
        if up.failed:
            up.unsubscribe(q)
            status = up.failed.removeprefix("upstream ")
            raise UpstreamRefused(status) if status.isdigit() else ConnectionError(up.failed)

        async def gen() -> AsyncIterator[bytes]:
            self.ensure_running(generation)
            self._on_start(source_id)
            self._viewers[q] = (up, source_id)
            try:
                while True:
                    item = await q.get()
                    if item is None:
                        break
                    yield item
            finally:
                up.unsubscribe(q)
                self._finish_viewer(q)

        async def close_viewer() -> None:
            up.unsubscribe(q)
            self._finish_viewer(q)

        return up.content_type, OwnedStream(gen(), close_viewer)

    async def passthrough(
        self,
        url: str,
        headers: dict[str, str],
        source_id: str,
        range_header: str | None = None,
        *,
        live: bool = False,
        generation: int | None = None,
        max_connections: int | None = None,
        viewer: Viewer | None = None,
        pin_key: str | None = None,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        """Single-client passthrough (VOD with Range support; live with reconnect).

        ``pin_key`` names one viewer's playback of a file: its Range requests share one pinned
        upstream session (``pin``) and replace each other instead of overlapping.
        """
        self.ensure_running(generation)
        generation = self.generation
        req_headers = dict(headers)
        if range_header:
            req_headers["Range"] = range_header
        async with self._hls_lock:
            self.ensure_running(generation)
            if pin_key:
                # A seek is a new Range request, and it can arrive before the player's previous one
                # is seen to disconnect. Upstream that would be two transfers for one viewer, and
                # here a refusal at the source's limit: the previous response is closed first.
                for previous in [s for s in self._http if s.pin_key == pin_key]:
                    await self._close_http(previous)
            self._admit_source(source_id, max_connections)
            client = self._client_factory(timeout=httpx.Timeout(30.0, connect=10.0, read=None), headers=req_headers)
            sess = HttpSession(client, asyncio.current_task(), source_id=source_id, viewer=viewer, pin_key=pin_key)
            self._http.add(sess)
        try:
            target = await self.pin(pin_key, url, headers) if pin_key else url
            resp = await client.send(client.build_request("GET", target), stream=True)
            sess.response = resp
            if pin_key and target != url and str(resp.status_code) in PIN_EXPIRED:
                # The panel no longer knows the pinned session: ask for a new one, once.
                await resp.aclose()
                target = await self.pin(pin_key, url, headers, stale=target)
                resp = await client.send(client.build_request("GET", target), stream=True)
                sess.response = resp
            self.ensure_running(generation)
        except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
            # Accepted, then closed without an answer: how some panels turn a connection away.
            await self._close_http(sess)
            raise UpstreamRefused(None) from exc
        except BaseException:
            await self._close_http(sess)
            raise
        if resp.status_code >= 400:
            await self._close_http(sess)
            raise UpstreamRefused(str(resp.status_code))
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() in HOP_HEADERS}
        status = resp.status_code

        async def gen() -> AsyncIterator[bytes]:
            self.ensure_running(generation)
            sess.task = asyncio.current_task()
            self._on_start(source_id)
            sess.counted = True
            nonlocal resp
            attempt = 0
            try:
                while True:
                    self.ensure_running(generation)
                    try:
                        async for chunk in resp.aiter_bytes(CHUNK):
                            attempt = 0
                            yield chunk
                        if not live:
                            return
                    except (httpx.TransportError, httpx.StreamError):
                        if not live:
                            return
                    await resp.aclose()
                    if attempt >= 8:
                        return
                    await asyncio.sleep(_backoff(attempt))
                    attempt += 1
                    try:
                        resp = await client.send(client.build_request("GET", url), stream=True)
                        sess.response = resp
                    except httpx.HTTPError:
                        continue
            finally:
                await self._close_http(sess)

        return status, out_headers, OwnedStream(gen(), lambda: self._close_http(sess))

    async def fetch_text(
        self,
        url: str,
        headers: dict[str, str],
        *,
        generation: int | None = None,
        source_id: str | None = None,
        max_connections: int | None = None,
    ) -> tuple[str, str]:
        self.ensure_running(generation)
        generation = self.generation
        async with self._hls_lock:
            self.ensure_running(generation)
            self._admit_source(source_id, max_connections)
            client = self._client_factory(timeout=20.0, headers=headers)
            sess = HttpSession(client, asyncio.current_task(), source_id=source_id)
            self._http.add(sess)
        try:
            resp = await client.get(url)
            sess.response = resp
            self.ensure_running(generation)
            if resp.status_code >= 400:
                raise UpstreamRefused(str(resp.status_code))
            return str(resp.url), resp.text
        finally:
            await self._close_http(sess)

    @staticmethod
    def rewrite_playlist(text: str, base_url: str, make_url: Callable[[str], str]) -> str:
        """Rewrite every URI in an HLS playlist so it flows through the proxy."""
        import re

        out = []
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(make_url(urljoin(base_url, s)))
            elif 'URI="' in s:
                out.append(re.sub(r'URI="([^"]+)"', lambda m: f'URI="{make_url(urljoin(base_url, m.group(1)))}"', line))
            else:
                out.append(line)
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------ HLS remux
    @staticmethod
    def session_id(token_key: str) -> str:
        return hashlib.sha1(token_key.encode(), usedforsecurity=False).hexdigest()[:20]

    async def _drain_hls_stderr(self, sess: HlsSession) -> None:
        # Never retain/log raw ffmpeg stderr: it may contain upstream credentials.
        # The one thing kept is the HTTP status of a refused upstream request, three characters.
        tail = b""
        if sess.proc.stderr is not None:
            while chunk := await sess.proc.stderr.read(4096):
                sess.stderr_bytes = min(1_000_000, sess.stderr_bytes + len(chunk))
                if sess.upstream_status is None:
                    found = UPSTREAM_STATUS.search(tail + chunk)
                    if found is not None:
                        sess.upstream_status = found.group(1).decode()
                    tail = chunk[-32:]

    async def _spawn_hls(
        self,
        key: str,
        sid: str,
        workdir: Path,
        attempts: list[tuple[str, list[str]]],
        *,
        source_id: str | None,
        buffered: bool = False,
        family: str | None = None,
        pin_key: str | None = None,
    ) -> HlsSession:
        """Caller holds _hls_lock. Start ``attempts[0]``; the rest stay on the session as fallbacks."""
        (label, args), *fallbacks = attempts

        def owned(proc: asyncio.subprocess.Process) -> HlsSession:
            sess = HlsSession(
                sid=sid,
                workdir=workdir,
                proc=proc,
                source_id=source_id,
                last_access=self._clock(),
                family=family,
                label=label,
                fallbacks=fallbacks,
                pin_key=pin_key,
            )
            sess.stderr_task = asyncio.create_task(self._drain_hls_stderr(sess))
            self._hls[key] = sess
            return sess

        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        )
        try:
            proc = await asyncio.shield(spawn)
        except BaseException:
            # Cancellation can race subprocess creation. Reconcile the owned
            # spawn before releasing its slot or removing its output directory.
            try:
                created = await spawn
            except Exception:
                if buffered:
                    self.dvr.stopped(key)
                    shutil.rmtree(workdir)
            else:
                # Failure to confirm termination retains the occupied slot.
                await self._stop_hls(key, expected=owned(created))
            raise
        return owned(proc)

    async def hls_session(
        self,
        key: str,
        url: str,
        headers: dict[str, str],
        *,
        live: bool,
        transcode: str,
        source_id: str | None = None,
        max_connections: int | None = None,
        generation: int | None = None,
        buffered: bool = False,
        pair_lease_id: str | None = None,
        vod: VodPlan | None = None,
        viewer: Viewer | None = None,
        pin_key: str | None = None,
        sid: str | None = None,
    ) -> HlsSession:
        """The running session for ``key``, started if there is none.

        ``sid`` names the session's directory and segment URLs when the key alone must not: a
        session that is stopped and started again under one key (a watch room after a seek) gets
        a new one each time, so no client mistakes the new segments for the old.
        """
        self.ensure_running(generation)
        generation = self.generation
        # ffmpeg asks for its input again on every seek inside the file (a Matroska index sits at
        # the end), so a file is opened by the URL of the upstream session this playback holds.
        source_url = url
        if pin_key:
            url = await self.pin(pin_key, source_url, headers)

        def attempts_for(target: str, outdir: Path) -> list[tuple[str, list[str]]]:
            if vod is not None:
                return vod_ffmpeg_attempts(self.ffmpeg, target, headers, outdir, vod, vaapi_device=self.vaapi_device)
            legacy = ffmpeg_args(
                self.ffmpeg,
                target,
                headers,
                outdir,
                live=live,
                transcode=transcode,
                vaapi_device=self.vaapi_device,
                buffered=buffered,
            )
            return [("", legacy)]

        repinned = False
        capacity = max_connections or 0
        admission_deadline = self._clock() + HLS_ADMISSION_WAIT_S
        while True:
            async with self._hls_lock:
                self.ensure_running(generation)
                now = self._clock()
                if pair_lease_id:
                    lease = self._check_pair(pair_lease_id, key)
                    if lease.members[key] != source_id or capacity < 1 or self._source_use(source_id) > capacity:
                        raise Conflict("Provider capacity changed before startup.", code="multiview_capacity")
                elif any(key in lease.members for lease in self._pairs.values()):
                    raise Conflict("Channel is reserved by a multiview session.", code="source_busy")
                if vod is not None:
                    # A seek or an audio switch restarts this viewer's session: free its connection now.
                    for other_key, other in list(self._hls.items()):
                        if other_key != key and other.family == vod.family:
                            await self._stop_hls(other_key, expected=other)
                sess = self._hls.get(key)
                if sess is not None and (sess.proc.returncode is None or sess.complete):
                    if buffered and self.dvr.key != key:
                        raise Conflict(
                            "Live session already started without a buffer; restart it explicitly.",
                            code="buffer_session_active",
                        )
                    sess.last_access = now
                    break  # Same channel shares its process, including during startup.
                if sess is not None:
                    await self._stop_hls(key, expected=sess)
                peers = [(k, s) for k, s in self._hls.items() if source_id and s.source_id == source_id]
                for peer_key, peer in peers:
                    if peer.proc.returncode is not None and not peer.complete:
                        await self._stop_hls(peer_key, expected=peer)
                full = not pair_lease_id and capacity > 0 and self._source_use(source_id) >= capacity
                if full:
                    full = await self._evict_idle_hls(source_id, capacity, now)
                if not full:
                    name = sid or self.session_id(key)
                    if buffered:
                        if not live or transcode != "none":
                            raise Conflict(
                                "DVR requires an existing live codec-copy HLS grant.", code="buffer_unsupported"
                            )
                        workdir = self.dvr.reserve(key, name)
                    else:
                        workdir = self.transcode_dir / name
                        shutil.rmtree(workdir, ignore_errors=True)
                        workdir.mkdir(parents=True, exist_ok=True)
                    sess = await self._spawn_hls(
                        key,
                        name,
                        workdir,
                        attempts_for(url, workdir),
                        source_id=source_id,
                        buffered=buffered,
                        family=vod.family if vod is not None else None,
                        pin_key=pin_key,
                    )
                    break
                if now >= admission_deadline:
                    raise HlsSourceBusy("Source connection limit is occupied by another active channel")
            # Crucially release the lock so the existing viewer can refresh its lease.
            await self._sleep(min(0.25, max(0, admission_deadline - self._clock())))

        if viewer is not None:
            first = sess.viewers.get(viewer.profile_id)
            sess.viewers[viewer.profile_id] = (first[0] if first else viewer, self._clock())
        deadline = self._clock() + HLS_READY_TIMEOUT
        playlist = sess.workdir / "index.m3u8"
        while self._clock() < deadline:
            self.ensure_running(generation)
            current = self._hls.get(key)
            if current is not sess:
                # Another request for this session may already have moved it to its next encoder.
                if current is None or sess.family is None or current.sid != sess.sid:
                    raise ConnectionError("HLS session stopped")
                sess = current
            sess.last_access = self._clock()  # An outstanding startup request is active.
            if playlist.exists() and "#EXTINF" in playlist.read_text(errors="ignore"):
                return sess
            if sess.proc.returncode is not None:
                code = sess.proc.returncode
                if sess.stderr_task is not None:
                    # Let the drain read ffmpeg's last words: an upstream refusal is not an encoder failure.
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(asyncio.shield(sess.stderr_task), 0.5)
                if pin_key and url != source_url and not repinned and sess.upstream_status in PIN_EXPIRED:
                    # The panel no longer knows the pinned session: ask for a new one and start again, once.
                    repinned = True
                    async with self._hls_lock:
                        await self._stop_hls(key, expected=sess)
                    url = await self.pin(pin_key, source_url, headers, stale=url)
                    async with self._hls_lock:
                        self.ensure_running(generation)
                        if key not in self._hls:  # else another request of this playback restarted it
                            self._admit_source(source_id, max_connections)
                            sess.workdir.mkdir(parents=True, exist_ok=True)
                            watching = sess.viewers
                            fresh = attempts_for(url, sess.workdir)
                            tried = next((i for i, (label, _) in enumerate(fresh) if label == sess.label), 0)
                            sess = await self._spawn_hls(
                                key,
                                sess.sid,
                                sess.workdir,
                                fresh[tried:],
                                source_id=source_id,
                                family=sess.family,
                                pin_key=pin_key,
                            )
                            sess.viewers = watching
                    deadline = self._clock() + HLS_READY_TIMEOUT
                    continue
                async with self._hls_lock:
                    # Exit 0 with nothing written is an empty input, not an encoder that failed to start.
                    retry = code != 0 and bool(sess.fallbacks) and sess.upstream_status is None
                    retry = retry and self._hls.get(key) is sess
                    await self._stop_hls(key, expected=sess)
                    if retry:
                        self.ensure_running(generation)
                        log.warning(
                            "transcode fallback", extra={"from": sess.label, "to": sess.fallbacks[0][0], "code": code}
                        )
                        sess.workdir.mkdir(parents=True, exist_ok=True)
                        watching = sess.viewers
                        sess = await self._spawn_hls(
                            key,
                            sess.sid,
                            sess.workdir,
                            sess.fallbacks,
                            source_id=source_id,
                            family=sess.family,
                            pin_key=pin_key,
                        )
                        sess.viewers = watching
                        deadline = self._clock() + HLS_READY_TIMEOUT
                        continue
                if sess.upstream_status is not None:
                    raise UpstreamRefused(sess.upstream_status)
                raise ConnectionError(f"ffmpeg exited (code {code}; stderr bytes {sess.stderr_bytes})")
            await self._sleep(0.25)
        async with self._hls_lock:
            await self._stop_hls(key, expected=sess)
        raise TimeoutError("hls not ready")

    def hls_by_sid(self, sid: str) -> HlsSession | None:
        for s in self._hls.values():
            if s.sid == sid:
                s.last_access = self._clock()
                return s
        return None

    def keep(self, key: str) -> None:
        """Do not reap ``key``'s session for idleness, nor give its connection to another viewer."""
        self._kept.add(key)

    async def stop_hls(self, key: str, *, forget: bool = False) -> None:
        """Stop one session by key. ``forget`` also ends its keep and drops the upstream session it pinned."""
        async with self._hls_lock:
            sess = self._hls.get(key)
            await self._stop_hls(key)
            if forget:
                self._kept.discard(key)
                if sess is not None:
                    self._forget_pin(sess)

    def hls_running(self, key: str) -> HlsSession | None:
        """``key``'s session while it is usable: ffmpeg running, or done with the whole file."""
        sess = self._hls.get(key)
        return sess if sess is not None and (sess.proc.returncode is None or sess.complete) else None

    async def _stop_hls(self, key: str, *, expected: HlsSession | None = None) -> None:
        """Caller holds _hls_lock, including throughout process termination."""
        sess = self._hls.get(key)
        if sess is None or (expected is not None and sess is not expected):
            return
        if sess.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                sess.proc.terminate()
            try:
                await asyncio.wait_for(sess.proc.wait(), HLS_STOP_TIMEOUT_S)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    sess.proc.kill()
                await asyncio.wait_for(sess.proc.wait(), HLS_STOP_TIMEOUT_S)
                # A timeout leaves the slot occupied; never admit while an old upstream lives.
        self._hls.pop(key, None)
        if sess.stderr_task is not None:
            sess.stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sess.stderr_task
        try:
            shutil.rmtree(sess.workdir)
        except FileNotFoundError:
            pass
        except OSError:
            self._cleanup_pending.add(sess.workdir)
        finally:
            self.dvr.stopped(key)

    async def buffer_lease(
        self,
        key: str,
        profile: str,
        url: str,
        headers: dict[str, str],
        *,
        source_id: str,
        max_connections: int | None,
        generation: int | None,
        viewer: Viewer | None = None,
    ) -> dict[str, Any]:
        if not self.dvr.enabled:
            raise Conflict("Local recording is disabled.", code="recording_disabled")
        if self.dvr.pending >= 16:
            raise Conflict("DVR admission limit reached.", code="buffer_busy")
        self.dvr.pending += 1
        acquired = False
        lease = None
        try:
            sess = await self.hls_session(
                key,
                url,
                headers,
                live=True,
                transcode="none",
                source_id=source_id,
                max_connections=max_connections,
                generation=generation,
                buffered=True,
                viewer=viewer,
            )
            lease = self.dvr.join(key, profile)
            window = self.dvr.window(key)
            acquired = True
            return {"lease_id": lease, "session_id": sess.sid, "window": window}
        except BaseException:
            if lease is not None:
                self.dvr.leave(key, profile, lease)
            raise
        finally:
            self.dvr.pending -= 1
            if not acquired and not self.dvr.pending and not self.dvr.leases and self.dvr.key == key:
                async with self._hls_lock:
                    await self._stop_hls(key)

    async def buffer_release(self, key: str, profile: str, lease: str) -> None:
        async with self._hls_lock:
            self.dvr.leave(key, profile, lease)
            if self.dvr.key == key and not self.dvr.held(key):
                await self._stop_hls(key)

    async def buffer_window(self, key: str) -> dict[str, Any]:
        async with self._hls_lock:
            try:
                return self.dvr.window(key)
            except (ServiceError, OSError, ValueError):
                await self._stop_hls(key)
                raise

    async def buffer_manifest(self, key: str, at: Any = None) -> str:
        async with self._hls_lock:
            try:
                return self.dvr.manifest(key, at)
            except ServiceError as exc:
                if exc.code != "buffer_expired":
                    await self._stop_hls(key)
                raise
            except (OSError, ValueError):
                await self._stop_hls(key)
                raise
