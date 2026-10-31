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
import shutil
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from app.infra.logging import redact_url

log = logging.getLogger(__name__)

CHUNK = 64 * 1024
CLIENT_QUEUE = 256  # ~16 MB of backlog per client before it's dropped
LINGER_S = 10.0
MAX_BACKOFF = 15.0
HLS_IDLE_S = 60.0
HLS_READY_TIMEOUT = 20.0
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
        if not self._subs and self._linger is None:
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


def ffmpeg_args(
    ffmpeg: str,
    url: str,
    headers: dict[str, str],
    outdir: Path,
    *,
    live: bool,
    transcode: str,
    vaapi_device: str,
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
    args += [
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "6" if live else "0",
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
    ) -> None:
        self._client_factory = client_factory
        self._shared: dict[str, SharedUpstream] = {}
        self._hls: dict[str, HlsSession] = {}
        self._hls_lock = asyncio.Lock()
        self.transcode_dir = transcode_dir
        self.ffmpeg = ffmpeg
        self.vaapi_device = vaapi_device
        self._on_start = on_start or (lambda _s: None)
        self._on_stop = on_stop or (lambda _s: None)
        self._reaper: asyncio.Task[None] | None = None

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
        for key in list(self._hls):
            await self._stop_hls(key)
        for up in list(self._shared.values()):
            if up._task:
                up._task.cancel()
        self._shared.clear()

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            now = time.monotonic()
            for key, sess in list(self._hls.items()):
                if now - sess.last_access > HLS_IDLE_S or sess.proc.returncode is not None:
                    await self._stop_hls(key)

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
        self, key: str, url: str, headers: dict[str, str], source_id: str
    ) -> tuple[str, AsyncIterator[bytes]]:
        up = self._shared.get(key)
        if up is None:
            up = SharedUpstream(key, url, headers, self._client_factory, self._drop_shared)
            self._shared[key] = up
        q = up.subscribe()
        await up.wait_ready()
        if up.failed:
            up.unsubscribe(q)
            raise ConnectionError(up.failed)

        async def gen() -> AsyncIterator[bytes]:
            self._on_start(source_id)
            try:
                while True:
                    item = await q.get()
                    if item is None:
                        break
                    yield item
            finally:
                up.unsubscribe(q)
                self._on_stop(source_id)

        return up.content_type, gen()

    async def passthrough(
        self,
        url: str,
        headers: dict[str, str],
        source_id: str,
        range_header: str | None = None,
        *,
        live: bool = False,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        """Single-client passthrough (VOD with Range support; live with reconnect)."""
        req_headers = dict(headers)
        if range_header:
            req_headers["Range"] = range_header
        client = self._client_factory(timeout=httpx.Timeout(30.0, connect=10.0, read=None), headers=req_headers)
        try:
            req = client.build_request("GET", url)
            resp = await client.send(req, stream=True)
        except Exception:
            await client.aclose()
            raise
        if resp.status_code >= 400:
            await resp.aclose()
            await client.aclose()
            raise ConnectionError(f"upstream {resp.status_code}")
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() in HOP_HEADERS}
        status = resp.status_code

        async def gen() -> AsyncIterator[bytes]:
            self._on_start(source_id)
            nonlocal resp
            attempt = 0
            try:
                while True:
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
                    except httpx.HTTPError:
                        continue
            finally:
                await resp.aclose()
                await client.aclose()
                self._on_stop(source_id)

        return status, out_headers, gen()

    async def fetch_text(self, url: str, headers: dict[str, str]) -> tuple[str, str]:
        async with self._client_factory(timeout=20.0, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                raise ConnectionError(f"upstream {resp.status_code}")
            return str(resp.url), resp.text

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

    async def hls_session(
        self, key: str, url: str, headers: dict[str, str], *, live: bool, transcode: str
    ) -> HlsSession:
        async with self._hls_lock:
            sess = self._hls.get(key)
            if sess is not None and sess.proc.returncode is None:
                sess.last_access = time.monotonic()
                return sess
            if sess is not None:
                await self._stop_hls(key)
            sid = self.session_id(key)
            workdir = self.transcode_dir / sid
            shutil.rmtree(workdir, ignore_errors=True)
            workdir.mkdir(parents=True, exist_ok=True)
            args = ffmpeg_args(
                self.ffmpeg, url, headers, workdir, live=live, transcode=transcode, vaapi_device=self.vaapi_device
            )
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            sess = HlsSession(sid=sid, workdir=workdir, proc=proc)
            self._hls[key] = sess
        # wait for the first playlist to appear
        deadline = time.monotonic() + HLS_READY_TIMEOUT
        playlist = workdir / "index.m3u8"
        while time.monotonic() < deadline:
            if playlist.exists() and "#EXTINF" in playlist.read_text(errors="ignore"):
                return sess
            if proc.returncode is not None:
                err = b""
                if proc.stderr is not None:
                    err = await proc.stderr.read()
                await self._stop_hls(key)
                detail = redact_url(err.decode(errors="ignore")[-300:]) or "" if err else ""
                raise ConnectionError(f"ffmpeg exited: {detail}".strip(": "))
            await asyncio.sleep(0.25)
        await self._stop_hls(key)
        raise TimeoutError("hls not ready")

    def hls_by_sid(self, sid: str) -> HlsSession | None:
        for s in self._hls.values():
            if s.sid == sid:
                s.last_access = time.monotonic()
                return s
        return None

    async def _stop_hls(self, key: str) -> None:
        sess = self._hls.pop(key, None)
        if sess is None:
            return
        if sess.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                sess.proc.terminate()
            try:
                await asyncio.wait_for(sess.proc.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    sess.proc.kill()
        shutil.rmtree(sess.workdir, ignore_errors=True)
