"""What is playing and how it reaches the viewer: the facts behind the player's stream information.

Everything here is read from the cached media probe (``Title.extra["probe"]``) and from the plan the
resolver made for the stream. Only neutral facts leave the server: codecs, sizes, rates, and whether
the file is sent as it is, remuxed or transcoded. Never a URL, a host name or anything from the
source's configuration other than the name the owner gave it.
"""

from __future__ import annotations

from typing import Any

# Encoders that run on a GPU (the ``transcode_hw`` setting); anything else encodes in software.
HARDWARE_ENCODERS = {"nvenc", "vaapi", "qsv"}
# A transcode for a browser is at most this wide (``vod_ffmpeg_attempts``).
MAX_WIDTH = 1920
# The qualities a viewer can cap a film at (``max_height``), and the video bit rate in kbit/s each is
# held to. The audio beside it is always 160 kbit/s AAC, so a client can say what an hour will cost.
QUALITY_KBPS = {1080: 5000, 720: 2800, 480: 1400}
# ffprobe's ``color_transfer`` values that mean high dynamic range.
HDR_TRANSFERS = {"smpte2084": "hdr10", "arib-std-b67": "hlg"}


def frame_rate(raw: Any) -> float | None:
    """ffprobe's ``avg_frame_rate`` ("24000/1001") as frames per second; ``None`` for "0/0" and the like."""
    try:
        top, _, bottom = str(raw).partition("/")
        rate = float(top) / float(bottom or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(rate, 3) if 0 < rate < 1000 else None


def dynamic_range(transfer: Any) -> str | None:
    """``sdr``, ``hdr10`` or ``hlg`` from the transfer characteristic; ``None`` when the file does not say."""
    name = str(transfer or "").lower()
    if not name or name in ("unknown", "unspecified", "reserved"):
        return None
    return HDR_TRANSFERS.get(name, "sdr")


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def capped_height(height: Any, max_height: int | None) -> int | None:
    """The cap that takes effect: only for a source known to be taller. Otherwise the file is
    delivered as it would be without one, and nothing is ever scaled up."""
    if max_height not in QUALITY_KBPS or not isinstance(height, int) or isinstance(height, bool):
        return None
    return max_height if height > max_height else None


def output_size(width: int | None, height: int | None, max_height: int | None = None) -> tuple[int, int] | None:
    """The picture size a transcode delivers, keeping the shape: ``None`` when the source's is unknown."""
    if not width or not height:
        return None
    if width > MAX_WIDTH:
        width, height = MAX_WIDTH, _even(height * MAX_WIDTH / width)
    if max_height and height > max_height:
        width, height = _even(width * max_height / height), max_height
    return width, height


def encoder_kind(delivery: str, hw: str) -> str | None:
    """Who encodes the video of a transcode. A hardware encoder that fails to start falls back to
    software for that session (``StreamProxy.hls_session``); this is the plan, made before it starts."""
    if delivery != "transcode":
        return None
    return "hardware" if hw in HARDWARE_ENCODERS else "software"


def _positive(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def media_info(
    *,
    source_name: str | None,
    probe: dict[str, Any] | None,
    audio_track: int | None,
    mode: str,
    video_plan: str | None,
    hw: str,
    live_transcode: bool = False,
    max_height: int | None = None,
) -> dict[str, Any] | None:
    """The ``media_info`` of a playback descriptor; ``None`` for a title another app plays.

    ``video_plan`` is the web VOD plan (``copy`` or ``transcode``, ``None`` without one),
    ``live_transcode`` a channel whose source asks for transcoding and ``max_height`` the quality
    cap in force (``capped_height``). Whatever is unknown is ``None`` and is left out of the answer
    by the schema.
    """
    if mode == "handoff":
        return None
    raw_video = probe.get("video") if probe else None
    video = raw_video if isinstance(raw_video, dict) else {}
    raw_audio = probe.get("audio") if probe else None
    tracks = [t for t in raw_audio if isinstance(t, dict)] if isinstance(raw_audio, list) else []
    index = audio_track if audio_track is not None else next((i for i, t in enumerate(tracks) if t.get("default")), 0)
    track = tracks[index] if 0 <= index < len(tracks) else {}
    if video_plan == "transcode" or (mode == "hls" and live_transcode):
        delivery = "transcode"
    else:
        # HLS made by this server without encoding the video is a remux; anything else is the file or feed itself.
        delivery = "remux" if mode == "hls" else "direct"
    width, height = _positive(video.get("width")), _positive(video.get("height"))
    if video_plan == "transcode":
        size = output_size(width, height, max_height)
    else:
        size = (width, height) if width and height else None
    fps = video.get("fps")
    language = str(track.get("language") or "")
    bit_rate = _positive(probe.get("bit_rate")) if probe else None
    return {
        "source_name": (source_name or "")[:80] or None,
        # ffprobe names a family ("matroska,webm"); the first name is the one a viewer recognises.
        "container": str(probe.get("container") or "").split(",")[0][:24] or None if probe else None,
        "bitrate_kbps": bit_rate // 1000 or None if bit_rate else None,
        "video": {
            "codec": str(video.get("codec") or "")[:24] or None,
            "width": width,
            "height": height,
            "frame_rate": fps if isinstance(fps, (int, float)) and not isinstance(fps, bool) and fps > 0 else None,
            "dynamic_range": dynamic_range(video.get("transfer")),
        }
        if video
        else None,
        "audio": {
            "codec": str(track.get("codec") or "")[:24] or None,
            "channels": _positive(track.get("channels")),
            "language": language if language and language != "und" else None,
        }
        if track
        else None,
        "delivery": delivery,
        "encoder": encoder_kind(delivery, hw),
        "output_width": size[0] if size else None,
        "output_height": size[1] if size else None,
        "max_height": max_height if video_plan == "transcode" else None,
    }
