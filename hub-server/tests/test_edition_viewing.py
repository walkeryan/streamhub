"""A viewer owns the edition timeline, the scene and the file license independently."""

from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.infra.models import Source, Title
from app.services.editions import edition_info
from tests.conftest import Api
from tests.test_viewing_preferences import REF, seed


async def test_scene_bookmark_and_stale_edition_report(api: Api):
    await seed(api)
    async with api.c.db.session() as session:
        rows = list(await session.scalars(select(Title)))
        for row in rows:
            row.duration_ms = 42_000 if row.ext_id == "fr" else 120_000
            row.extra = {**row.extra, "edition_title": "Theatrical cut" if row.ext_id == "fr" else "Director's cut"}
        await session.commit()
    editions = {row.ext_id: edition_info(row, rows)["edition_id"] for row in rows}
    assert editions["unknown"] == editions["en"] != editions["fr"]

    async def start(ext):
        return await api.c.progress.start_edition(api.profile_id, REF, editions[ext], "lib:iptv:" + ext)

    async def report(token, ext, pos, dur):
        return await api.post(
            "/playback/progress",
            json={
                "id": REF,
                "state": "stopped",
                "variant_id": "lib:iptv:" + ext,
                "position_ms": pos,
                "duration_ms": dur,
                "progress_session_id": token,
            },
        )

    a = await start("en")
    assert (await report(a, "en", 80000, 120000)).status_code == 204
    saved = await api.post("/viewing/bookmarks", json={"name": "End of the chase", "position_ms": 80000, "session": a})
    assert saved.status_code == 201, saved.text
    mark = saved.json()["id"]
    other = await api.c.auth.issue(await api.c.auth.get_profile("another-viewer"))
    params = {"headers": {"Authorization": "Bearer " + other["access_token"]}}
    assert (await api.get("/viewing/bookmarks", params={"id": REF}, **params)).json() == []
    assert (await api.get(f"/viewing/bookmarks/{mark}/restore", **params)).status_code == 404
    await api.delete(f"/viewing/bookmarks/{mark}", **params)
    assert (await api.get(f"/viewing/bookmarks/{mark}/restore")).json() == {
        "id": REF,
        "position_ms": 80000,
        "variant_id": "lib:iptv:en",
    }
    b = await start("fr")
    assert await api.c.progress.edition_resume(api.profile_id, REF, editions["fr"], "lib:iptv:fr", 42000, None) == 0
    assert (await report(b, "fr", 19000, 42000)).status_code == 204
    assert (await report(a, "en", 2000, 120000)).status_code == 204
    assert (await api.c.progress.get(api.profile_id, REF)).position_ms == 19000
    assert (
        await api.c.progress.edition_resume(api.profile_id, REF, editions["unknown"], "lib:iptv:unknown", 120000, None)
        == 80000
    )
    assert (
        await api.post("/viewing/bookmarks", json={"name": "Stale", "position_ms": 10, "session": a})
    ).status_code == 409
    assert (
        await api.post(
            "/viewing/bookmarks", json={"name": "unsafe image", "position_ms": 10, "session": b, "preview": "Zm9v"}
        )
    ).status_code == 400
    assert (await api.delete(f"/viewing/bookmarks/{mark}")).status_code == 204


async def test_offline_license_permission_actual_bytes_session_and_reconciliation(api: Api):
    async with api.c.db.session() as s:
        s.add(
            Source(
                id="j",
                name="Personal",
                type="jellyfin",
                enabled=True,
                config_enc=api.c.crypto.encrypt_json(
                    {"server_url": "http://j.invalid", "access_token": "test", "user_id": "u"}
                ),
            )
        )
        await s.flush()
        s.add(Title(source_id="j", ext_id="e", title="Episode", kind="episode", duration_ms=120000))
        await s.commit()
    source, provider = await api.c.sources.provider_for("j")
    ref = "lib:j:e"
    body = b"permitted-media-bytes"
    info = {
        "Type": "Episode",
        "CanDownload": False,
        "MediaSources": [{"Size": len(body), "Container": "mp4", "Id": "file"}],
    }

    async def chunks(*_):
        yield body[:8]
        yield body[8:]

    params = {"id": ref, "max_bytes": 200}
    with (
        patch.object(api.c.sources, "provider_for", AsyncMock(return_value=(source, provider))),
        patch.object(provider, "_json", AsyncMock(return_value=info)),
        patch.object(provider, "download_bytes", chunks),
    ):
        assert (await api.post("/viewing/downloads", params=params)).status_code == 403
        info["CanDownload"] = True
        assert (await api.post("/viewing/downloads", params={**params, "max_bytes": 4})).status_code == 409
        grant = await api.post("/viewing/downloads", params=params)
        assert grant.status_code == 201, grant.text
        path = "/viewing/downloads/" + grant.json()["id"]
        assert (await api.get(path + "/content")).content == body
        same_profile_other_session = await api.c.auth.issue(await api.c.auth.get_profile(api.profile_id))
        assert (
            await api.get(path, headers={"Authorization": "Bearer " + same_profile_other_session["access_token"]})
        ).status_code == 404
        progress = {"position_ms": 21000, "duration_ms": 120000}
        assert (await api.post(path + "/progress", params=progress)).status_code == 204
        assert (await api.c.progress.get(api.profile_id, ref)).position_ms == 21000
        await api.c.progress.start_edition(api.profile_id, ref, grant.json().get("edition_id", "new"), ref)
        assert (await api.post(path + "/progress", params=progress)).status_code == 409
        info["CanDownload"] = False
        assert (await api.get(path + "/content")).status_code == 403
        assert (await api.get(path)).status_code == 403


