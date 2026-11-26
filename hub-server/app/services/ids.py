"""Hub id scheme.

- ``tmdb:movie:603`` / ``tmdb:tv:1399`` — a title identified by TMDB (merged across sources)
- ``lib:<source_id>:<ext_id>``          — an item owned by one source (library/VOD/series/season/episode)
- ``ch:<source_id>:<ext_id>``           — a live channel
- ``prog:<int>``                         — an EPG programme (for catch-up)
- ``rec:<recording id>``                 — a finished recording (playback and its progress only)
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
    if len(parts) == 2 and parts[0] in ("prog", "rec") and parts[1]:
        return HubId(parts[0], parts[1])
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


# Bare numbers can be part of the official name (Death Race 2050, Blade Runner 2049).
_DISPLAY_YEAR = re.compile(r"\s*(?:\((?:18|19|20|21)\d{2}\)|\[(?:18|19|20|21)\d{2}\])\s*$")
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
    cleaned = _DISPLAY_YEAR.sub("", cleaned).strip()
    return cleaned or title


_EPISODE_MARK = re.compile(
    r"(?<![A-Za-z0-9])(?:S\d{1,3}\s*[ ._-]?\s*E\d{1,4}(?:-?E\d{1,4})*|\d{1,2}x\d{2,4})(?![A-Za-z0-9])", re.I
)
_FILE_EXT = re.compile(r"\.(?:mkv|mp4|m4v|avi|ts|mov|wmv|webm)$", re.I)
# Release-name tails: everything from the first quality or source token on.
_RELEASE_TAIL = re.compile(
    r"[ ._-]+(?:\d{3,4}p|4k|uhd|hdr|web(?:-?dl|rip)?|bluray|bdrip|hdtv|dvdrip"
    r"|x26[45]|h\.?26[45]|hevc|aac|ddp?5\.1|proper|repack)\b.*$",
    re.I,
)
_GENERIC_EPISODE = re.compile(r"^(?:episode|ep\.?|e)\s*\d+$", re.I)


def episode_display_title(title: str, episode_number: int | None) -> str:
    """An episode tile's name: ``E4 · In the Rubble``, or ``Episode 4`` when it has no name of its own.

    Providers often store the whole release line (``EN - Show - S06E04 - Name``) or a file
    name; only the part after the season/episode marker is the episode's own name.
    """
    name = _FILE_EXT.sub("", (title or "").strip())
    mark = _EPISODE_MARK.search(name)
    if mark:
        name = name[mark.end() :]
    if " " not in name.strip() and name.count(".") >= 2:
        # A dotted file name: Show.S01E03.The.Name.1080p
        name = name.replace(".", " ")
    name = _RELEASE_TAIL.sub("", name).strip(" .-_:|")
    if not mark:
        name = clean_display_title(name) if name else name
    if not name or _GENERIC_EPISODE.match(name):
        return f"Episode {episode_number}" if episode_number is not None else (title or "").strip()
    return f"E{episode_number} · {name}" if episode_number is not None else name
