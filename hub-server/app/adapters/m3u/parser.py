"""Streaming, line-based M3U/M3U8 (extended IPTV) parser.

Memory use is O(1) in the playlist size: input is consumed as an async stream of
byte chunks and channels are yielded one at a time.

Supported:
- header ``#EXTM3U`` attributes ``url-tvg`` / ``x-tvg-url`` (comma separated lists),
  ``catchup``, ``catchup-source``, ``catchup-days`` defaults
- ``#EXTINF:-1 key="value" …,Display name`` with attributes tvg-id, tvg-name,
  tvg-logo, tvg-language, tvg-country, tvg-chno, group-title, catchup,
  catchup-source, catchup-days (and ``timeshift`` as alias)
- ``#EXTGRP:`` group lines
- ``#EXTVLCOPT:http-user-agent=…`` / ``http-referrer=…`` (also ``http-referer``)
- ``#KODIPROP:inputstream.adaptive.stream_headers=…`` headers
- ``url|User-Agent=..&Referer=..`` pipe-header syntax
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

from app.domain.entities import ChannelInfo

_ATTR_RE = re.compile(r'([A-Za-z0-9_\-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,]+))')


@dataclass(slots=True)
class M3UHeader:
    epg_urls: list[str] = field(default_factory=list)
    catchup: str | None = None
    catchup_source: str | None = None
    catchup_days: int = 0
    attrs: dict[str, str] = field(default_factory=dict)


def parse_attributes(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(text):
        key = m.group(1).lower()
        val = m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(4)
        out[key] = (val or "").strip()
    return out


def split_extinf(line: str) -> tuple[str, str]:
    """Split ``#EXTINF:-1 attrs,Name`` into (attrs, name), respecting quotes."""
    body = line[len("#EXTINF:") :]
    in_quote: str | None = None
    for i, ch in enumerate(body):
        if in_quote:
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == ",":
            return body[:i], body[i + 1 :].strip()
    return body, ""


def _int(value: str | None, default: int = 0) -> int:
    if not value:
        return default
    m = re.match(r"\s*(-?\d+)", value)
    return int(m.group(1)) if m else default


def stable_ext_id(url: str, tvg_id: str | None, name: str) -> str:
    """Stable id across refreshes: prefer URL (unique per stream)."""
    h = hashlib.sha1(f"{url}".encode(), usedforsecurity=False).hexdigest()[:16]
    return h if url else hashlib.sha1(f"{tvg_id}|{name}".encode(), usedforsecurity=False).hexdigest()[:16]


def _split_pipe_headers(url: str) -> tuple[str, dict[str, str]]:
    if "|" not in url:
        return url, {}
    base, _, hdrs = url.partition("|")
    headers: dict[str, str] = {}
    for k, v in parse_qsl(hdrs, keep_blank_values=True):
        kl = k.lower()
        if kl in ("user-agent", "http-user-agent"):
            headers["User-Agent"] = v
        elif kl in ("referer", "referrer", "http-referrer", "http-referer"):
            headers["Referer"] = v
        elif k:
            headers[k] = v
    return base, headers


async def iter_lines(chunks: AsyncIterable[bytes], encoding: str = "utf-8") -> AsyncIterator[str]:
    """Decode an async byte stream into lines without buffering the whole body."""
    import codecs

    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    buf = ""
    first = True
    async for chunk in chunks:
        text = decoder.decode(chunk)
        if first:
            text = text.lstrip("﻿")
            first = False
        buf += text
        if "\n" not in buf and "\r" not in buf:
            continue
        lines = buf.splitlines(keepends=True)
        buf = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            buf = lines.pop()
        for ln in lines:
            yield ln.rstrip("\r\n")
    buf += decoder.decode(b"", final=True)
    if buf:
        for ln in buf.splitlines():
            yield ln


