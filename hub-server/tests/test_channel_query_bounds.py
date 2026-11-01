from datetime import UTC, datetime, timedelta

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.infra.models import Channel, Programme, Source
from app.services.channels import now_next


async def test_epg_batch_preserves_source_identity_order_and_skips_invalid(api):
    now = datetime.now(UTC)
    async with api.c.db.session() as session:
        session.add_all(
            [Source(id=source, type="xtream", name=source, config_enc="unused") for source in ("one", "two")]
        )
        await session.commit()
        for source in ("one", "two"):
            for index in range(12):
                session.add(
                    Channel(
                        source_id=source,
                        ext_id=str(index),
                        name=f"{source}:{index}",
                        epg_id=f"epg-{index}",
                        group_id=f"group-{source}",
                        group_name="Synthetic",
                    )
                )
                session.add(
                    Programme(
                        feed=source,
                        epg_channel=f"epg-{index}",
                        title=f"{source} programme {index}",
                        start=now - timedelta(minutes=1),
                        stop=now + timedelta(hours=1),
                    )
                )
        await session.commit()
    queries = []

    def before(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(api.c.db.engine.sync_engine, "before_cursor_execute", before)
    try:
        one = await api.c.channels.epg_grid(api.profile_id, ["ch:one:0"], now, now + timedelta(minutes=30))
        one_count = len(queries)
        queries.clear()
        requested = ["ch:two:2", "bad", "lib:one:0", "ch:one:0", "ch:missing:1", "ch:two:2"] + [
            f"ch:one:{n}" for n in range(1, 12)
        ]
        many = await api.c.channels.epg_grid(api.profile_id, requested, now, now + timedelta(minutes=30))
        assert len(queries) == one_count == 2
        assert list(many)[:2] == ["ch:two:2", "ch:one:0"]
        assert many["ch:two:2"][0]["title"] == "two programme 2"
        assert one["ch:one:0"][0]["title"] == many["ch:one:0"][0]["title"] == "one programme 0"
        assert len(many) == 13
    finally:
        event.remove(api.c.db.engine.sync_engine, "before_cursor_execute", before)


async def test_now_next_loads_two_objects_per_exact_feed_and_keeps_overlap_evidence(api):
    now = datetime.now(UTC)
    channels = [Channel(source_id=source, ext_id="0", name=source, epg_id="shared") for source in ("one", "two")]
    async with api.c.db.session() as session:
        for source in ("one", "two"):
            session.add(Source(id=source, type="xtream", name=source, config_enc="unused"))
            for offset in (-2, -1, *range(1, 20)):
                session.add(
                    Programme(
                        feed=source,
                        epg_channel="shared",
                        title=f"{source}:{offset}",
                        start=now + timedelta(minutes=offset),
                        stop=now + timedelta(minutes=offset + 30),
                    )
                )
        await session.commit()
    loaded = []

    def on_load(_session, value):
        if isinstance(value, Programme):
            loaded.append(value)

    event.listen(Session, "loaded_as_persistent", on_load)
    try:
        result = await now_next(api.c.db, channels)
        assert len(loaded) == 4
        for source in ("one", "two"):
            current, upcoming = result[(source, "shared")]
            assert current.title == f"{source}:-2"
            assert upcoming.title == f"{source}:1"
        # Presentation selection leaves all source evidence intact for separate
        # policy overlap checks; no programmes are changed or deleted.
        async with api.c.db.session() as session:
            assert await session.scalar(select(func.count()).select_from(Programme)) == 42
    finally:
        event.remove(Session, "loaded_as_persistent", on_load)
