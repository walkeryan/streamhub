"""Seed a demo M3U source + EPG from local fixture files so the UI has content.

Usage (from hub-server/):
    CONFIG_DIR=./dev-config .venv/bin/python scripts/demo_seed.py

Creates (idempotently) a "Demo IPTV" M3U source on the admin profile, accepts the
disclaimer for it (this is a local demo, not a real IPTV provider), syncs channels
and loads a generated XMLTV guide covering the next 24 hours.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.container import build_container, shutdown, startup  # noqa: E402
from app.infra.models import Profile  # noqa: E402
from app.infra.settings import AppSettings  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO_NAME = "Demo IPTV"
SHOWS = ["Morning Brief", "World Report", "Live Studio", "Deep Dive", "Highlights", "Late Edition"]


def build_epg(path: Path, channel_ids: list[str]) -> None:
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="streamhub-demo">']
    for cid in channel_ids:
        parts.append(f'<channel id="{cid}"><display-name>{cid}</display-name></channel>')
    for cid in channel_ids:
        t = start
        i = 0
        while t < start + timedelta(hours=30):
            dur = timedelta(minutes=30 if i % 3 else 60)
            fmt = "%Y%m%d%H%M%S +0000"
            parts.append(
                f'<programme start="{t.strftime(fmt)}" stop="{(t + dur).strftime(fmt)}" channel="{cid}">'
                f"<title>{SHOWS[i % len(SHOWS)]}</title><desc>Demo programme {i + 1} on {cid}.</desc>"
                f"<category>Demo</category></programme>"
            )
            t += dur
            i += 1
    parts.append("</tv>")
    path.write_text("\n".join(parts), encoding="utf-8")


async def main() -> None:
    settings = AppSettings(enable_scheduler=False)
    c = build_container(settings)
    await startup(c, start_background=False)
    try:
        async with c.db.session() as s:
            admin = (await s.execute(select(Profile).where(Profile.is_admin.is_(True)))).scalars().first()
        assert admin is not None
        if not await c.disclaimer.has_accepted_current(admin.id):
            await c.disclaimer.accept(admin.id, None)

        pl_dir = settings.playlist_dir
        pl_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "demo.m3u", pl_dir / "demo.m3u")
        epg = settings.config_dir / "demo-epg.xml"
        build_epg(epg, ["demo.news.us", "demo.sport.uk", "demo.cinema.fr", "demo.kids.de", "demo.arabic", "demo.music"])

        existing = next((r for r in await c.sources.list() if r.name == DEMO_NAME), None)
        cfg = {"file": "demo.m3u", "epg_url": str(epg), "refresh_hours": 24}
        if existing is None:
            row = await c.sources.create(profile_id=admin.id, type_="m3u", name=DEMO_NAME, config=cfg)
        else:
            row = await c.sources.update(existing.id, {"config": cfg})
        await c.sync.sync_source(row.id)
        n = await c.sync.refresh_epg(row.id)
        row = await c.sources.get(row.id)
        print(f"Demo source {row.id}: status={row.status} counts={row.counts} programmes={n}")
    finally:
        await shutdown(c)


if __name__ == "__main__":
    asyncio.run(main())
