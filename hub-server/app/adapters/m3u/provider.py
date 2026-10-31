"""M3U playlist source adapter (URL or uploaded file)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.adapters.m3u.parser import M3UParser, iter_lines
from app.domain.entities import (
    ChannelInfo,
    EpgSourceSpec,
    ProgrammeInfo,
    SourceRef,
    StreamTarget,
    TestResult,
)
from app.domain.ports import AdapterContext, CatalogSink

log = logging.getLogger(__name__)

BATCH = 500


def guess_mime(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    if path.endswith((".m3u8", ".m3u")):
        return "application/x-mpegURL"
    if path.endswith(".mpd"):
        return "application/dash+xml"
    if path.endswith((".mp4", ".m4v")):
        return "video/mp4"
    if path.endswith(".mkv"):
        return "video/x-matroska"
    return "video/mp2t"


def _fmt_time(dt: datetime, fmt: str) -> str:
    """Expand flussonic/kodi style time placeholders."""
    return dt.strftime(fmt)


def build_catchup_url(channel: ChannelInfo, programme: ProgrammeInfo, now: datetime | None = None) -> str | None:
    """Support the common catchup types: default/append (template), shift, flussonic."""
    url = channel.stream_url
    if not url or not channel.catchup_type:
        return None
    now = now or datetime.now(UTC)
    start = programme.start.astimezone(UTC)
    stop = programme.stop.astimezone(UTC)
    duration = max(int((stop - start).total_seconds()), 60)
    offset = max(int((now - start).total_seconds()), 0)
    ctype = channel.catchup_type.lower()
    src = channel.catchup_source or ""

    def expand(tpl: str) -> str:
        out = tpl
        repl = {
            "{utc}": str(int(start.timestamp())),
            "${start}": str(int(start.timestamp())),
            "{start}": str(int(start.timestamp())),
            "{utcend}": str(int(stop.timestamp())),
            "${end}": str(int(stop.timestamp())),
            "{end}": str(int(stop.timestamp())),
            "{lutc}": str(int(now.timestamp())),
            "${timestamp}": str(int(now.timestamp())),
            "{timestamp}": str(int(now.timestamp())),
            "{duration}": str(duration),
            "${duration}": str(duration),
            "{offset}": str(offset),
            "${offset}": str(offset),
            "{Y}": start.strftime("%Y"),
            "{m}": start.strftime("%m"),
            "{d}": start.strftime("%d"),
            "{H}": start.strftime("%H"),
            "{M}": start.strftime("%M"),
            "{S}": start.strftime("%S"),
        }
        for k, v in repl.items():
            out = out.replace(k, v)
        # {utc:YmdHMS} / {start:Y-m-d} style
        import re

        def _fmt(m: re.Match[str]) -> str:
            which, fmt = m.group(1), m.group(2)
            base = stop if which in ("utcend", "end") else start
            pyfmt = "".join(f"%{c}" if c.isalpha() else c for c in fmt)
            return _fmt_time(base, pyfmt)

        out = re.sub(r"\$?\{(utc|start|utcend|end):([^}]+)\}", _fmt, out)
        out = out.replace("{duration:60}", str(duration // 60))
        return out

    if ctype in ("default", "") and src:
        return expand(src)
    if ctype == "append" and src:
        return url + expand(src)
    if ctype in ("shift", "timeshift"):
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}utc={int(start.timestamp())}&lutc={int(now.timestamp())}"
    if ctype == "flussonic" or ctype == "fs":
        base, _, tail = url.rpartition("/")
        ts = int(start.timestamp())
        if tail.endswith(".m3u8") or "mpegts" not in tail:
            return f"{base}/index-{ts}-{duration}.m3u8"
        return f"{base}/timeshift_abs-{ts}.ts"
    if src:
        return expand(src)
    return None


class M3USource:
    def __init__(self, ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> None:
        self.ref = ref
        self._cfg = config
        self._ctx = ctx

    # ---- config helpers
    @property
    def url(self) -> str | None:
        return self._cfg.get("url") or None

    @property
    def file_path(self) -> Path | None:
        f = self._cfg.get("file")
        if not f:
            return None
        p = Path(f)
        return p if p.is_absolute() else self._ctx.data_dir / "playlists" / p

    def _default_headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._cfg.get("user_agent"):
            h["User-Agent"] = str(self._cfg["user_agent"])
        if self._cfg.get("referer"):
            h["Referer"] = str(self._cfg["referer"])
        return h

    def config_summary(self) -> dict[str, Any]:
        from app.infra.logging import redact_url  # pure function, no I/O

        return {
            "url": redact_url(self.url) if self.url else None,
            "file": Path(str(self._cfg["file"])).name if self._cfg.get("file") else None,
            "epg_url": redact_url(self._cfg.get("epg_url")),
            "user_agent": self._cfg.get("user_agent"),
            "referer": self._cfg.get("referer"),
            "refresh_hours": int(self._cfg.get("refresh_hours") or 24),
            "proxy": self._cfg.get("proxy", "auto"),
            "max_connections": self._cfg.get("max_connections"),
        }

    def secrets(self) -> list[str]:
        return []

    # ---- reading
    async def _chunks(self) -> AsyncIterator[bytes]:
        fp = self.file_path
        if fp is not None:
            with fp.open("rb") as fh:
                while True:
                    chunk = fh.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk
            return
        if not self.url:
            raise ValueError("m3u source needs url or file")
        async with (
            self._ctx.http_factory(timeout=120.0, headers=self._default_headers()) as client,
            client.stream("GET", self.url) as resp,
        ):
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(256 * 1024):
                yield chunk

    def _parser(self) -> M3UParser:
        return M3UParser(default_headers=self._default_headers())

    async def iter_channels(self) -> AsyncIterator[ChannelInfo]:
        parser = self._parser()
        self._last_parser = parser
        async for ch in parser.parse(iter_lines(self._chunks())):
            yield ch

    async def test(self) -> TestResult:
        try:
            n = 0
            async for _ in self.iter_channels():
                n += 1
                if n >= 5:
                    break
            if n == 0:
                return TestResult(False, "Playlist contains no channels")
            return TestResult(True, "Playlist reachable")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return TestResult(False, f"Playlist error: {type(exc).__name__}")

    async def sync(self, sink: CatalogSink) -> None:
        batch: list[ChannelInfo] = []
        parser = self._parser()
        async for ch in parser.parse(iter_lines(self._chunks())):
            batch.append(ch)
            if len(batch) >= BATCH:
                await sink.add_channels(batch)
                batch = []
        if batch:
            await sink.add_channels(batch)
        await sink.set_config_hint("epg_urls", parser.header.epg_urls)

    # ---- EPG
    async def epg_sources(self) -> list[EpgSourceSpec]:
        urls: list[str] = []
        if self._cfg.get("epg_url"):
            urls.append(str(self._cfg["epg_url"]))
        hints = self._cfg.get("_hints") or {}
        for u in hints.get("epg_urls") or []:
            if u not in urls:
                urls.append(u)
        return [EpgSourceSpec(url=u, headers=self._default_headers()) for u in urls]

    # ---- playback
    async def stream_for_channel(self, channel: ChannelInfo) -> StreamTarget:
        url = channel.stream_url or ""
        return StreamTarget(url=url, mime=guess_mime(url), headers=dict(channel.headers), live=True)

    async def catchup_for_channel(
        self, channel: ChannelInfo, programme: ProgrammeInfo
    ) -> StreamTarget | None:
        url = build_catchup_url(channel, programme)
        if not url:
            return None
        duration = int((programme.stop - programme.start).total_seconds() * 1000)
        return StreamTarget(
            url=url, mime=guess_mime(url), headers=dict(channel.headers), live=False, duration_ms=duration
        )


def factory(ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> M3USource:
    return M3USource(ref, config, ctx)


def safe_filename(name: str) -> str:
    return quote(name, safe="").replace("%", "_")[:120] or "playlist.m3u"
