from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.entities import EpgSourceSpec, ProgrammeInfo
from app.infra.models import Programme
from tests.conftest import FIXTURES, Api


async def add_source(api: Api, name: str = "Test") -> dict[str, str]:
    # This test controls EPG explicitly; do not schedule the fixture's remote feed.
    await api.accept_disclaimer()
    playlist = "#EXTM3U\n" + (FIXTURES / "sample.m3u").read_text().split("\n", 1)[1]
    response = await api.post("/sources", json={"type": "m3u", "name": name, "config": {"file": playlist}})
    assert response.status_code == 201
    await api.c.sync.wait_idle()
    return response.json()


async def test_epg_ids_are_scoped_to_their_source(api: Api) -> None:
    first = await add_source(api, name="First")
    second = await add_source(api, name="Second")
    now = datetime.now(UTC)
    async with api.c.db.session() as session:
        for source, title in [(first, "First schedule"), (second, "Second schedule")]:
            session.add(
                Programme(
                    feed=source["id"],
                    epg_channel="bbc1.uk",
                    start=now - timedelta(minutes=10),
                    stop=now + timedelta(minutes=30),
                    title=title,
                )
            )
        await session.commit()
    channels = (await api.get("/channels", params={"language": "all"})).json()["channels"]
    bbc = [c for c in channels if c["epg_id"] == "bbc1.uk"]
    assert len(bbc) == 2
    expected = {first["id"]: "First schedule", second["id"]: "Second schedule"}
    for ch in bbc:
        assert ch["now"]["title"] == expected[ch["source_id"]]
    grid = (await api.get("/epg", params={"channel_ids": ",".join(c["id"] for c in bbc)})).json()
    for ch in bbc:
        assert [p["title"] for p in grid[ch["id"]]] == [expected[ch["source_id"]]]


@pytest.mark.parametrize("failure", [True, False])
async def test_epg_refresh_preserves_previous_on_failed_or_empty_feed(
    api: Api,
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    source = await add_source(api)
    now = datetime.now(UTC)
    async with api.c.db.session() as session:
        session.add(
            Programme(
                feed=source["id"],
                epg_channel="bbc1.uk",
                start=now,
                stop=now + timedelta(hours=1),
                title="Previous good guide",
            )
        )
        await session.commit()

    class Provider:
        async def epg_sources(self) -> list[EpgSourceSpec]:
            return [EpgSourceSpec(url="https://epg.invalid/test.xml")]

    class Epg:
        async def iter_programmes(self, spec: EpgSourceSpec, wanted: set[str]) -> AsyncIterator[list[ProgrammeInfo]]:
            if failure:
                yield [ProgrammeInfo(channel="bbc1.uk", start=now, stop=now + timedelta(hours=1), title="Partial")]
                raise ValueError("incomplete feed")
            if False:
                yield []

    monkeypatch.setattr(api.c.sources, "provider", lambda row: Provider())
    monkeypatch.setattr(api.c.sync, "_epg", Epg())
    assert await api.c.sync.refresh_epg(source["id"]) == 0
    async with api.c.db.session() as session:
        titles = list((await session.execute(select(Programme.title).where(Programme.feed == source["id"]))).scalars())
    assert titles == ["Previous good guide"]


async def test_epg_refresh_atomically_replaces_complete_feed(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    source = await add_source(api)
    now = datetime.now(UTC)
    async with api.c.db.session() as session:
        session.add(
            Programme(feed=source["id"], epg_channel="bbc1.uk", start=now, stop=now + timedelta(hours=1), title="Old")
        )
        await session.commit()

    class Provider:
        async def epg_sources(self) -> list[EpgSourceSpec]:
            return [EpgSourceSpec(url="https://epg.invalid/test.xml")]

    class Epg:
        async def iter_programmes(self, spec: EpgSourceSpec, wanted: set[str]) -> AsyncIterator[list[ProgrammeInfo]]:
            for n in range(2):
                yield [
                    ProgrammeInfo(
                        channel="bbc1.uk",
                        start=now + timedelta(hours=n),
                        stop=now + timedelta(hours=n + 1),
                        title=f"Complete {n}",
                    )
                ]

    monkeypatch.setattr(api.c.sources, "provider", lambda row: Provider())
    monkeypatch.setattr(api.c.sync, "_epg", Epg())
    assert await api.c.sync.refresh_epg(source["id"]) == 2
    async with api.c.db.session() as session:
        titles = list(
            (
                await session.execute(
                    select(Programme.title).where(Programme.feed == source["id"]).order_by(Programme.start)
                )
            ).scalars()
        )
    assert titles == ["Complete 0", "Complete 1"]
