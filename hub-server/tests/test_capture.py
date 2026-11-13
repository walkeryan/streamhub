"""Capture: a recording fed from the channel's shared upstream, or admitted on a connection of its own.

ffmpeg is a stub that keeps its arguments, writes a few bytes where it was told to and ends the
way the real one does (0 at end of input, 255 when interrupted). The upstream is respx-mocked.
The one test that runs the real binaries is in ``test_capture_ffmpeg.py``.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.infra.models import Channel, Recording, Source
from app.infra.settings import AppSettings
from app.proxy import capture as capture_module
from app.proxy import stream_proxy
from app.proxy.capture import Capture, pipe_args, url_args
from app.proxy.stream_proxy import Viewer
from app.services import ids
from app.services.recorder import SourceBusy, programme_key, recording_filename
from tests.conftest import Api
from tests.test_epg_integrity import add_source

TS = b"\x47" + b"\x00" * 187


class Stdin:
    def __init__(self, proc: Ffmpeg) -> None:
        self.proc = proc
        self.data = bytearray()
        self.open = asyncio.Event()  # cleared by a test to make ffmpeg stop reading
        self.open.set()

    def write(self, chunk: bytes) -> None:
        self.data += chunk

    async def drain(self) -> None:
        await self.open.wait()

    def close(self) -> None:
        self.proc.end(0)  # end of input: the file is finished and ffmpeg leaves


class Ffmpeg:
    """A stub ffmpeg process."""

    def __init__(self, args: list[str], piped: bool) -> None:
        self.args = args
        self.out = Path(args[-1])
        self.returncode: int | None = None
        self.signals: list[int] = []
        self.done = asyncio.Event()
        self.stdin = Stdin(self) if piped else None
        self.out.write_bytes(b"matroska")

    def end(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
            if code in (0, 255):
                self.out.write_bytes(self.out.read_bytes() + b"+cues")
            self.done.set()

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.end(255)

    def kill(self) -> None:
        self.end(-9)

    async def wait(self) -> int | None:
        await self.done.wait()
        return self.returncode


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[Ffmpeg]:
    started: list[Ffmpeg] = []

    async def spawn(*args: Any, **kwargs: Any) -> Ffmpeg:
        started.append(Ffmpeg(list(args), kwargs.get("stdin") == asyncio.subprocess.PIPE))
        return started[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return started


class Live(httpx.AsyncByteStream):
    """A channel that keeps sending transport packets until it is closed."""

    opened = 0

    def __init__(self) -> None:
        self.closed = False
        Live.opened += 1

    async def __aiter__(self) -> Any:
        while not self.closed:
            yield TS * 400
            await asyncio.sleep(0.001)

    async def aclose(self) -> None:
        self.closed = True


class Rig:
    def __init__(self, api: Api, root: Path, source: dict[str, Any], channels: list[Channel]) -> None:
        self.api = api
        self.root = root
        self.source_id = str(source["id"])
        self.channels = channels
        self.free = 500 * 1024**3
        self.admissions = 0
        self.made = 0
        self.capture = Capture(
            api.c.proxy,
            api.c.sources,
            api.c.db,
            recordings_dir=root,
            ffmpeg="ffmpeg",
            min_free_gb=50,
            free_bytes=lambda: self.free,
            tz=UTC,
        )

    @property
    def use(self) -> int:
        return int(self.api.c.proxy._source_use(self.source_id))

    def key(self, channel: Channel) -> str:
        return f"{channel.source_id}:{channel.ext_id}"

    def headers(self, channel: Channel) -> dict[str, str]:
        return dict((channel.data or {}).get("headers") or {})

    async def watch(self, channel: Channel) -> AsyncIterator[bytes]:
        """A viewer of the channel in proxy mode: opens (or joins) its shared upstream."""
        viewer = Viewer(self.api.profile_id, channel.name, "channel", ids.channel_id(channel.source_id, channel.ext_id))
        _, stream = await self.api.c.proxy.live_shared(
            self.key(channel), channel.stream_url, {}, self.source_id, max_connections=1, viewer=viewer
        )
        assert await anext(stream)
        return stream

    async def recording(self, channel: Channel, title: str = "Evening News", **fields: Any) -> Recording:
        start = fields.pop("starts_at", datetime(2026, 3, 1, 20, 0, tzinfo=UTC))
        hub = ids.channel_id(channel.source_id, channel.ext_id)
        self.made += 1
        row = Recording(
            id=f"rec{self.made:04d}",
            profile_id=self.api.profile_id,
            channel_id=hub,
            channel_name=channel.name,
            programme_key=f"{programme_key(hub, start)}#{self.made}",
            title=title,
            starts_at=start,
            ends_at=fields.pop("ends_at", start + timedelta(hours=1)),
            state="recording",
            **fields,
        )
        async with self.api.c.db.session() as s:
            s.add(row)
            await s.commit()
        return row

    def titles(self) -> list[str | None]:
        return [v.title for v in self.api.c.proxy.active_viewers(self.source_id)]


@pytest.fixture
def settings(settings: AppSettings, tmp_path: Path) -> AppSettings:
    root = tmp_path / "recordings"
    root.mkdir()
    return settings.model_copy(update={"recordings_dir": str(root)})


@pytest.fixture
async def rig(
    api: Api, settings: AppSettings, ffmpeg: list[Ffmpeg], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Rig]:
    source = await add_source(api)
    async with api.c.db.session() as s:
        row = await s.get(Source, source["id"])
        assert row is not None
        row.hints = {**(row.hints or {}), "max_connections": 1}
        await s.commit()
        channels = list(
            (
                await s.execute(
                    select(Channel)
                    .where(Channel.source_id == source["id"], Channel.stream_url.like("%.ts"))
                    .order_by(Channel.id)
                )
            ).scalars()
        )
    rig = Rig(api, Path(settings.recordings_dir), source, channels)
    admit = api.c.proxy._admit_source

    def counted(source_id: str | None, maximum: int | None) -> None:
        rig.admissions += 1
        admit(source_id, maximum)

    monkeypatch.setattr(api.c.proxy, "_admit_source", counted)
    monkeypatch.setattr(stream_proxy, "LINGER_S", 0.05)
    Live.opened = 0
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        router.route(host="hub.test").pass_through()
        router.route(host="streams.example").mock(
            side_effect=lambda request: httpx.Response(200, stream=Live(), headers={"content-type": "video/mp2t"})
        )
        yield rig
    await rig.capture.shutdown()
    await api.c.proxy.shutdown()


async def until(check: Any, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.005)


# ---------------------------------------------------------------- arguments
def test_ffmpeg_arguments_for_both_paths(tmp_path: Path) -> None:
    out = tmp_path / ".recording-x.part"
    copy = ["-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-f", "matroska", "-y", str(out)]
    assert pipe_args("ffmpeg", out) == [
        *["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-fflags", "+genpts"],
        *["-f", "mpegts", "-i", "pipe:0"],
        *copy,
    ]
    assert "mpegts" not in pipe_args("ffmpeg", out, mpegts=False)
    url = "http://streams.example.test/live/alice/s3cret/3.ts"
    headers = {"User-Agent": "Player/2.0", "Referer": "http://ref.example.test/"}
    assert url_args("ffmpeg", url, headers, out, live=True) == [
        *["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"],
        *["-user_agent", "Player/2.0", "-headers", "Referer: http://ref.example.test/\r\n"],
        *["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10", "-reconnect_at_eof", "1"],
        *["-fflags", "+genpts", "-i", url],
        *copy,
    ]
    # A finite archive download reconnects on errors but ends at the end of its input.
    archive = url_args("ffmpeg", url, {}, out, live=False)
    assert "-reconnect_at_eof" not in archive and archive[5:11] == [
        *["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
    ]
    for args in (pipe_args("ffmpeg", out), archive):
        assert "-c" in args and args[args.index("-c") + 1] == "copy"  # never a transcode


# ---------------------------------------------------------------- shared upstream
async def test_recording_rides_the_viewers_upstream_and_outlives_them(rig: Rig, ffmpeg: list[Ffmpeg]) -> None:
    channel = rig.channels[0]
    viewer = await rig.watch(channel)
    assert (rig.use, rig.admissions, Live.opened) == (1, 1, 1)
    row = await rig.recording(channel)
    owner = (await rig.api.get("/auth/me")).json()["user"]["id"]

    await rig.capture.start(row)
    [proc] = ffmpeg
    # No admission, no second connection: ffmpeg is fed the upstream's bytes on its stdin.
    assert (rig.use, rig.admissions, Live.opened) == (1, 1, 1) and not rig.api.c.proxy._captures
    assert proc.args == pipe_args("ffmpeg", rig.root / f".recording-{row.id}.part")
    assert proc.stdin is not None and rig.capture.is_running(row.id)
    await until(lambda: len(proc.stdin.data) >= 188 * 8)
    assert TS in bytes(proc.stdin.data)  # joined mid-stream, as a viewer does
    assert sorted(rig.titles(), key=str) == sorted([channel.name, "Recording: Evening News"])
    # While someone is watching, the connection is theirs: the recording is not its owner's stream.
    assert rig.capture.user_streams(owner) == [] and await rig.api.c.sessions.now_playing(owner) == []

    # The last viewer leaves, the linger passes: the recording keeps the upstream open.
    await viewer.aclose()
    seen = len(proc.stdin.data)
    await asyncio.sleep(0.2)
    assert rig.key(channel) in rig.api.c.proxy._shared and (rig.use, Live.opened) == (1, 1)
    assert len(proc.stdin.data) > seen and rig.capture.is_running(row.id)
    assert rig.titles() == ["Recording: Evening News"]
    # Now it alone holds the connection, and counts as its owner's stream.
    [mine] = rig.capture.user_streams(owner)
    assert (mine.title, mine.kind, mine.ref) == ("Recording: Evening News", "recording", f"rec:{row.id}")
    rig.api.c.streams.external.append(rig.capture.user_streams)
    assert [p["title"] for p in await rig.api.c.sessions.now_playing(owner)] == ["Recording: Evening News"]
    # A viewer who comes back joins the recording's upstream instead of opening another.
    again = await rig.watch(channel)
    assert (rig.use, Live.opened) == (1, 1) and rig.capture.user_streams(owner) == []
    await again.aclose()

    result = await rig.capture.stop(row)
    assert proc.returncode == 0 and proc.signals == []  # end of input, not a signal
    assert (result.path, result.error) == ("Evening News (2026-03-01 2000).mkv", None)
    assert (rig.root / str(result.path)).read_bytes() == b"matroska+cues" and result.size_bytes == 13
    assert not rig.capture.is_running(row.id) and not list(rig.root.glob(".*"))
    # With the recording gone the upstream closes after its linger and the connection is free.
    await until(lambda: rig.use == 0)
    assert rig.api.c.proxy._shared == {} and rig.capture.user_streams(owner) == []


async def test_slow_recording_is_dropped_and_fails_without_touching_viewers(
    rig: Rig, ffmpeg: list[Ffmpeg], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_module, "SPACE_CHECK_S", 0.02)
    channel = rig.channels[0]
    viewer = await rig.watch(channel)
    monkeypatch.setattr(stream_proxy, "CLIENT_QUEUE", 4)  # the recording's queue, opened next
    row = await rig.recording(channel)
    await rig.capture.start(row)
    [proc] = ffmpeg
    assert proc.stdin is not None
    proc.stdin.open.clear()  # the disk stalls: ffmpeg stops reading its input
    upstream = rig.api.c.proxy._shared[rig.key(channel)]
    await until(lambda: not upstream.taps)
    # The upstream let the recording go rather than wait for it; the viewer never noticed.
    for _ in range(20):
        assert await anext(viewer)
    await until(lambda: not rig.capture.is_running(row.id))
    result = await rig.capture.stop(row)
    assert result.error == "The recording could not keep up with the stream." and result.path is not None
    assert rig.use == 1
    await viewer.aclose()


async def test_upstream_ending_ends_the_capture_and_keeps_the_file(rig: Rig, ffmpeg: list[Ffmpeg]) -> None:
    channel = rig.channels[0]
    viewer = await rig.watch(channel)
    row = await rig.recording(channel)
    await rig.capture.start(row)
    await rig.api.c.proxy.stop_owned()  # an administrator stops all streams
    await until(lambda: not rig.capture.is_running(row.id))
    assert ffmpeg[0].returncode == 0
    result = await rig.capture.stop(row)
    assert result.path is not None and result.error is None  # the recorder marks it as ended early
    await viewer.aclose()


# ---------------------------------------------------------------- a connection of its own
async def test_recording_alone_is_admitted_like_a_viewer(rig: Rig, ffmpeg: list[Ffmpeg]) -> None:
    channel, other = rig.channels[0], rig.channels[1]
    row = await rig.recording(channel, season=2, episode=5, subtitle="The Vote")
    owner = (await rig.api.get("/auth/me")).json()["user"]["id"]
    await rig.capture.start(row)
    [proc] = ffmpeg
    tmp = rig.root / f".recording-{row.id}.part"
    # Admitted against the source; ffmpeg reads the stream itself, reconnecting like a live session.
    assert (rig.admissions, rig.use, len(rig.api.c.proxy._captures)) == (1, 1, 1)
    assert proc.args == url_args("ffmpeg", channel.stream_url, rig.headers(channel), tmp, live=True)
    assert rig.headers(channel) and "-user_agent" in proc.args  # the channel's own request headers
    assert proc.stdin is None and "-reconnect_at_eof" in proc.args and Live.opened == 0
    assert rig.titles() == ["Recording: Evening News"]
    [mine] = rig.capture.user_streams(owner)
    assert mine.title == "Recording: Evening News" and rig.capture.user_streams("someone-else") == []

    # The source allows one connection: a second recording, like a second viewer, is turned away.
    second = await rig.recording(other, "Late Film")
    with pytest.raises(SourceBusy) as busy:
        await rig.capture.start(second)
    assert busy.value.code == "source_busy" and len(ffmpeg) == 1 and not rig.capture.is_running(second.id)
    with pytest.raises(Exception) as refused:
        await rig.watch(other)
    assert getattr(refused.value, "code", None) == "source_busy"

    result = await rig.capture.stop(row)
    assert proc.signals == [signal.SIGINT] and proc.returncode == 255  # interrupted, so the file is finalised
    assert result.error is None and result.size_bytes == 13
    assert result.path == "Evening News/Season 02/Evening News - S02E05 - The Vote.mkv"
    assert (rig.use, len(rig.api.c.proxy._captures), rig.titles()) == (0, 0, [])
    # The connection is free again.
    await rig.capture.start(second)
    assert rig.capture.is_running(second.id)
    assert (await rig.capture.stop(second)).path == "Late Film (2026-03-01 2000).mkv"


async def test_ffmpeg_dying_releases_the_connection_and_reports_how(rig: Rig, ffmpeg: list[Ffmpeg]) -> None:
    row = await rig.recording(rig.channels[0])
    await rig.capture.start(row)
    ffmpeg[0].end(1)
    await until(lambda: rig.use == 0)
    assert not rig.capture.is_running(row.id)
    result = await rig.capture.stop(row)
    assert result.error == "The capture ended with an error (ffmpeg exit code 1)." and result.path is not None


async def test_stop_all_streams_ends_a_recording_on_its_own_connection(rig: Rig, ffmpeg: list[Ffmpeg]) -> None:
    row = await rig.recording(rig.channels[0])
    await rig.capture.start(row)
    assert rig.api.c.proxy.recovery_status()["active_sessions"] == 1
    status = await rig.api.c.proxy.stop_owned()
    assert status["active_sessions"] == 0 and ffmpeg[0].signals == [signal.SIGINT]
    assert not rig.capture.is_running(row.id)
    result = await rig.capture.stop(row)
    assert result.path is not None and "administrator" in str(result.error)


# ---------------------------------------------------------------- the file
async def test_file_is_never_written_over_another(rig: Rig, ffmpeg: list[Ffmpeg]) -> None:
    channel = rig.channels[0]
    taken = rig.root / "Evening News (2026-03-01 2000).mkv"
    taken.write_bytes(b"an earlier airing")
    first = await rig.recording(channel)
    assert recording_filename(first, UTC) == taken.name
    await rig.capture.start(first)
    # While it is written the file has a hidden temporary name inside the recordings directory.
    assert [p.name for p in rig.root.glob(".*")] == [f".recording-{first.id}.part"]
    assert (await rig.capture.stop(first)).path == "Evening News (2026-03-01 2000) (2).mkv"
    second = await rig.recording(channel)
    await rig.capture.start(second)
    assert (await rig.capture.stop(second)).path == "Evening News (2026-03-01 2000) (3).mkv"
    assert taken.read_bytes() == b"an earlier airing" and len(list(rig.root.iterdir())) == 3

    # Sports and rule recordings are filed in folders, which are created as needed.
    game = await rig.recording(channel, "Rovers v United", is_sport=True, subtitle="Cup Final")
    await rig.capture.start(game)
    assert (await rig.capture.stop(game)).path == "Sports/Rovers v United - Cup Final (2026-03-01).mkv"


async def test_stop_without_a_running_capture_keeps_what_a_restart_left(rig: Rig, ffmpeg: list[Ffmpeg]) -> None:
    row = await rig.recording(rig.channels[0])
    nothing = await rig.capture.stop(row)
    assert (nothing.path, nothing.size_bytes, nothing.error) == (None, 0, None)
    (rig.root / f".recording-{row.id}.part").write_bytes(b"partial")
    kept = await rig.capture.stop(row)
    assert (kept.path, kept.size_bytes) == ("Evening News (2026-03-01 2000).mkv", 7)
    assert ffmpeg == []


async def test_capture_stops_when_free_space_falls_below_the_floor(
    rig: Rig, ffmpeg: list[Ffmpeg], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_module, "SPACE_CHECK_S", 0.02)
    row = await rig.recording(rig.channels[0])
    await rig.capture.start(row)
    await asyncio.sleep(0.1)
    assert rig.capture.is_running(row.id) and ffmpeg[0].signals == []  # plenty of room so far
    rig.free = 50 * 1024**3 - 1
    await until(lambda: not rig.capture.is_running(row.id))
    await until(lambda: rig.use == 0)
    assert ffmpeg[0].signals == [signal.SIGINT]  # finalised, not killed
    result = await rig.capture.stop(row)
    assert result.error == "Stopped: free space for recordings fell below 50 GB."
    assert result.path == "Evening News (2026-03-01 2000).mkv" and result.size_bytes == 13


# ---------------------------------------------------------------- provider archive
async def archived(rig: Rig, channel: Channel, days: int = 3) -> None:
    async with rig.api.c.db.session() as s:
        row = await s.get(Channel, channel.id)
        assert row is not None
        row.catchup_days = days
        row.data = {
            **(row.data or {}),
            "catchup_type": "default",
            "catchup_source": "http://streams.example/archive/{utc}-{duration}.ts",
        }
        await s.commit()


async def test_missed_programme_is_fetched_from_the_provider_archive(rig: Rig, ffmpeg: list[Ffmpeg]) -> None:
    channel = rig.channels[0]
    now = datetime.now(UTC).replace(microsecond=0)
    ended = await rig.recording(
        channel, "Late Film", starts_at=now - timedelta(hours=3), ends_at=now - timedelta(hours=1)
    )
    # No archive advertised: nothing to do.
    assert await rig.capture.recover_from_archive(ended) is None and ffmpeg == []
    await archived(rig, channel)
    airing = await rig.recording(
        channel, "Still On", starts_at=now - timedelta(minutes=30), ends_at=now + timedelta(minutes=30)
    )
    old = await rig.recording(
        channel, "Last Week", starts_at=now - timedelta(days=5), ends_at=now - timedelta(days=5) + timedelta(hours=1)
    )
    assert await rig.capture.recover_from_archive(airing) is None  # not over yet
    assert await rig.capture.recover_from_archive(old) is None  # older than the archive keeps
    assert ffmpeg == []

    fetch = asyncio.create_task(rig.capture.recover_from_archive(ended))
    await until(lambda: len(ffmpeg) == 1)
    proc = ffmpeg[0]
    start = int((now - timedelta(hours=3)).timestamp())
    tmp = rig.root / f".archive-{ended.id}.part"
    url = f"http://streams.example/archive/{start}-7200.ts"
    assert proc.args == url_args("ffmpeg", url, rig.headers(channel), tmp, live=False)
    # The download occupies one of the source's connections like any viewer, under the recording's name.
    assert (rig.admissions, rig.use, rig.titles()) == (1, 1, ["Recording: Late Film"])
    proc.end(0)
    result = await fetch
    assert result is not None and (result.error, result.size_bytes) == (None, 13)
    assert result.path == recording_filename(ended, UTC) and (rig.root / result.path).is_file()
    assert rig.use == 0 and not list(rig.root.glob(".*"))


async def test_archive_fetch_waits_for_a_connection_and_reports_a_failed_download(
    rig: Rig, ffmpeg: list[Ffmpeg]
) -> None:
    channel = rig.channels[0]
    await archived(rig, channel)
    now = datetime.now(UTC).replace(microsecond=0)
    ended = await rig.recording(
        channel, "Late Film", starts_at=now - timedelta(hours=3), ends_at=now - timedelta(hours=1)
    )
    viewer = await rig.watch(rig.channels[1])
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            await viewer.aclose()
            await until(lambda: rig.use == 0)

    rig.capture._sleep = sleep
    fetch = asyncio.create_task(rig.capture.recover_from_archive(ended))
    await until(lambda: len(ffmpeg) == 1)
    assert waits == [30.0, 30.0]
    ffmpeg[0].end(1)
    result = await fetch
    assert result is not None and result.path is None
    assert result.error == "The archive download ended with an error (ffmpeg exit code 1)."
    assert rig.use == 0 and not list(rig.root.iterdir())


# ---------------------------------------------------------------- wiring
async def test_capture_is_the_recorders_backend_when_recording_is_configured(api: Api) -> None:
    assert isinstance(api.c.recorder.capture, Capture) and api.c.capture is api.c.recorder.capture
    assert api.c.capture.root == Path(api.c.settings.recordings_dir)
    assert api.c.capture.user_streams in api.c.streams.external
