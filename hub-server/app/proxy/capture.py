"""Capture: a recording's bytes, written by a codec-copy ffmpeg to a Matroska file.

Where the bytes come from:

- **Shared upstream.** When viewers have the channel's ``SharedUpstream`` open, the recording
  subscribes to it (``Tap``) and its bytes are piped into ``ffmpeg -i pipe:0``: no second source
  connection. The tap is a subscriber like any other, so the upstream stays open for it when the
  last viewer leaves; the recording has then taken the connection over, and later viewers of the
  channel join it. A recording that cannot keep up is dropped by the upstream and ends there.
- **Its own connection.** Otherwise the recording is admitted against the source like a viewer
  (``SourceBusy`` when no connection is free) and ffmpeg reads the stream URL itself, reconnecting
  as a live HLS session does.

Stopping finalises the file, so it has a duration and cues: the piped ffmpeg sees end of input,
the other one is interrupted (SIGINT, exit code 255). The file is written under a hidden temporary
name inside the recordings directory and moved to ``recording_filename`` when the capture ends,
never over an existing file. Free space is checked every ``SPACE_CHECK_S`` while capturing.

ffmpeg's stderr is discarded: it can contain the stream URL and with it the source's credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.domain.entities import ProgrammeInfo, StreamTarget
from app.domain.ports import LiveProvider
from app.infra.models import Channel, Profile, Recording, utcnow
from app.proxy.stream_proxy import CaptureSlot, StreamProxy, Tap, Viewer, _upstream_args
from app.services.errors import ServiceError
from app.services.recorder import CaptureResult, SourceBusy, recording_filename
from app.services.rewind import archive_window
from app.services.streams import StreamRecord
from app.services.tiles import row_to_channel

log = logging.getLogger(__name__)

SPACE_CHECK_S = 30.0
# How long ffmpeg gets to write the file's index after its input ended or it was interrupted.
FINALIZE_TIMEOUT_S = 15.0
KILL_TIMEOUT_S = 5.0
# A missed programme's archive fetch waits this long for a free source connection.
ARCHIVE_ADMIT_WAIT_S = 600.0
ARCHIVE_ADMIT_EVERY_S = 30.0
ARCHIVE_MARGIN_S = 600.0
# How ffmpeg ends when it was interrupted and finished its file.
INTERRUPTED = 255
SLOW = "The recording could not keep up with the stream."
OUTPUT = ["-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-f", "matroska", "-y"]


def pipe_args(ffmpeg: str, out: Path, *, mpegts: bool = True) -> list[str]:
    """ffmpeg reading a shared upstream's bytes from its stdin: MPEG-TS, unless the upstream said otherwise."""
    demux = ["-f", "mpegts"] if mpegts else []
    start = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-fflags", "+genpts"]
    return [*start, *demux, "-i", "pipe:0", *OUTPUT, str(out)]


def url_args(ffmpeg: str, url: str, headers: dict[str, str], out: Path, *, live: bool) -> list[str]:
    """ffmpeg reading the stream URL itself, as the proxy's own requests look; ``live`` reconnects at end of input."""
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *_upstream_args(url, headers)]
    if live and url.startswith("http"):
        args += ["-reconnect_at_eof", "1"]
    return [*args, "-fflags", "+genpts", "-i", url, *OUTPUT, str(out)]


@dataclass(eq=False)
class _Run:
    recording_id: str
    tmp: Path
    proc: asyncio.subprocess.Process
    viewer: Viewer
    record: StreamRecord
    tap: Tap | None = None
    slot: CaptureSlot | None = None
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    error: str | None = None
    stopping: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def holds_connection(self) -> bool:
        """The recording is why a source connection is in use: its own, or one its viewers have left to it."""
        return self.slot is not None or (self.tap is not None and self.tap.alone)


