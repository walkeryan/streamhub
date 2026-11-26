"""Per-profile viewing choices survive independent requests and changing source evidence."""

from sqlalchemy import select

from app.infra.models import Profile, Source, Title
from tests.conftest import Api
from tests.test_audio_selection import row, tracks

REF = "tmdb:movie:140607"


async def seed(api: Api, *, enabled: bool = True) -> None:
    async with api.c.db.session() as session:
        session.add(Profile(id="another-viewer", name="Another viewer", languages=["eng"]))
        session.add(
            Source(
                id="iptv", type="xtream", name="Fixture IPTV", enabled=enabled, config_enc=api.c.crypto.encrypt_json({})
            )
        )
        await session.flush()
        session.add_all([row("en", tracks("eng")), row("fr", tracks("fra")), row("unknown")])
        await session.commit()


async def test_runtime_free_watch_resume_and_shelf_visibility(api: Api) -> None:
    await seed(api)

    async def report(**change):
        response = await api.post(
            "/playback/progress", json={"id": REF, "position_ms": 0, "state": "stopped", **change}
        )
        assert response.status_code == 204, response.text

    await report(watched=True)
    assert (await api.get("/history")).json()[0]["progress_pct"] == 100
    state = await api.c.progress.get(api.profile_id, REF)
    assert state.completed and state.duration_ms is None
    await report(watched=False)
    await report(position_ms=68_000)
    for hidden in (True, False):
        assert (await api.put("/history/continue-watching", params={"id": REF, "hidden": hidden})).status_code == 204
        assert bool(await api.c.progress.in_progress(api.profile_id)) is not hidden
        assert (await api.c.progress.get(api.profile_id, REF)).position_ms == 68_000
    assert await api.c.progress.get("another-viewer", REF) is None
    assert (await api.get(f"/titles/{REF}")).json()["resume_position_ms"] == 68_000
    await report(watched=False)
    assert (await api.c.progress.get(api.profile_id, REF)).position_ms == 0


async def test_remember_verified_edition_and_revalidate_audio_and_source(api: Api) -> None:
    await seed(api)

    async def choose(variant):
        return await api.put("/playback/preference", params={"id": REF, "variant_id": variant})

    async def choices():
        return (await api.get("/playback/options", params={"id": REF})).json()

    assert (await choose("lib:iptv:unknown")).status_code == 409
    async with api.c.db.session() as s:
        variant = await s.scalar(select(Title).where(Title.ext_id == "fr"))
        variant.extra = {"probe": {"audio": [{"language": "eng", "default": True}, {"language": "fra"}]}}
        await s.commit()
    response = await api.put("/playback/preference", params={"id": REF, "variant_id": "lib:iptv:fr", "audio_track": 1})
    assert response.status_code == 204
    saved = await choices()
    assert (
        saved["remembered"] and saved["automatic_variant_id"] == "lib:iptv:fr" and saved["remembered_audio_track"] == 1
    )
    assert not (await api.c.playback.options("another-viewer", REF, probe=False))["remembered"]
    async with api.c.db.session() as s:
        variant = await s.scalar(select(Title).where(Title.ext_id == "fr"))
        variant.extra = tracks("eng")
        await s.commit()
    assert (await choices())["selection_required"]
    declined = await api.post("/playback/resolve", json={"id": REF, "client": "web"})
    assert declined.status_code == 409 and declined.json()["code"] == "audio_selection_required"
    assert (await choose("lib:iptv:en")).status_code == 204
    async with api.c.db.session() as s:
        source = await s.get(Source, "iptv")
        source.enabled = False
        await s.commit()
    options = await choices()
    assert options["selection_required"] and not options["automatic_variant_id"] and options["options"] == []
    assert (await api.put("/playback/preference", params={"id": REF})).status_code == 204
    assert not (await choices())["remembered"]


async def test_watchlist_transitions_profile_scope_service_and_audio(api: Api) -> None:
    await seed(api, enabled=False)
    assert (await api.put("/watchlist", json={"id": REF})).status_code == 204

    async def shelf():
        result = await api.get("/home")
        assert result.status_code == 200, result.text
        return next((r["tiles"] for r in result.json()["rows"] if r["id"] == "ready_to_watch"), [])

    assert await shelf() == []
    async with api.c.db.session() as s:
        (await s.get(Source, "iptv")).enabled = True
        await s.commit()
    ready = await shelf()
    assert [(t["id"], t["subtitle"]) for t in ready] == [(REF, "Fixture IPTV · English")]
    assert await shelf() == ready
    # Another profile saving a title already available does not acquire this profile's transition.
    await api.c.watchlist.put("another-viewer", REF, playable=True)
    assert await api.c.watchlist.newly_available("another-viewer", {REF: True}) == []
    async with api.c.db.session() as s:
        (await s.get(Source, "iptv")).enabled = False
        await s.commit()
    assert await shelf() == []


async def test_hidden_ready_shelf_avoids_playback_checks_and_old_saved_entries_rotate(api: Api, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    await seed(api)
    ids = [f"tmdb:movie:{index}" for index in range(49)]
    service = api.c.watchlist
    for ref in ids:
        await service.put(api.profile_id, ref, playable=False)
    first = await service.availability_candidates(api.profile_id, ids, 20)
    second = await service.availability_candidates(api.profile_id, ids, 20)
    third = await service.availability_candidates(api.profile_id, ids, 20)
    assert len(first) == len(second) == len(third) == 20
    assert set(first + second + third) == set(ids)
    result = await api.c.layout.get(api.profile_id)
    for item in result:
        if item["id"] in {"ready_to_watch", "watchlist"}:
            item["hidden"] = True
    await api.c.layout.put(api.profile_id, result)
    options = AsyncMock(side_effect=AssertionError("An opted-out shelf requested playback options"))
    monkeypatch.setattr(api.c.playback, "options", options)
    assert (await api.get("/home")).status_code == 200
    options.assert_not_awaited()


async def test_home_survives_catalog_churn_without_changing_saved_availability(api: Api, monkeypatch) -> None:
    from app.infra.models import Watchlist

    await seed(api)
    await api.c.watchlist.put(api.profile_id, REF, playable=False)
    original = api.c.home.watchlist_audio
    attempts = 0

    async def catalog_changes_while_checking(profile_id: str, ref: str) -> str | None:
        nonlocal attempts
        result = await original(profile_id, ref)
        attempts += 1
        api.c.db.catalog_revisions.catalog += 1
        return result

    monkeypatch.setattr(api.c.home, "watchlist_audio", catalog_changes_while_checking)
    response = await api.req("GET", "/home")
    assert response.status_code == 200, response.text
    assert attempts == 2
    assert any(row["id"] == "watchlist" and row["tiles"] for row in response.json()["rows"])
    assert not any(row["id"] == "ready_to_watch" and row["tiles"] for row in response.json()["rows"])
    async with api.c.db.session() as session:
        saved = await session.scalar(
            select(Watchlist).where(Watchlist.profile_id == api.profile_id, Watchlist.ref_id == REF)
        )
        assert saved and saved.last_playable is False and saved.playable_since is None

    monkeypatch.setattr(api.c.home, "watchlist_audio", original)
    recovered = await api.req("GET", "/home")
    assert recovered.status_code == 200
    ready = next(row for row in recovered.json()["rows"] if row["id"] == "ready_to_watch")
    assert [tile["id"] for tile in ready["tiles"]] == [REF]
