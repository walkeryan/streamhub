"""Lenient field access for Xtream Codes panels.

Panels (XC, XUI, Ministra bridges, …) disagree on field names and types: ids
come as strings or ints, booleans as "1"/1/true, ratings as "7.2"/"" and
timestamps as epoch strings. Everything here is total — it never raises.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import UTC, datetime
from typing import Any


def first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def as_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, list):
        v = next((x for x in v if x), None)
        if v is None:
            return None
    s = str(v).strip()
    return s or None


def as_int(v: Any, default: int | None = None) -> int | None:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    m = re.match(r"\s*(-?\d+)", str(v))
    return int(m.group(1)) if m else default


def as_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "."))
    except ValueError:
        return None
    return f if f == f else None  # NaN guard


def as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def as_epoch(v: Any) -> datetime | None:
    n = as_int(v)
    if n is None or n <= 0:
        return None
    try:
        return datetime.fromtimestamp(n, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def year_from(*vals: Any) -> int | None:
    for v in vals:
        if v is None:
            continue
        m = re.search(r"(19|20)\d{2}", str(v))
        if m:
            return int(m.group(0))
    return None


def maybe_b64(v: Any) -> str | None:
    """get_short_epg returns base64 title/description on most panels."""
    s = as_str(v)
    if not s:
        return None
    if re.fullmatch(r"[A-Za-z0-9+/=\s]+", s) and len(s) % 4 == 0:
        try:
            dec = base64.b64decode(s, validate=False).decode("utf-8")
            if dec and all(c.isprintable() or c.isspace() for c in dec):
                return dec
        except (binascii.Error, UnicodeDecodeError, ValueError):
            pass
    return s


def genres_from(v: Any) -> list[str]:
    s = as_str(v)
    if not s:
        return []
    return [g.strip() for g in re.split(r"[,/|]", s) if g.strip()]


def tmdb_from(d: dict[str, Any]) -> int | None:
    v = first(d, "tmdb", "tmdb_id", "tmdbId", "TMDB")
    n = as_int(v)
    return n if n and n > 0 else None


def imdb_from(d: dict[str, Any]) -> str | None:
    v = as_str(first(d, "imdb", "imdb_id", "imdbId"))
    if v and re.fullmatch(r"tt\d+", v):
        return v
    return None