class Capture:
    """The ``CaptureBackend`` of ``RecorderService``."""

    def __init__(
        self,
        proxy: StreamProxy,
        sources: Any,
        db: Any,
        *,
        recordings_dir: str | Path,
        ffmpeg: str = "ffmpeg",
        min_free_gb: int = 50,
        free_bytes: Callable[[], int] | None = None,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        tz: tzinfo | None = None,
    ) -> None:
        self._proxy = proxy
        self._sources = sources
        self._db = db
        self.root = Path(recordings_dir)
        self.ffmpeg = ffmpeg
        self.min_free_gb = min_free_gb
        self._free_bytes = free_bytes
        self._clock = clock
        self._sleep = sleep
        self._tz = tz
        self._runs: dict[str, _Run] = {}

    # ------------------------------------------------------------------ what is being captured
    def is_running(self, recording_id: str) -> bool:
        run = self._runs.get(recording_id)
        return run is not None and not run.stopping and run.proc.returncode is None

    def user_streams(self, user_id: str) -> list[StreamRecord]:
        """This user's recordings that occupy a source connection by themselves (``StreamRegistry.external``)."""
        return [
            run.record
            for run in self._runs.values()
            if run.record.user_id == user_id and run.proc.returncode is None and run.holds_connection
        ]

    def _tmp(self, recording_id: str, kind: str = "recording") -> Path:
        # Hidden, so a library server watching the directory does not pick up a file in progress.
        return self.root / f".{kind}-{recording_id}.part"

    async def _channel(self, recording: Recording) -> tuple[Any, Any, Channel]:
        parts = recording.channel_id.split(":", 2)
        if len(parts) != 3 or parts[0] != "ch":
            raise ServiceError("This recording's channel is not known any more.", code="channel_gone")
        src, prov = await self._sources.provider_for(parts[1])
        async with self._db.session() as s:
            channel = (
                await s.execute(select(Channel).where(Channel.source_id == parts[1], Channel.ext_id == parts[2]))
            ).scalar_one_or_none()
        if channel is None or not src.enabled or not isinstance(prov, LiveProvider):
            raise ServiceError("This recording's channel is not known any more.", code="channel_gone")
        return src, prov, channel

    async def _owner(self, recording: Recording) -> str:
        async with self._db.session() as s:
            profile = await s.get(Profile, recording.profile_id)
        return str(profile.user_id or "") if profile is not None else ""

    def _tagged(self, recording: Recording, user_id: str) -> tuple[Viewer, StreamRecord]:
        """The recording as a ``source_busy`` answer and its owner's stream list name it."""
        title = f"Recording: {recording.title}"
        viewer = Viewer(recording.profile_id, title, "recording", recording.channel_id, user_id=user_id or None)
        record = StreamRecord(
            user_id, recording.profile_id, "", f"rec:{recording.id}", title, "recording", None, viewer.since, 0.0, 0.0
        )
        return viewer, record

    # ------------------------------------------------------------------ start
    async def start(self, recording: Recording) -> None:
        if recording.id in self._runs:
            return
        src, prov, channel = await self._channel(recording)
        target: StreamTarget = await prov.stream_for_channel(row_to_channel(channel))
        viewer, record = self._tagged(recording, await self._owner(recording))
        tmp = self._tmp(recording.id)
        tap = self._proxy.tap_shared(f"{src.id}:{channel.ext_id}", viewer)
        slot: CaptureSlot | None = None
        try:
            if tap is not None:
                kind = tap.upstream.content_type.lower()
                proc = await asyncio.create_subprocess_exec(
                    *pipe_args(self.ffmpeg, tmp, mpegts="mp2t" in kind or "octet-stream" in kind),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            else:
                try:
                    slot = await self._proxy.capture_slot(src.id, self._sources.max_connections(src), viewer)
                except ServiceError as exc:
                    if exc.code != SourceBusy.code:
                        raise
                    raise SourceBusy(exc.message) from exc
                proc = await asyncio.create_subprocess_exec(
                    *url_args(self.ffmpeg, target.url, dict(target.headers), tmp, live=True),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
        except BaseException:
            if tap is not None:
                tap.close()
            if slot is not None:
                self._proxy.release_capture(slot)
            raise
        run = _Run(recording.id, tmp, proc, viewer, record, tap=tap, slot=slot)
        self._runs[recording.id] = run
        if slot is not None:
            slot.stop = lambda: self._halt(run, "Stopped: an administrator stopped all streams.")
        if tap is not None:
            run.tasks.append(asyncio.create_task(self._feed(run, tap)))
        run.tasks.append(asyncio.create_task(self._watch(run)))
        log.info("capture started", extra={"recording": recording.id, "shared": tap is not None})

    async def _feed(self, run: _Run, tap: Tap) -> None:
        """Pipe the shared upstream's bytes into ffmpeg until the upstream ends, drops us, or the capture stops."""
        stdin = run.proc.stdin
        assert stdin is not None
        try:
            while True:
                chunk = await tap.queue.get()
                if chunk is None:
                    break
                stdin.write(chunk)
                # Waiting here is what fills the tap's queue when the disk is slow; the upstream
                # then drops the recording, so a viewer never waits for it.
                await stdin.drain()
            if tap.dropped and not run.stopping:
                run.error = run.error or SLOW
        except (BrokenPipeError, ConnectionResetError):
            pass  # ffmpeg went away; the watcher reports how
        finally:
            tap.close()
            with contextlib.suppress(Exception):
                stdin.close()

    def _low_space(self) -> bool:
        try:
            free = self._free_bytes() if self._free_bytes is not None else shutil.disk_usage(self.root).free
        except OSError:
            return False
        return free < self.min_free_gb * 1024**3

    async def _watch(self, run: _Run) -> None:
        """End the capture when free space runs low or the upstream dropped it; release what it holds at the end."""
        waiting = asyncio.ensure_future(run.proc.wait())
        try:
            while not waiting.done():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(waiting), SPACE_CHECK_S)
                if waiting.done() or run.stopping:
                    continue
                note = None
                if self._low_space():
                    note = f"Stopped: free space for recordings fell below {self.min_free_gb} GB."
                elif run.tap is not None and run.tap.dropped:
                    note = SLOW  # ffmpeg is not even reading: the feeder never saw the upstream let go
                if note is not None:
                    run.tasks.append(asyncio.create_task(self._halt(run, note)))
            await waiting
        finally:
            if not waiting.done():
                waiting.cancel()
            self._release(run)

    def _release(self, run: _Run) -> None:
        if run.tap is not None:
            run.tap.close()
        if run.slot is not None:
            self._proxy.release_capture(run.slot)

    # ------------------------------------------------------------------ stop
    async def _halt(self, run: _Run, error: str | None = None) -> None:
        """Make ffmpeg finish its file and wait for it. Safe to call more than once."""
        async with run.lock:
            run.stopping = True
            run.error = run.error or error
            proc = run.proc
            if proc.returncode is None:
                if run.tap is not None:
                    # End of input: the feeder (the first task) closes ffmpeg's stdin on its way out.
                    run.tap.close()
                    run.tasks[0].cancel()
                    if proc.stdin is not None:
                        with contextlib.suppress(Exception):
                            proc.stdin.close()
                else:
                    with contextlib.suppress(ProcessLookupError):
                        proc.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(proc.wait(), FINALIZE_TIMEOUT_S)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(proc.wait(), KILL_TIMEOUT_S)
                    run.error = run.error or "The recording's file could not be finalised."
            self._release(run)

    @staticmethod
    def _exit_error(run: _Run) -> str | None:
        code = run.proc.returncode
        clean = (0,) if run.tap is not None else (0, INTERRUPTED)
        if code is None or code in clean:
            return None
        return f"The capture ended with an error (ffmpeg exit code {code})."

    async def stop(self, recording: Recording) -> CaptureResult:
        """Finalise the capture, running or not, and report the file."""
        run = self._runs.pop(recording.id, None)
        error: str | None = None
        if run is not None:
            await self._halt(run)
            await asyncio.gather(*run.tasks, return_exceptions=True)
            error = run.error or self._exit_error(run)
        tmp = run.tmp if run is not None else self._tmp(recording.id)
        return self._keep(recording, tmp, error)

    def _keep(self, recording: Recording, tmp: Path, error: str | None) -> CaptureResult:
        """Move a finished temporary file to its name, never over a file that is already there."""
        try:
            size = tmp.stat().st_size
        except OSError:
            return CaptureResult(error=error)
        if size <= 0:
            tmp.unlink(missing_ok=True)
            return CaptureResult(error=error)
        root = self.root.resolve()
        wanted = (root / recording_filename(recording, self._tz)).resolve()
        if root not in wanted.parents:  # the name is sanitised; this is the last line, not the first
            wanted = root / f"{recording.id}.mkv"
        try:
            wanted.parent.mkdir(parents=True, exist_ok=True)
            final, n = wanted, 2
            while final.exists():
                final = wanted.with_name(f"{wanted.stem} ({n}){wanted.suffix}")
                n += 1
            tmp.rename(final)
        except OSError as exc:
            log.warning("recording not moved into place", extra={"recording": recording.id, "err": type(exc).__name__})
            return CaptureResult(error=error or "The recording could not be moved into place.")
        return CaptureResult(path=str(final.relative_to(root)), size_bytes=size, error=error)

    async def shutdown(self) -> None:
        """Finalise every capture in progress; the recorder marks them when it next starts."""
        for run in list(self._runs.values()):
            with contextlib.suppress(Exception):
                await self._halt(run)
            for task in run.tasks:
                task.cancel()
            await asyncio.gather(*run.tasks, return_exceptions=True)

    # ------------------------------------------------------------------ provider archive
    async def recover_from_archive(self, recording: Recording) -> CaptureResult | None:
        """Fetch an ended programme from the provider archive. None when the channel has none covering it."""
        now = self._clock()
        if recording.ends_at > now:
            return None
        try:
            src, prov, channel = await self._channel(recording)
        except ServiceError:
            return None
        if archive_window(channel, now=now)["mode"] != "provider_archive":
            return None
        if recording.starts_at < now - timedelta(days=channel.catchup_days):
            return None
        programme = ProgrammeInfo(
            channel=channel.epg_id or channel.ext_id,
            start=recording.starts_at,
            stop=recording.ends_at,
            title=recording.title,
        )
        target = await prov.catchup_for_channel(row_to_channel(channel), programme)
        if target is None:
            return None
        viewer, _ = self._tagged(recording, "")
        slot = await self._archive_slot(src, viewer)
        tmp = self._tmp(recording.id, "archive")
        length = (recording.ends_at - recording.starts_at).total_seconds()
        try:
            proc = await asyncio.create_subprocess_exec(
                *url_args(self.ffmpeg, target.url, dict(target.headers), tmp, live=False),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            error: str | None = None
            try:
                # Some archives are served at playback speed: allow the programme's length and a margin.
                code = await asyncio.wait_for(proc.wait(), length + ARCHIVE_MARGIN_S)
                if code != 0:
                    error = f"The archive download ended with an error (ffmpeg exit code {code})."
            except TimeoutError:
                error = "The archive download took too long."
            finally:
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
        finally:
            self._proxy.release_capture(slot)
        if error is not None:
            tmp.unlink(missing_ok=True)
            return CaptureResult(error=error)
        result = self._keep(recording, tmp, None)
        return result if result.path else CaptureResult(error=result.error or "The archive was empty.")

    async def _archive_slot(self, src: Any, viewer: Viewer) -> CaptureSlot:
        waited = 0.0
        while True:
            try:
                return await self._proxy.capture_slot(src.id, self._sources.max_connections(src), viewer)
            except ServiceError as exc:
                if exc.code != SourceBusy.code or waited >= ARCHIVE_ADMIT_WAIT_S:
                    raise
            await self._sleep(ARCHIVE_ADMIT_EVERY_S)
            waited += ARCHIVE_ADMIT_EVERY_S