async def test_catalog_resync_preserves_unnamed_scene_and_latest_progress(api: Api):
    from sqlalchemy import delete

    from tests.test_audio_selection import row, tracks

    await seed(api)
    async with api.c.db.session() as s:
        await s.execute(delete(Title).where(Title.ext_id != "en"))
        original = await s.scalar(select(Title))
        original.duration_ms = 120000
        await s.commit()
    variant = "lib:iptv:en"
    a = await api.c.progress.start_edition(api.profile_id, REF, "runtime:120000", variant)
    await api.c.progress.report(
        api.profile_id,
        REF,
        position_ms=70000,
        duration_ms=120000,
        state="paused",
        source_id="iptv",
        progress_session_id=a,
    )
    mark = (await api.post("/viewing/bookmarks", json={"session": a, "position_ms": 61000, "name": "Arrival"})).json()[
        "id"
    ]
    async with api.c.db.session() as s:
        short = row("4k", tracks("eng"))
        short.duration_ms = 119400
        s.add(short)
        await s.commit()
    restored = await api.get(f"/viewing/bookmarks/{mark}/restore")
    assert restored.status_code == 200 and restored.json()["variant_id"] == variant
    assert (
        await api.c.progress.edition_resume(api.profile_id, REF, "runtime:119400", "lib:iptv:4k", 119400, None) == 70000
    )
    b = await api.c.progress.start_edition(api.profile_id, REF, "runtime:119400", "lib:iptv:4k")
    await api.c.progress.report(
        api.profile_id,
        REF,
        position_ms=32000,
        duration_ms=119400,
        state="paused",
        source_id="iptv",
        progress_session_id=b,
    )
    async with api.c.db.session() as s:
        await s.execute(delete(Title).where(Title.ext_id == "4k"))
        await s.commit()
    assert await api.c.progress.edition_resume(api.profile_id, REF, "runtime:120000", variant, 120000, None) == 32000
    assert (await api.get(f"/viewing/bookmarks/{mark}/restore")).json()["position_ms"] == 61000
    async with api.c.db.session() as s:
        original = await s.scalar(select(Title))
        original.duration_ms = 42000
        await s.commit()
    assert (await api.get(f"/viewing/bookmarks/{mark}/restore")).status_code == 409


def test_upgrade_merges_direct_histories_and_keeps_distinct_cuts(tmp_path):
    from datetime import UTC, datetime, timedelta

    import sqlalchemy as sa
    from alembic import command
    from sqlalchemy.orm import Session

    from app.infra.models import EditionProgress, Profile, Progress
    from tests.test_users_migration import config

    path = tmp_path / "legacy.db"
    cfg = config(path)
    command.upgrade(cfg, "0019")
    engine = sa.create_engine(f"sqlite:///{path}")
    with Session(engine) as s:
        s.add(Profile(id="p", name="Viewer"))
        s.add(Source(id="lib", name="Library", type="plex", config_enc="placeholder"))
        s.flush()
        for item, cut, ms in [("a", "Theatrical cut", 42000), ("b", "Extended cut", 120000)]:
            s.add(
                Title(
                    source_id="lib",
                    ext_id=item,
                    kind="movie",
                    title="Film",
                    tmdb_id=28,
                    extra={"edition_title": cut},
                    duration_ms=ms,
                )
            )
        now = datetime.now(UTC)
        s.add_all(
            [
                Progress(profile_id="p", ref_id="lib:lib:a", position_ms=18000, duration_ms=42000, updated_at=now),
                Progress(
                    profile_id="p",
                    ref_id="lib:lib:b",
                    position_ms=72000,
                    duration_ms=120000,
                    updated_at=now + timedelta(seconds=1),
                ),
                Progress(
                    profile_id="p",
                    ref_id="tmdb:movie:28",
                    position_ms=1000,
                    duration_ms=120000,
                    updated_at=now - timedelta(seconds=1),
                ),
            ]
        )
        s.commit()
    command.upgrade(cfg, "0020")
    with Session(engine) as s:
        assert [(r.ref_id, r.position_ms) for r in s.scalars(select(Progress))] == [("tmdb:movie:28", 72000)]
        assert sorted((r.variant_id, r.position_ms) for r in s.scalars(select(EditionProgress))) == [
            ("lib:lib:a", 18000),
            ("lib:lib:b", 72000),
        ]
