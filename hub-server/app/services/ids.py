"""Hub id scheme.

- ``tmdb:movie:603`` / ``tmdb:tv:1399`` — a title identified by TMDB (merged across sources)
- ``lib:<source_id>:<ext_id>``          — an item owned by one source (library/VOD/series/season/episode)
- ``ch:<source_id>:<ext_id>``           — a live channel
- ``prog:<int>``                         — an EPG programme (for catch-up)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.domain.entities import MediaKind


@dataclass(frozen=True, slots=True)
class HubId:
    scheme: str
    a: str
    b: str = ""

    def __str__(self) -> str:
        return f"{self.scheme}:{self.a}:{self.b}" if self.b else f"{self.scheme}:{self.a}"


def parse(hub_id: str) -> HubId:
    parts = hub_id.split(":", 2)
    if len(parts) == 3 and parts[0] in ("tmdb", "lib", "ch"):
        return HubId(parts[0], parts[1], parts[2])
    if len(parts) == 2 and parts[0] == "prog":
        return HubId("prog", parts[1])
    raise ValueError(f"invalid id {hub_id!r}")


def tmdb_id(media_type: str, tid: int) -> str:
    return f"tmdb:{media_type}:{tid}"


def lib_id(source_id: str, ext_id: str) -> str:
    return f"lib:{source_id}:{ext_id}"


def channel_id(source_id: str, ext_id: str) -> str:
    return f"ch:{source_id}:{ext_id}"


def prog_id(pk: int) -> str:
    return f"prog:{pk}"


MOVIE_KINDS = (MediaKind.MOVIE.value, MediaKind.VOD.value)
SHOW_KINDS = (MediaKind.SHOW.value, MediaKind.SERIES.value)


def tmdb_type_for_kind(kind: str) -> str | None:
    if kind in MOVIE_KINDS:
        return "movie"
    if kind in SHOW_KINDS:
        return "tv"
    return None


_STRIP_RE = re.compile(
    r"(\[[^\]]*\]|\([^)]*\)|\b(4k|uhd|fhd|hd|sd|multi|multisub|vostfr|hevc|x264|x265|1080p|720p|2160p)\b)",
    re.IGNORECASE,
)
_PREFIX_RE = re.compile(r"^\s*([A-Z]{2,3}|[A-Z]{2}-[A-Z]{2})\s*[|:\-–]\s+", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_YEAR_RE = re.compile(r"[\(\[\s\-]((?:19|20)\d{2})[\)\]\s]*$")


def normalise_title(title: str) -> str:
    """'EN | The Matrix (1999) [4K]' → 'the matrix'."""
    t = _PREFIX_RE.sub("", title or "")
    t = _YEAR_RE.sub("", t)
    t = _STRIP_RE.sub(" ", t)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = t.lower().replace("&", " and ")
    t = re.sub(r"^(the|a|an)\s+", "", t.strip())
    return _NON_ALNUM.sub(" ", t).strip()


_DISPLAY_PREFIX = re.compile(
    r"^\s*(?:4K|3D|UHD|FHD|HD|SD|EN|ENG|FR|FRA|DE|DEU|ES|ESP|IT|ITA|PT|NL|US|UK|"
    r"NETFLIX|AMAZON|HBO|DISNEY|D\+)\s*[|:–-]\s*",
    re.IGNORECASE,
)


def clean_display_title(title: str) -> str:
    """Remove explicit provider decorations, without pretending to know canonical metadata."""
    cleaned = title.strip()
    for _ in range(6):
        updated = _DISPLAY_PREFIX.sub("", cleaned, count=1)
        if updated == cleaned:
            break
        cleaned = updated
    cleaned = re.sub(r"\s*\[(?:4K|HD|FHD|UHD|1080p|2160p)\]\s*$", "", cleaned, flags=re.I)
    cleaned = _YEAR_RE.sub("", cleaned).strip()
    return cleaned or title
