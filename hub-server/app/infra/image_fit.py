"""Artwork the hub serves, cut down to what a screen can show.

Providers hand out originals: a 1920×2880 poster or a 4K backdrop at 4-6 MB is common, and a screen
of tiles asks for dozens of them, so a TV across the internet waited on megabytes it then shrank to a
few hundred pixels. Each image is fitted once, before the disk cache keeps it.

A poster (taller than wide) is fitted to POSTER_WIDTH, which covers a tile at twice its CSS size; a
backdrop or anything wider is fitted to BACKDROP_WIDTH, a full 1080p screen. An image already that
small and already light is passed through as it came. Anything with transparency stays a PNG (channel
logos); everything else becomes a progressive JPEG. Whatever Pillow cannot read (SVG, an animation,
a broken file) is passed through untouched: fitting is an optimisation, never a reason to lose art.
"""

from __future__ import annotations

import io
import logging
import warnings

from PIL import Image

log = logging.getLogger(__name__)

POSTER_WIDTH = 600
BACKDROP_WIDTH = 1920
# Already within its size, an image heavier than this is still re-encoded.
LIGHT_BYTES = 300_000
JPEG_QUALITY = 82
# Bigger than any real artwork: a larger claim is a decompression bomb, not a poster.
MAX_PIXELS = 50_000_000
_READABLE = {"JPEG", "PNG", "WEBP"}


def _cap(width: int, height: int) -> int:
    return POSTER_WIDTH if height > width else BACKDROP_WIDTH


def fit(body: bytes, media_type: str) -> tuple[bytes, str]:
    """``(body, media type)`` of the image as the hub serves it: fitted, or exactly as given."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as image:
                if image.format not in _READABLE or getattr(image, "is_animated", False):
                    return body, media_type
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    return body, media_type
                cap = _cap(width, height)
                if width <= cap and len(body) <= LIGHT_BYTES:
                    return body, media_type
                target = (cap, max(1, round(height * cap / width))) if width > cap else (width, height)
                if image.format == "JPEG":
                    # Decodes at a fraction of the size straight away: a 4K JPEG never fills memory.
                    image.draft("RGB", target)
                image.load()
                alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
                picture = image.convert("RGBA" if alpha else "RGB")
                if picture.size != target:
                    picture = picture.resize(target, Image.Resampling.LANCZOS)
                out = io.BytesIO()
                if alpha:
                    picture.save(out, "PNG", optimize=True)
                    fitted, kind = out.getvalue(), "image/png"
                else:
                    picture.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
                    fitted, kind = out.getvalue(), "image/jpeg"
    except Exception as exc:  # unreadable or hostile: served as it came
        log.debug("artwork not fitted", extra={"err": type(exc).__name__})
        return body, media_type
    # Re-encoding a small, well-compressed image can make it bigger: then the original wins.
    return (fitted, kind) if len(fitted) < len(body) else (body, media_type)
