"""StreamProxy.

- **Passthrough** (``/stream/{token}``): async httpx streaming with upstream headers and
  reconnect-with-backoff for live streams. Live channels share one upstream connection
  fanned out to N clients (``SharedUpstream``), closed 10 s after the last client leaves.
  HLS upstreams have their playlists rewritten so segments also flow through the proxy.
- **HLS remux / transcode** (``/stream/{token}/index.m3u8``): ffmpeg ``-c copy -f hls``
  (or libx264 / VAAPI when enabled) into ``<transcode>/<id>/``; idle sessions are reaped.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from app.infra.logging import redact_url
from app.proxy.dvr import DvrManager
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


class HlsSourceBusy(Exception):
    """Another recently polled HLS channel occupies this source’s connection budget."""


HOP_HEADERS = {"content-type", "content-length", "accept-ranges", "content-range", "last-modified", "etag"}


def _backoff(attempt: int) -> float:
    return float(min(MAX_BACKOFF, 0.5 * (2**attempt)))


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

    @property
    def subscribers(self) -> int:
        return len(self._subs)

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
        if not self.closed and not self._subs and self._linger is None:
            loop = asyncio.get_running_loop()
            self._linger = loop.call_later(LINGER_S, self._close_if_idle)

    def _close_if_idle(self) -> None:
        self._linger = None
        if self._subs:
            return
        if self._task is not None:
            self._task.cancel()
        self._on_close(self.key)

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


@dataclass(eq=False)
class HttpSession:
    client: Any
    task: asyncio.Task[Any] | None
    response: Any = None
    source_id: str | None = None
    counted: bool = False
    close_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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
    if headers:
        ua = next((v for k, v in headers.items() if k.lower() == "user-agent"), None)
        if ua:
            args += ["-user_agent", ua]
        other = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() != "user-agent")
        if other:
            args += ["-headers", other]
    if url.startswith("http"):
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
        if live:
            args += ["-reconnect_at_eof", "1"]
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
    if buffered:
        flags += "+program_date_time+temp_file"
    args += [
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "1800" if buffered else "6" if live else "0",
        "-hls_flags",
        flags,
        "-hls_segment_filename",
        str(outdir / "seg_%05d.ts"),
        str(outdir / "index.m3u8"),
    ]
    return args


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

    def ensure_running(self, generation: int | None = None) -> None:
        if self.paused or (generation is not None and generation != self.generation):
            raise Forbidden(
                "Playback was stopped by an administrator. Wait for playback to be resumed.", code="playback_paused"
            )

    def recovery_status(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "active_sessions": len(self._shared) + len(self._hls) + len(self._http),
            "shared_upstreams": len(self._shared),
            "hls_sessions": len(self._hls),
            "http_sessions": len(self._http),
            "external_sessions_tracked": False,
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
        for path in list(self._cleanup_pending):
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                self._cleanup_pending.discard(path)
            except OSError:
                continue
            else:
                self._cleanup_pending.discard(path)
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

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            async with self._hls_lock:
                now = self._clock()
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
                    if (
                        abandoned
                        or (not held and now - sess.last_access > HLS_IDLE_S)
                        or sess.proc.returncode is not None
                    ):
                        await self._stop_hls(key, expected=sess)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "shared_upstreams": {k: v.subscribers for k, v in self._shared.items()},
            "hls_sessions": len(self._hls),
        }

    # ------------------------------------------------------------------ passthrough
    def _drop_shared(self, key: str) -> None:
        self._shared.pop(key, None)

    async def live_shared(
        self, key: str, url: str, headers: dict[str, str], source_id: str, *, generation: int | None = None
    ) -> tuple[str, AsyncIterator[bytes]]:
        self.ensure_running(generation)
        generation = self.generation
        up = self._shared.get(key)
        if up is None:
            up = SharedUpstream(key, url, headers, self._client_factory, self._drop_shared)
            self._shared[key] = up
        q = up.subscribe()
        await up.wait_ready()
        self.ensure_running(generation)
        if up.failed:
            up.unsubscribe(q)
            raise ConnectionError(up.failed)

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

        return up.content_type, gen()

    async def passthrough(
        self,
        url: str,
        headers: dict[str, str],
        source_id: str,
        range_header: str | None = None,
        *,
        live: bool = False,
        generation: int | None = None,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        """Single-client passthrough (VOD with Range support; live with reconnect)."""
        self.ensure_running(generation)
        generation = self.generation
        req_headers = dict(headers)
        if range_header:
            req_headers["Range"] = range_header
        client = self._client_factory(timeout=httpx.Timeout(30.0, connect=10.0, read=None), headers=req_headers)
        sess = HttpSession(client, asyncio.current_task(), source_id=source_id)
        self._http.add(sess)
        try:
            req = client.build_request("GET", url)
            resp = await client.send(req, stream=True)
            sess.response = resp
            self.ensure_running(generation)
        except BaseException:
            await self._close_http(sess)
            raise
        if resp.status_code >= 400:
            await self._close_http(sess)
            raise ConnectionError(f"upstream {resp.status_code}")
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

        return status, out_headers, gen()

    async def fetch_text(self, url: str, headers: dict[str, str], *, generation: int | None = None) -> tuple[str, str]:
        self.ensure_running(generation)
        generation = self.generation
        client = self._client_factory(timeout=20.0, headers=headers)
        sess = HttpSession(client, asyncio.current_task())
        self._http.add(sess)
        try:
            resp = await client.get(url)
            sess.response = resp
            self.ensure_running(generation)
            if resp.status_code >= 400:
                raise ConnectionError(f"upstream {resp.status_code}")
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
        if sess.proc.stderr is not None:
            while chunk := await sess.proc.stderr.read(4096):
                sess.stderr_bytes = min(1_000_000, sess.stderr_bytes + len(chunk))

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
    ) -> HlsSession:
        self.ensure_running(generation)
        generation = self.generation
        capacity = max_connections or 0
        admission_deadline = self._clock() + HLS_ADMISSION_WAIT_S
        while True:
            async with self._hls_lock:
                self.ensure_running(generation)
                now = self._clock()
                sess = self._hls.get(key)
                if sess is not None and sess.proc.returncode is None:
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
                    if peer.proc.returncode is not None:
                        await self._stop_hls(peer_key, expected=peer)
                peers = [(k, s) for k, s in self._hls.items() if source_id and s.source_id == source_id]
                full = capacity > 0 and len(peers) >= capacity
                if full:
                    # A long target duration can mean legitimately slow playlist polling.
                    # Protect it rather than assuming every five-second gap is abandonment.
                    for peer_key, peer in sorted(peers, key=lambda pair: pair[1].last_access):
                        grace = HLS_SWITCH_GRACE_S
                        playlist = peer.workdir / "index.m3u8"
                        if playlist.exists():
                            for line in playlist.read_text(errors="ignore").splitlines():
                                if line.startswith("#EXT-X-TARGETDURATION:"):
                                    with contextlib.suppress(ValueError):
                                        grace = max(grace, float(line.split(":", 1)[1]) + 2)
                                    break
                        if not self.dvr.held(peer_key) and now - peer.last_access >= grace:
                            await self._stop_hls(peer_key, expected=peer)
                            full = len([s for s in self._hls.values() if s.source_id == source_id]) >= capacity
                            if not full:
                                break
                if not full:
                    sid = self.session_id(key)
                    if buffered:
                        if not live or transcode != "none":
                            raise Conflict(
                                "DVR requires an existing live codec-copy HLS grant.", code="buffer_unsupported"
                            )
                        workdir = self.dvr.reserve(key, sid)
                    else:
                        workdir = self.transcode_dir / sid
                        shutil.rmtree(workdir, ignore_errors=True)
                        workdir.mkdir(parents=True, exist_ok=True)
                    args = ffmpeg_args(
                        self.ffmpeg,
                        url,
                        headers,
                        workdir,
                        live=live,
                        transcode=transcode,
                        vaapi_device=self.vaapi_device,
                        buffered=buffered,
                    )
                    spawn = asyncio.create_task(
                        asyncio.create_subprocess_exec(
                            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
                        )
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
                            abandoned = HlsSession(
                                sid=sid,
                                workdir=workdir,
                                proc=created,
                                source_id=source_id,
                                last_access=self._clock(),
                            )
                            abandoned.stderr_task = asyncio.create_task(self._drain_hls_stderr(abandoned))
                            self._hls[key] = abandoned
                            # Failure to confirm termination retains the occupied slot.
                            await self._stop_hls(key, expected=abandoned)
                        raise
                    sess = HlsSession(
                        sid=sid, workdir=workdir, proc=proc, source_id=source_id, last_access=self._clock()
                    )
                    sess.stderr_task = asyncio.create_task(self._drain_hls_stderr(sess))
                    self._hls[key] = sess
                    break
                if now >= admission_deadline:
                    raise HlsSourceBusy("Source connection limit is occupied by another active channel")
            # Crucially release the lock so the existing viewer can refresh its lease.
            await self._sleep(min(0.25, max(0, admission_deadline - self._clock())))

        deadline = self._clock() + HLS_READY_TIMEOUT
        playlist = sess.workdir / "index.m3u8"
        while self._clock() < deadline:
            self.ensure_running(generation)
            if self._hls.get(key) is not sess:
                raise ConnectionError("HLS session stopped")
            sess.last_access = self._clock()  # An outstanding startup request is active.
            if playlist.exists() and "#EXTINF" in playlist.read_text(errors="ignore"):
                return sess
            if sess.proc.returncode is not None:
                code = sess.proc.returncode
                async with self._hls_lock:
                    await self._stop_hls(key, expected=sess)
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
