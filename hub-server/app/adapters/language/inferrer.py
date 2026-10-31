"""Language inference chain for IPTV channels.

Order (first hit wins), each recorded in ``language_source``:
  1. ``tag``     — tvg-language attribute
  2. ``db``      — iptv-org channels DB, matched on tvg-id (languages[0])
  3. ``country`` — tvg-country (or iptv-org country) → dominant language
  4. ``group``   — group-title prefix/suffix tokens ("EN|", "FR -", "|AR|", "DE:") or words ("ARABIC")
  5. ``flag``    — regional-indicator flag emoji in group/name → country → language
  6. ``name``    — channel-name keywords / tokens

User overrides (per-channel and per-group) are applied by the service layer and
recorded as ``user``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.adapters.language.data import (
    COUNTRY_LANG,
    EXTRA_PREFIX_TOKENS,
    ISO1_TO_3,
    ISO2B_TO_3,
    LANG_KEYWORDS,
    LANG_NAMES,
    NON_LANG_TOKENS,
)
from app.domain.entities import ChannelInfo, LanguageGuess, LanguageSource
from app.domain.ports import AdapterContext

log = logging.getLogger(__name__)

IPTV_ORG_TTL = 7 * 24 * 3600
_NAME_TO_3 = {v.upper(): k for k, v in LANG_NAMES.items()}
_FLAG_RE = re.compile("([\U0001f1e6-\U0001f1ff]{2})")
# token at start: "EN|", "EN -", "EN:", "[EN]", "(EN)", "|EN|"
_PREFIX_RE = re.compile(r"^\s*[\[\(|]?\s*([A-Za-z]{2,3}(?:-[A-Za-z]{2})?)\s*(?:[\]\)|:\-–•/]|\s-\s)")
_SUFFIX_RE = re.compile(r"(?:[\[\(|:\-–•/]|\s-\s)\s*([A-Za-z]{2,3})\s*[\]\)|]?\s*$")
_PIPE_TOKEN_RE = re.compile(r"\|\s*([A-Za-z]{2,3})\s*\|")
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]+")


def normalise_code(code: str | None) -> str | None:
    """Return ISO 639-3 for a 639-1/639-2/639-3 code or English language name."""
    if not code:
        return None
    c = code.strip().split(";")[0].split(",")[0].strip()
    if not c:
        return None
    low = c.lower()
    if low in LANG_NAMES:
        return low
    if low in ISO2B_TO_3:
        return ISO2B_TO_3[low]
    if low in ISO1_TO_3:
        return ISO1_TO_3[low]
    if len(low) > 2 and "-" in low:  # en-US
        return normalise_code(low.split("-")[0])
    up = c.upper()
    if up in _NAME_TO_3:
        return _NAME_TO_3[up]
    if up in LANG_KEYWORDS:
        return LANG_KEYWORDS[up]
    return None


def _token_to_lang(tok: str) -> str | None:
    t = tok.strip().upper()
    if not t or t in NON_LANG_TOKENS:
        return None
    if t in EXTRA_PREFIX_TOKENS:
        return EXTRA_PREFIX_TOKENS[t]
    if len(t) == 2:
        lang = ISO1_TO_3.get(t.lower())
        if lang:
            return lang
        return COUNTRY_LANG.get(t)
    if len(t) == 3:
        low = t.lower()
        if low in LANG_NAMES and low != "und":
            return low
        if low in ISO2B_TO_3:
            return ISO2B_TO_3[low]
    return None


def lang_from_group(group: str | None) -> str | None:
    if not group:
        return None
    g = group.strip()
    for rx in (_PIPE_TOKEN_RE, _PREFIX_RE, _SUFFIX_RE):
        m = rx.search(g)
        if m:
            lang = _token_to_lang(m.group(1))
            if lang:
                return lang
    for w in _WORD_RE.findall(g.upper()):
        if w in LANG_KEYWORDS:
            return LANG_KEYWORDS[w]
    return None


def lang_from_flag(*texts: str | None) -> str | None:
    for t in texts:
        if not t:
            continue
        m = _FLAG_RE.search(t)
        if m:
            cc = "".join(chr(ord(ch) - 0x1F1E6 + ord("A")) for ch in m.group(1))
            lang = COUNTRY_LANG.get(cc)
            if lang:
                return lang
    return None


def lang_from_name(name: str | None) -> str | None:
    if not name:
        return None
    up = name.upper()
    for w in _WORD_RE.findall(up):
        if w in LANG_KEYWORDS:
            return LANG_KEYWORDS[w]
    # prefix tokens on names too: "UK: BBC One", "AR - MBC"
    for rx in (_PIPE_TOKEN_RE, _PREFIX_RE):
        m = rx.search(name)
        if m:
            lang = _token_to_lang(m.group(1))
            if lang:
                return lang
    return None


class LanguageChain:
    """Implements the LanguageInferrer port."""

    def __init__(self, ctx: AdapterContext | None = None, url: str | None = None) -> None:
        self._ctx = ctx
        self._url = url or "https://iptv-org.github.io/api/channels.json"
        self._db: dict[str, dict[str, Any]] = {}

    @property
    def db_size(self) -> int:
        return len(self._db)

    def load_db(self, rows: list[dict[str, Any]]) -> None:
        db: dict[str, dict[str, Any]] = {}
        for r in rows:
            cid = r.get("id")
            if not isinstance(cid, str):
                continue
            langs = r.get("languages") or []
            db[cid.lower()] = {
                "languages": [x for x in langs if isinstance(x, str)],
                "country": r.get("country"),
            }
        self._db = db

    async def prepare(self) -> None:
        if self._db or self._ctx is None:
            return
        cache_key = "iptv-org:channels:v1"
        try:
            cached = await self._ctx.cache.get(cache_key)
            if isinstance(cached, list):
                self.load_db(cached)
                return
            async with self._ctx.http_factory(timeout=60.0) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                data = resp.json()
            if isinstance(data, list):
                # keep only what we need to keep the cache row small
                slim = [
                    {"id": r.get("id"), "languages": r.get("languages") or [], "country": r.get("country")}
                    for r in data
                    if isinstance(r, dict)
                ]
                await self._ctx.cache.set(cache_key, slim, IPTV_ORG_TTL)
                self.load_db(slim)
        except Exception as exc:  # never fail a sync because of this
            log.warning("iptv-org channels DB unavailable", extra={"err": type(exc).__name__})

    def _db_lookup(self, tvg_id: str | None) -> dict[str, Any] | None:
        if not tvg_id or not self._db:
            return None
        key = tvg_id.strip().lower()
        hit = self._db.get(key)
        if hit is None and "@" in key:  # iptv-org feed ids: "BBCOne.uk@HD"
            hit = self._db.get(key.split("@", 1)[0])
        return hit

    def infer(self, channel: ChannelInfo) -> LanguageGuess:
        # 1. explicit tag
        lang = normalise_code(channel.tvg_language)
        if lang and lang != "und":
            return LanguageGuess(lang, LanguageSource.TAG)
        # 2. iptv-org DB
        hit = self._db_lookup(channel.tvg_id)
        if hit:
            for code in hit.get("languages") or []:
                lang = normalise_code(code)
                if lang:
                    return LanguageGuess(lang, LanguageSource.DB)
        # 3. country
        country = (channel.tvg_country or (hit or {}).get("country") or "").strip().upper()
        if country:
            lang = COUNTRY_LANG.get(country.split(",")[0].split(";")[0].strip())
            if lang:
                return LanguageGuess(lang, LanguageSource.COUNTRY)
        # 4. group-title
        lang = lang_from_group(channel.group)
        if lang:
            return LanguageGuess(lang, LanguageSource.GROUP)
        # 5. flag emoji
        lang = lang_from_flag(channel.group, channel.name)
        if lang:
            return LanguageGuess(lang, LanguageSource.FLAG)
        # 6. channel name
        lang = lang_from_name(channel.name)
        if lang:
            return LanguageGuess(lang, LanguageSource.NAME)
        return LanguageGuess("und", None)

    def language_name(self, code: str) -> str:
        return LANG_NAMES.get(code, code)

    def normalise(self, code: str | None) -> str | None:
        return normalise_code(code)
