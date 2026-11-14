"""Artwork is served at the size a screen can show, and nothing readable or not is ever lost."""

from __future__ import annotations

import io

from PIL import Image

from app.infra.image_fit import BACKDROP_WIDTH, LIGHT_BYTES, POSTER_WIDTH, fit


def picture(width: int, height: int, fmt: str = "JPEG", mode: str = "RGB", noisy: bool = True) -> bytes:
    """An image of the given size; noise keeps a JPEG as heavy as a real photograph."""
    image = Image.effect_noise((width, height), 90).convert(mode) if noisy else Image.new(mode, (width, height), "teal")
    out = io.BytesIO()
    image.save(out, fmt, **({"quality": 95} if fmt == "JPEG" else {}))
    return out.getvalue()


def opened(body: bytes) -> Image.Image:
    return Image.open(io.BytesIO(body))


def test_a_huge_poster_is_fitted_to_a_tile() -> None:
    original = picture(1920, 2880)
    assert len(original) > 2_000_000
    body, kind = fit(original, "image/jpeg")
    assert kind == "image/jpeg" and opened(body).size == (POSTER_WIDTH, 900)
    assert len(body) < len(original) // 5


def test_a_4k_backdrop_is_fitted_to_a_1080p_screen() -> None:
    body, kind = fit(picture(3840, 2160), "image/jpeg")
    assert kind == "image/jpeg" and opened(body).size == (BACKDROP_WIDTH, 1080)


def test_a_small_light_image_is_passed_through_exactly() -> None:
    small = picture(300, 450, noisy=False)
    assert len(small) <= LIGHT_BYTES
    assert fit(small, "image/jpeg") == (small, "image/jpeg")


def test_a_heavy_image_within_its_size_is_re_encoded() -> None:
    heavy = picture(560, 840, fmt="PNG")
    assert len(heavy) > LIGHT_BYTES
    body, kind = fit(heavy, "image/png")
    assert kind == "image/jpeg" and opened(body).size == (560, 840) and len(body) < len(heavy)


def test_transparency_stays_a_png() -> None:
    logo = picture(2400, 1200, fmt="PNG", mode="RGBA")
    body, kind = fit(logo, "image/png")
    image = opened(body)
    assert kind == "image/png" and image.size == (BACKDROP_WIDTH, 960) and image.mode == "RGBA"


def test_what_cannot_be_read_is_served_as_it_came() -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4000" height="4000"/>' + b" " * LIGHT_BYTES
    assert fit(svg, "image/svg+xml") == (svg, "image/svg+xml")
    broken = picture(1920, 2880)[:5000]
    assert fit(broken, "image/jpeg") == (broken, "image/jpeg")
    frames = [Image.new("RGB", (1200, 1800), c) for c in ("red", "blue")]
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:])
    gif = out.getvalue() + b"\0" * LIGHT_BYTES
    assert fit(gif, "image/gif") == (gif, "image/gif")
