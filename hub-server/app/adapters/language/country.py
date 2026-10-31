"""Conservative country hints from explicit provider group tags, never from language."""

from __future__ import annotations

import re

from app.adapters.language.data import COUNTRY_LANG

_FLAGS = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
# Tags must be delimited: ordinary prose such as 'Made in America' is not a tag.
_TAG = re.compile(r"(?:^|[|:/\[({\-])\s*([A-Z]{2,3})\s*(?=$|[|:/\])}\-])")
_ALIASES = {"USA": "US", "UK": "GB"}
_NAMES = {"UNITED STATES": "US", "UNITEDSTATES": "US", "UNITED KINGDOM": "GB", "UNITEDKINGDOM": "GB"}


def group_country(group: str | None) -> tuple[str | None, str | None]:
    text = (group or "").strip().upper()
    found: set[str] = set()
    flag_countries: set[str] = set()
    for flag in _FLAGS.findall(text):
        code = "".join(chr(ord(ch) - 0x1F1E6 + ord("A")) for ch in flag)
        if code in COUNTRY_LANG:
            found.add(code)
            flag_countries.add(code)
    for match in _TAG.finditer(text):
        token = match.group(1)
        code = _ALIASES.get(token, token)
        if code in COUNTRY_LANG:
            found.add(code)
    for label, code in _NAMES.items():
        if re.search(r"(?<![A-Z])" + re.escape(label) + r"(?![A-Z])", text):
            found.add(code)
    if len(found) != 1:
        return None, None
    country = next(iter(found))
    return country, "flag" if country in flag_countries else "group"


def channel_country(explicit: str | None, group: str | None) -> tuple[str | None, str | None]:
    if explicit and explicit.strip():
        return explicit.strip().upper(), "tag"
    return group_country(group)