class M3UParser:
    """Stateful parser. Use :meth:`parse` (async) or :meth:`parse_lines` (sync)."""

    def __init__(self, default_headers: dict[str, str] | None = None) -> None:
        self.header = M3UHeader()
        self._default_headers = dict(default_headers or {})
        self._reset()
        self._index = 0

    def _reset(self) -> None:
        self._attrs: dict[str, str] | None = None
        self._name = ""
        self._group: str | None = None
        self._opts: dict[str, str] = {}

    def _feed(self, raw: str) -> ChannelInfo | None:
        line = raw.strip()
        if not line:
            return None
        if line.startswith("#"):
            upper = line[:12].upper()
            if upper.startswith("#EXTM3U"):
                attrs = parse_attributes(line[7:])
                self.header.attrs.update(attrs)
                for key in ("url-tvg", "x-tvg-url", "tvg-url"):
                    if attrs.get(key):
                        for u in re.split(r"[,\s]+", attrs[key]):
                            if u and u not in self.header.epg_urls:
                                self.header.epg_urls.append(u)
                self.header.catchup = attrs.get("catchup") or self.header.catchup
                self.header.catchup_source = attrs.get("catchup-source") or self.header.catchup_source
                self.header.catchup_days = _int(attrs.get("catchup-days"), self.header.catchup_days)
            elif upper.startswith("#EXTINF"):
                attr_txt, name = split_extinf(line)
                self._attrs = parse_attributes(attr_txt)
                self._name = name
                self._group = None
                self._opts = {}
            elif upper.startswith("#EXTGRP"):
                self._group = line.split(":", 1)[1].strip() if ":" in line else None
            elif upper.startswith("#EXTVLCOPT"):
                opt = line.split(":", 1)[1] if ":" in line else ""
                k, _, v = opt.partition("=")
                k = k.strip().lower()
                v = v.strip()
                if k == "http-user-agent":
                    self._opts["User-Agent"] = v
                elif k in ("http-referrer", "http-referer"):
                    self._opts["Referer"] = v
                elif k == "http-origin":
                    self._opts["Origin"] = v
            elif upper.startswith("#KODIPROP"):
                opt = line.split(":", 1)[1] if ":" in line else ""
                k, _, v = opt.partition("=")
                if k.strip().lower().endswith("stream_headers"):
                    _, hdrs = _split_pipe_headers("x|" + v)
                    self._opts.update(hdrs)
            return None
        # URL line
        if self._attrs is None:
            # bare URL with no EXTINF — still a channel
            self._attrs = {}
            self._name = line.rsplit("/", 1)[-1]
        url, pipe_headers = _split_pipe_headers(line)
        attrs = self._attrs
        headers = dict(self._default_headers)
        headers.update(pipe_headers)
        headers.update(self._opts)
        name = self._name or attrs.get("tvg-name") or url
        group = attrs.get("group-title") or self._group or "Uncategorized"
        catchup = attrs.get("catchup") or attrs.get("catchup-type") or self.header.catchup
        catchup_days = _int(attrs.get("catchup-days") or attrs.get("timeshift"), self.header.catchup_days)
        catchup_source = attrs.get("catchup-source") or self.header.catchup_source
        tvg_id = attrs.get("tvg-id") or None
        chno = attrs.get("tvg-chno") or attrs.get("channel-number")
        self._index += 1
        info = ChannelInfo(
            ext_id=stable_ext_id(url, tvg_id, name),
            name=name.strip(),
            stream_url=url,
            logo=attrs.get("tvg-logo") or attrs.get("logo") or None,
            group=group.strip() or "Uncategorized",
            tvg_id=tvg_id,
            tvg_name=attrs.get("tvg-name") or None,
            tvg_language=attrs.get("tvg-language") or None,
            tvg_country=attrs.get("tvg-country") or None,
            number=_int(chno, 0) or None,
            catchup_days=catchup_days if catchup else 0,
            catchup_type=catchup or None,
            catchup_source=catchup_source if catchup else None,
            headers=headers,
            extra={"index": self._index},
        )
        self._reset()
        return info

    def parse_lines(self, lines: Iterable[str]) -> Iterable[ChannelInfo]:
        for ln in lines:
            ch = self._feed(ln)
            if ch is not None:
                yield ch

    async def parse(self, lines: AsyncIterable[str]) -> AsyncIterator[ChannelInfo]:
        async for ln in lines:
            ch = self._feed(ln)
            if ch is not None:
                yield ch
