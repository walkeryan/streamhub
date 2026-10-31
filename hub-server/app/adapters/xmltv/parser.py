"""XMLTV EPG adapter: streaming lxml.iterparse with transparent gzip.

Downloads to a temp file (EPGs can be hundreds of MB) and parses incrementally,
clearing elements as it goes so memory stays flat.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Any

from lxml import etree

from app.domain.entities import EpgSourceSpec, ProgrammeInfo
from app.domain.ports import AdapterContext

log = logging.getLogger(__name__)

BATCH = 2000
_TS_RE = re.compile(r"^\s*(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?\s*([+-]\d{4}|Z|UTC|GMT)?")


def parse_xmltv_time(value: str | None) -> datetime | None:
    """``20261031183000 +0100`` → aware UTC datetime. Missing offset = UTC."""
    if not value:
        return None
    m = _TS_RE.match(value)
    if not m:
        return None
    y, mo, d, h, mi, s, off = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None
    tz: timezone = UTC
    if off and off not in ("Z", "UTC", "GMT"):
        sign = 1 if off[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
    return dt.replace(tzinfo=tz).astimezone(UTC)


def _open_maybe_gzip(path: Path) -> IO[bytes]:
    with path.open("rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rb")
    return path.open("rb")


def _text(el: Any, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    t: str = child.text.strip()
    return t or None


def iter_file(
    path: Path, wanted: set[str] | None = None, not_before: datetime | None = None
) -> Iterator[ProgrammeInfo | tuple[str, list[str], str | None]]:
    """Yield ProgrammeInfo items and (channel_id, display_names, icon) tuples."""
    with _open_maybe_gzip(path) as fh:
        ctx = etree.iterparse(
            fh, events=("end",), tag=("programme", "channel"), recover=True, huge_tree=True,
            resolve_entities=False, no_network=True,
        )
        for _, el in ctx:
            try:
                if el.tag == "channel":
                    cid = el.get("id")
                    if cid:
                        names = [n.text.strip() for n in el.findall("display-name") if n.text]
                        icon_el = el.find("icon")
                        yield (cid, names, icon_el.get("src") if icon_el is not None else None)
                    continue
                cid = el.get("channel")
                if not cid or (wanted is not None and cid not in wanted):
                    continue
                start = parse_xmltv_time(el.get("start"))
                stop = parse_xmltv_time(el.get("stop"))
                if start is None:
                    continue
                if stop is None or stop <= start:
                    stop = start + timedelta(minutes=30)
                if not_before and stop < not_before:
                    continue
                icon_el = el.find("icon")
                yield ProgrammeInfo(
                    channel=cid,
                    start=start,
                    stop=stop,
                    title=_text(el, "title") or "",
                    description=_text(el, "desc"),
                    category=_text(el, "category"),
                    icon=icon_el.get("src") if icon_el is not None else None,
                )
            finally:
                el.clear(keep_tail=False)
                parent = el.getparent()
                if parent is not None:
                    while el.getprevious() is not None:
                        del parent[0]


class XmltvEpgProvider:
    """Implements the EpgProvider port."""

    def __init__(self, ctx: AdapterContext) -> None:
        self._ctx = ctx

    async def _download(self, spec: EpgSourceSpec) -> Path:
        if spec.url.startswith("file://") or spec.url.startswith("/"):
            return Path(spec.url.removeprefix("file://"))
        tmpdir = self._ctx.data_dir / "cache"
        tmpdir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="epg-", suffix=".xml", dir=tmpdir)
        path = Path(name)
        try:
            with open(fd, "wb") as out:
                async with (
                    self._ctx.http_factory(timeout=300.0, headers=spec.headers) as client,
                    client.stream("GET", spec.url) as resp,
                ):
                    resp.raise_for_status()
                    # httpx decodes Content-Encoding: gzip; .gz files arrive raw and are sniffed later.
                    async for chunk in resp.aiter_bytes(512 * 1024):
                        out.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    async def iter_programmes(
        self, spec: EpgSourceSpec, wanted_channels: set[str] | None = None
    ) -> AsyncIterator[list[ProgrammeInfo]]:
        path = await self._download(spec)
        is_temp = not (spec.url.startswith("file://") or spec.url.startswith("/"))
        not_before = datetime.now(UTC) - timedelta(days=2)
        queue: asyncio.Queue[list[ProgrammeInfo] | None | BaseException] = asyncio.Queue(maxsize=4)
        loop = asyncio.get_running_loop()

        def worker() -> None:
            batch: list[ProgrammeInfo] = []
            try:
                for item in iter_file(path, wanted_channels, not_before):
                    if isinstance(item, ProgrammeInfo):
                        batch.append(item)
                        if len(batch) >= BATCH:
                            asyncio.run_coroutine_threadsafe(queue.put(batch), loop).result()
                            batch = []
                if batch:
                    asyncio.run_coroutine_threadsafe(queue.put(batch), loop).result()
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()
            except BaseException as exc:
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()

        task = loop.run_in_executor(None, worker)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            # drain so the worker can finish if the consumer stopped early
            while not task.done():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.01)
            if is_temp:
                path.unlink(missing_ok=True)


def parse_bytes(data: bytes, wanted: set[str] | None = None) -> list[ProgrammeInfo]:
    """Convenience for tests / small documents."""
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as fh:
        fh.write(data)
        p = Path(fh.name)
    try:
        return [x for x in iter_file(p, wanted) if isinstance(x, ProgrammeInfo)]
    finally:
        p.unlink(missing_ok=True)
