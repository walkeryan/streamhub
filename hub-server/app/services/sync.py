"""SyncService (catalog + EPG refresh) and SyncScheduler (APScheduler).

Each run is recorded in ``sync_run``; ``Source.status`` reflects the latest run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, insert, select, update

from app.domain.entities import SourceStatus, SourceType
from app.domain.ports import EpgProvider, EpgSourceProvider, InteractiveAuthProvider
from app.infra.db import Database
from app.infra.logging import redact_text
from app.infra.models import Channel, Programme, Source, SyncRun, utcnow
from app.services.settings_service import SettingsService
from app.services.sources import DbCatalogSink, SourceService

log = logging.getLogger(__name__)

EPG_RETENTION = timedelta(days=2)
DEFAULT_REFRESH_HOURS = {
    SourceType.M3U.value: 24,
    SourceType.XTREAM.value: 24,
    SourceType.PLEX.value: 6,
    SourceType.JELLYFIN.value: 6,
}


class SyncService:
    def __init__(self, db: Database, sources: SourceService, epg: EpgProvider, settings: SettingsService) -> None:
        self._db = db
        self._sources = sources
        self._epg = epg
        self._settings = settings
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def is_running(self, source_id: str) -> bool:
        lk = self._locks.get(source_id)
        return bool(lk and lk.locked())

    def spawn(self, source_id: str, *, with_epg: bool = True) -> None:
        async def _run() -> None:
            await self.sync_source(source_id)
            if with_epg:
                await self.refresh_epg(source_id)

        t = asyncio.create_task(_run())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _start_run(self, source_id: str | None, job: str) -> int:
        async with self._db.session() as s:
            run = SyncRun(source_id=source_id, job=job)
            s.add(run)
            await s.commit()
            return run.id

    async def _finish_run(self, run_id: int, ok: bool, message: str | None, stats: dict[str, Any]) -> None:
        async with self._db.session() as s:
            run = await s.get(SyncRun, run_id)
            if run:
                run.finished_at = utcnow()
                run.status = "ok" if ok else "error"
                run.message = message
                run.stats = stats
                await s.commit()

    # ------------------------------------------------------------------ catalog
    async def sync_source(self, source_id: str) -> bool:
        lock = self._lock(source_id)
        if lock.locked():
            return False
        async with lock:
            try:
                row = await self._sources.get(source_id)
            except Exception:
                return False
            if row.type == SourceType.STREAMING_SERVICE.value:
                await self._sources.set_status(source_id, SourceStatus.OK, last_sync=utcnow())
                return True
            run_id = await self._start_run(source_id, "catalog")
            await self._sources.set_status(source_id, SourceStatus.SYNCING)
            prov = self._sources.provider(row)
            try:
                if isinstance(prov, InteractiveAuthProvider):
                    done, upd = await prov.complete_auth()
                    if upd:
                        await self._sources.update_config(source_id, upd)
                    if not done:
                        await self._sources.set_status(source_id, SourceStatus.UNAUTHENTICATED)
                        await self._finish_run(run_id, False, "awaiting authentication", {})
                        return False
                    row = await self._sources.get(source_id)
                    prov = self._sources.provider(row)
                if row.type in (SourceType.M3U.value, SourceType.XTREAM.value):
                    await self._sources.language.prepare()
                gen = int((row.hints or {}).get("sync_gen") or 0) + 1
                sink = DbCatalogSink(self._db, row, gen, self._sources.language)
                await prov.sync(sink)
                counts = await sink.finalize()
                hints = dict(row.hints or {})
                hints.update(sink.hints)
                hints["sync_gen"] = gen
                async with self._db.session() as s:
                    await s.execute(update(Source).where(Source.id == source_id).values(hints=hints))
                    await s.commit()
                await self._sources.set_status(source_id, SourceStatus.OK, last_sync=utcnow(), counts=counts)
                await self._finish_run(run_id, True, None, counts)
                log.info("source synced", extra={"source": source_id, **counts})
                return True
            except PermissionError as exc:
                msg = redact_text(str(exc))
                await self._sources.set_status(source_id, SourceStatus.UNAUTHENTICATED, error=msg)
                await self._finish_run(run_id, False, msg, {})
            except Exception as exc:
                msg = redact_text(f"{type(exc).__name__}: {exc}")[:500]
                log.warning("source sync failed", extra={"source": source_id, "err": msg})
                await self._sources.set_status(source_id, SourceStatus.ERROR, error=msg)
                await self._finish_run(run_id, False, msg, {})
            return False

    # ------------------------------------------------------------------ EPG
    async def refresh_epg(self, source_id: str) -> int:
        lock = self._lock("epg:" + source_id)
        if lock.locked():
            return 0
        async with lock:
            try:
                row = await self._sources.get(source_id)
            except Exception:
                return 0
            prov = self._sources.provider(row)
            if not isinstance(prov, EpgSourceProvider):
                return 0
            specs = await prov.epg_sources()
            if not specs:
                return 0
            async with self._db.session() as s:
                wanted = {
                    e
                    for e in (await s.execute(select(Channel.epg_id).where(Channel.source_id == source_id))).scalars()
                    if e
                }
            if not wanted:
                return 0
            run_id = await self._start_run(source_id, "epg")
            total = 0
            errors: list[str] = []
            # Stage the whole refresh before replacing anything. A later feed or parser
            # failure must not erase a previously usable schedule. Disk staging also
            # keeps large provider feeds out of memory and avoids a network-long DB lock.
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as staged:
                for spec in specs:
                    try:
                        async for batch in self._epg.iter_programmes(spec, wanted):
                            for p in batch:
                                staged.write(
                                    json.dumps(
                                        {
                                            "epg_channel": p.channel,
                                            "feed": source_id,
                                            "start": p.start.isoformat(),
                                            "stop": p.stop.isoformat(),
                                            "title": p.title[:512],
                                            "description": p.description,
                                            "category": (p.category or None) and p.category[:128],
                                            "icon": p.icon,
                                            "age_evidence": {
                                                **p.age_evidence,
                                                "subject": {
                                                    "source_id": source_id,
                                                    "epg_channel": p.channel,
                                                    "start": p.start.isoformat(),
                                                    "stop": p.stop.isoformat(),
                                                },
                                            },
                                        }
                                    )
                                    + "\n"
                                )
                            total += len(batch)
                    except Exception as exc:
                        errors.append(redact_text(f"{type(exc).__name__}: {exc}")[:200])
                if errors or not total:
                    await self._finish_run(
                        run_id,
                        False,
                        "; ".join(errors) or "No matching programmes; previous guide retained",
                        {"programmes": 0, "previous_guide_retained": True},
                    )
                    return 0
                staged.seek(0)
                async with self._db.session() as s:
                    await s.execute(delete(Programme).where(Programme.feed == source_id))
                    values: list[dict[str, Any]] = []
                    for line in staged:
                        value = json.loads(line)
                        value["start"] = datetime.fromisoformat(value["start"])
                        value["stop"] = datetime.fromisoformat(value["stop"])
                        values.append(value)
                        if len(values) >= 2000:
                            await s.execute(insert(Programme), values)
                            values.clear()
                    if values:
                        await s.execute(insert(Programme), values)
                    src = await s.get(Source, source_id)
                    if src:
                        counts = dict(src.counts or {})
                        counts["programmes"] = total
                        src.counts = counts
                    await s.commit()
            await self._finish_run(run_id, True, None, {"programmes": total})
            return total

    async def cleanup_epg(self) -> int:
        cutoff = datetime.now(UTC) - EPG_RETENTION
        async with self._db.session() as s:
            res = await s.execute(delete(Programme).where(Programme.stop < cutoff))
            await s.execute(delete(SyncRun).where(SyncRun.started_at < datetime.now(UTC) - timedelta(days=14)))
            await s.commit()
        return int(getattr(res, "rowcount", 0) or 0)

    # ------------------------------------------------------------------ scheduling decisions
    def refresh_hours(self, row: Source) -> int:
        cfg = self._sources.decrypt_config(row)
        try:
            h = int(cfg.get("refresh_hours") or DEFAULT_REFRESH_HOURS.get(row.type, 24))
        except (TypeError, ValueError):
            h = 24
        return max(1, h)

    async def due_sources(self) -> list[str]:
        now = utcnow()
        out = []
        for row in await self._sources.list():
            if not row.enabled or row.type == SourceType.STREAMING_SERVICE.value or self.is_running(row.id):
                continue
            if row.last_sync is None or now - row.last_sync >= timedelta(hours=self.refresh_hours(row)):
                out.append(row.id)
        return out

    async def tick(self) -> None:
        for sid in await self.due_sources():
            await self.sync_source(sid)
            await self.refresh_epg(sid)

    async def epg_tick(self) -> None:
        hours = int(self._settings.get("epg_refresh_hours") or 12)
        last = self._settings.get("_last_epg_refresh")
        now = utcnow()
        if isinstance(last, str):
            try:
                if now - datetime.fromisoformat(last) < timedelta(hours=hours):
                    return
            except ValueError:
                pass
        for row in await self._sources.list():
            if row.enabled and row.type in (SourceType.M3U.value, SourceType.XTREAM.value):
                await self.refresh_epg(row.id)
        await self._settings._put("_last_epg_refresh", now.isoformat())
        await self.cleanup_epg()

    async def reset_stuck(self) -> None:
        """On boot, sources left in 'syncing' by a crash are marked for resync."""
        async with self._db.session() as s:
            await s.execute(
                update(Source).where(Source.status == SourceStatus.SYNCING.value).values(status="error", last_sync=None)
            )
            await s.execute(
                update(SyncRun).where(SyncRun.status == "running").values(status="error", message="interrupted")
            )
            await s.commit()


class SyncScheduler:
    def __init__(self, sync: SyncService) -> None:
        self._sync = sync
        self._sched: Any = None

    def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        sched = AsyncIOScheduler(timezone="UTC")
        sched.add_job(
            self._sync.tick,
            "interval",
            minutes=5,
            id="catalog",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(UTC) + timedelta(seconds=10),
        )
        sched.add_job(
            self._sync.epg_tick,
            "interval",
            minutes=30,
            id="epg",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(UTC) + timedelta(minutes=2),
        )
        sched.start()
        self._sched = sched

    def shutdown(self) -> None:
        if self._sched is not None:
            self._sched.shutdown(wait=False)
            self._sched = None
