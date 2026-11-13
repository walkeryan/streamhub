"""On-demand ``ffprobe`` of one VOD file.

Some sources list a film without saying what is inside it. A browser needs the answer before
playback (container, video codec, audio codecs) and a viewer needs the audio tracks to choose from,
so the file is asked directly: one short read-only request against the URL and headers the proxy
would use, capped at ten seconds. A probe holds an upstream connection while it runs, so callers
run it only for the one title being opened and keep the result in ``Title.extra["probe"]``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

from app.adapters.language.inferrer import normalise_code
from app.services.media_info import frame_rate

log = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 10.0
PROBE_TTL = timedelta(days=30)
# Enough to read stream headers; a long analysis would hold the source connection for nothing.
ANALYZE_US = 5_000_000
PROBE_BYTES = 5_000_000
MAX_OUTPUT = 512 * 1024
# No ``format=filename``: that field is the upstream URL and may embed credentials.
ENTRIES = (
    "format=format_name,duration,bit_rate"
    ":stream=codec_type,codec_name,profile,pix_fmt,width,height,channels,channel_layout,avg_frame_rate,color_transfer"
    ":stream_tags=language,title"
    ":stream_disposition=default,attached_pic"
)


def ffprobe_args(ffprobe: str, url: str, headers: dict[str, str]) -> list[str]:
    args = [ffprobe, "-v", "error", "-hide_banner", "-of", "json"]
    args += ["-analyzeduration", str(ANALYZE_US), "-probesize", str(PROBE_BYTES)]
    ua = next((v for k, v in headers.items() if k.lower() == "user-agent"), None)
    if ua:
        args += ["-user_agent", ua]
    other = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() != "user-agent")
    if other:
        args += ["-headers", other]
    if url.startswith("http"):
        args += ["-rw_timeout", str(int(PROBE_TIMEOUT_S * 1_000_000))]
    return [*args, "-show_entries", ENTRIES, "-i", url]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_probe(raw: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """ffprobe JSON to the cached shape; ``None`` when it describes nothing playable."""
    if not isinstance(raw, dict):
        return None
    streams = [s for s in raw.get("streams") or [] if isinstance(s, dict)]
    video: dict[str, Any] | None = None
    audio: list[dict[str, Any]] = []
    for stream in streams:
        raw_disposition = stream.get("disposition")
        disposition = raw_disposition if isinstance(raw_disposition, dict) else {}
        raw_tags = stream.get("tags")
        tags = {str(k).lower(): v for k, v in raw_tags.items()} if isinstance(raw_tags, dict) else {}
        codec = str(stream.get("codec_name") or "").lower()
        if stream.get("codec_type") == "video":
            # Cover art is a video stream too; it is never the film.
            if video is None and codec and not disposition.get("attached_pic"):
                video = {
                    "codec": codec,
                    "width": _int(stream.get("width")),
                    "height": _int(stream.get("height")),
                    "pix_fmt": str(stream.get("pix_fmt") or "").lower() or None,
                }
                # For the viewer's stream information only (``media_info``); kept when the file says them.
                fps = frame_rate(stream.get("avg_frame_rate"))
                if fps is not None:
                    video["fps"] = fps
                transfer = str(stream.get("color_transfer") or "").lower()
                if transfer:
                    video["transfer"] = transfer[:24]
        elif stream.get("codec_type") == "audio" and codec:
            title = str(tags.get("title") or "").strip()[:80]
            audio.append(
                {
                    # Position among the file's audio streams: the value ``audio_track`` selects.
                    "index": len(audio),
                    "language": normalise_code(str(tags.get("language") or "")) or "und",
                    "codec": codec,
                    "channels": _int(stream.get("channels")),
                    "title": title or None,
                    "default": disposition.get("default") in (1, True),
                }
            )
    if video is None and not audio:
        return None
    raw_format = raw.get("format")
    fmt = raw_format if isinstance(raw_format, dict) else {}
    try:
        duration_ms: int | None = int(float(str(fmt.get("duration"))) * 1000)
    except ValueError:
        duration_ms = None
    found: dict[str, Any] = {
        "video": video,
        "audio": audio,
        "container": str(fmt.get("format_name") or "").lower() or None,
        "duration_ms": duration_ms if duration_ms and duration_ms > 0 else None,
        "probed_at": (now or datetime.now(UTC)).isoformat(),
    }
    bit_rate = _int(fmt.get("bit_rate"))
    if bit_rate and bit_rate > 0:
        found["bit_rate"] = bit_rate
    return found


def cached_probe(extra: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any] | None:
    """The stored probe while it is younger than ``PROBE_TTL``."""
    probe = (extra or {}).get("probe")
    if not isinstance(probe, dict):
        return None
    try:
        at = datetime.fromisoformat(str(probe.get("probed_at")))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return probe if (now or datetime.now(UTC)) - at < PROBE_TTL else None


class MediaProbe:
    def __init__(self, ffprobe: str = "ffprobe") -> None:
        self.ffprobe = ffprobe
        # Without the binary there is no ffmpeg either, so callers keep their unprobed behaviour.
        self.available = shutil.which(ffprobe) is not None

    async def run(self, url: str, headers: dict[str, str]) -> dict[str, Any] | None:
        """Probe one URL. ``None`` on any failure; the URL and ffprobe's stderr are never logged."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *ffprobe_args(self.ffprobe, url, headers),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            self.available = False
            log.warning("ffprobe unavailable", extra={"err": type(exc).__name__})
            return None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), PROBE_TIMEOUT_S)
        except BaseException as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(proc.wait()), 1)
            if not isinstance(exc, TimeoutError):
                raise
            log.info("media probe timed out")
            return None
        if proc.returncode != 0 or len(out) > MAX_OUTPUT:
            log.info("media probe failed", extra={"code": proc.returncode})
            return None
        try:
            return parse_probe(json.loads(out))
        except ValueError:
            return None
