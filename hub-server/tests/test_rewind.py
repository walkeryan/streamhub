from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.infra.models import Channel, Programme, Source
from app.services.errors import ServiceError
from app.services.rewind import archive_request, archive_window
from tests.conftest import FIXTURES
from tests.test_api_playback import add_m3u, channels


def channel(**kw):
    return Channel(
        source_id="fixture",
        ext_id="1",
        name="Synthetic",
        stream_url="http://fixture.invalid/live.ts",
        catchup_days=3,
        data={"catchup_type": "xtream"},
        **kw,
    )


def test_archive_window_is_advertised_bounded_and_not_recorded():
    now = datetime(2026, 11, 1, 6, 30, 45, tzinfo=UTC)
    cap = archive_window(channel(), now=now)
    assert cap["advertised_end"] - cap["advertised_start"] == timedelta(hours=1)
    assert not cap["verified"] and not cap["recording_enabled"]
    # Same instant across a DST offset is normalized, not treated as local wall time.
    requested = datetime(2026, 11, 1, 1, 0, tzinfo=timezone(timedelta(hours=-5)))
    start, stop = archive_request(channel(), requested, now=now)
    assert start == datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    assert stop == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    for invalid in [now + timedelta(minutes=1), now - timedelta(hours=2), now.replace(tzinfo=None)]:
        with pytest.raises(ServiceError):
            archive_request(channel(), invalid, now=now)
    no_archive = channel()
    no_archive.catchup_days = 0
    assert archive_window(no_archive, now=now)["mode"] == "unavailable"
    with pytest.raises(ServiceError):
        archive_request(no_archive, requested, now=now)


async def test_rewind_resolve_is_finite_source_scoped_and_authenticated(api):
    source = await add_m3u(api, FIXTURES / "sample.m3u")
    tf1 = next(c for c in await channels(api) if c["name"] == "TF1")
    cap = await api.get("/playback/rewind", params={"id": tf1["id"]})
    assert cap.status_code == 200
    data = cap.json()
    assert data["mode"] == "provider_archive" and data["verified"] is False
    edge = datetime.fromisoformat(data["advertised_end"].replace("Z", "+00:00"))
    req = {"id": tf1["id"], "client": "android_tv", "rewind_at": (edge - timedelta(minutes=30)).isoformat()}
    response = await api.post("/playback/resolve", json=req)
    assert response.status_code == 200
    descriptor = response.json()
    assert descriptor["live"] is False and descriptor["duration_ms"] == 1800000
    assert descriptor["archive_start"] and descriptor["archive_end"]
    assert api.c.proxy.recovery_status()["active_sessions"] == 0
    assert not list(api.c.settings.transcode_path.iterdir()) if api.c.settings.transcode_path.exists() else True
    assert (await api.post("/playback/resolve", json={**req, "source_id": "other"})).status_code == 404
    assert (await api.post("/playback/resolve", json={**req, "rewind_at": "2026-11-01T06:00:00"})).status_code == 422
    assert (await api.post("/playback/resolve", json={**req, "catchup_programme_id": "prog:other"})).status_code == 400
    assert (await api.client.get("/api/v1/playback/rewind", params={"id": tf1["id"]})).status_code == 401
    async with api.c.db.session() as session:
        src = await session.get(Source, source["id"])
        src.enabled = False
        await session.commit()
    assert (await api.get("/playback/rewind", params={"id": tf1["id"]})).status_code == 404
    assert (await api.post("/playback/resolve", json=req)).status_code == 404


async def test_programme_cannot_cross_channel_or_source(api):
    source = await add_m3u(api, FIXTURES / "sample.m3u")
    tf1 = next(c for c in await channels(api) if c["name"] == "TF1")
    now = datetime.now(UTC)
    async with api.c.db.session() as session:
        programme = Programme(
            epg_channel="different.channel",
            feed=source["id"],
            start=now - timedelta(hours=1),
            stop=now - timedelta(minutes=10),
            title="Wrong channel",
        )
        session.add(programme)
        await session.commit()
        from app.services.ids import prog_id

        ref = prog_id(programme.id)
    result = await api.post(
        "/playback/resolve", json={"id": tf1["id"], "client": "android_tv", "catchup_programme_id": ref}
    )
    assert result.status_code == 404
