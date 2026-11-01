from unittest.mock import AsyncMock

from sqlalchemy import select

from app.infra.models import Channel, Profile, Source
from app.services.errors import Forbidden
from tests.conftest import FIXTURES, add_m3u
from tests.test_audio_selection import tracks


async def fixture_pair(api, maximum=2):
    source = await add_m3u(api, FIXTURES / "sample.m3u")
    async with api.c.db.session() as session:
        src = await session.get(Source, source["id"])
        src.hints = {**src.hints, "max_connections": maximum}
        profile = await session.get(Profile, api.profile_id)
        profile.languages = ["eng"]
        rows = list((await session.execute(select(Channel).where(Channel.source_id == src.id))).scalars())[:2]
        for row in rows:
            row.data = {**row.data, **tracks("eng")}
        await session.commit()
        panes = [{"id": f"ch:{row.source_id}:{row.ext_id}"} for row in rows]
    return source["id"], {"panes": panes, "recovery_generation": api.c.proxy.recovery_epoch}


async def test_pair_http_capacity_one_and_source_audio_auth_gates(api, monkeypatch):
    source, request = await fixture_pair(api, 1)
    start = AsyncMock(side_effect=AssertionError("resolution must not open streams"))
    monkeypatch.setattr(api.c.proxy, "hls_session", start)
    unauth = await api.client.post("/api/v1/playback/multiview", json=request)
    assert unauth.status_code == 401
    blocked = await api.post("/playback/multiview", json=request)
    assert blocked.status_code == 409 and blocked.json()["code"] == "multiview_capacity"
    assert not api.c.proxy._pairs
    wrong_source = {**request, "panes": [{**request["panes"][0], "source_id": "other"}, request["panes"][1]]}
    assert (await api.post("/playback/multiview", json=wrong_source)).status_code == 404
    legacy = await api.post("/playback/resolve", json={**request["panes"][0], "multiview": True})
    assert legacy.status_code == 409 and legacy.json()["code"] == "multiview_atomic_required"
    async with api.c.db.session() as session:
        row = (await session.execute(select(Channel).where(Channel.source_id == source))).scalars().first()
        row.data = {**row.data, **tracks("fra")}
        await session.commit()
    denied = await api.post("/playback/multiview", json=request)
    assert denied.status_code == 409 and denied.json()["code"] == "audio_selection_required"
    start.assert_not_awaited()


async def test_pair_http_tokens_bound_release_and_preferences_invalidate(api, monkeypatch):
    source, request = await fixture_pair(api)
    response = await api.post("/playback/multiview", json=request)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["panes"]) == 2 and payload["expires_in_seconds"] == 60
    assert api.c.proxy._source_use(source) == 2
    tokens = [pane["url"].split("/stream/")[1].split("/")[0] for pane in payload["panes"]]
    grants = [api.c.tokens.verify(token) for token in tokens]
    assert all(g.pair_lease_id == payload["lease_id"] and g.profile_id == api.profile_id for g in grants)
    assert all(g.audio_preferred == ["eng"] and not g.audio_consent for g in grants)
    assert grants[0].session_id and grants[0].session_id == grants[1].session_id
    # Preferences change after admission: old URLs never start either stream.
    async with api.c.db.session() as session:
        profile = await session.get(Profile, api.profile_id)
        profile.languages = ["fra"]
        await session.commit()
    start = AsyncMock(side_effect=AssertionError("stale grant must not start"))
    monkeypatch.setattr(api.c.proxy, "hls_session", start)
    denied = await api.client.get(f"/api/v1/stream/{tokens[0]}/index.m3u8")
    assert denied.status_code == 403 and denied.json()["code"] == "multiview_expired"
    assert not api.c.proxy._pairs and api.c.proxy._source_use(source) == 0
    start.assert_not_awaited()
    assert (await api.post("/playback/multiview/release", json={"lease_id": payload["lease_id"]})).status_code == 204


async def test_pair_http_partial_authorization_failure_rolls_back(api, monkeypatch):
    source, request = await fixture_pair(api)
    monkeypatch.setattr(
        api.c.content_policy, "require_grant", AsyncMock(side_effect=[None, Forbidden("fixture policy revoked")])
    )
    response = await api.post("/playback/multiview", json=request)
    assert response.status_code == 403
    assert not api.c.proxy._pairs and api.c.proxy._source_use(source) == 0


async def test_pair_http_release_stop_and_stale_urls(api, monkeypatch):
    source, request = await fixture_pair(api)
    payload = (await api.post("/playback/multiview", json=request)).json()
    token = payload["panes"][0]["url"].split("/stream/")[1].split("/")[0]
    assert (await api.post("/playback/multiview/release", json={"lease_id": payload["lease_id"]})).status_code == 204
    start = AsyncMock(side_effect=AssertionError("released grant must not start"))
    monkeypatch.setattr(api.c.proxy, "hls_session", start)
    assert (await api.client.get(f"/api/v1/stream/{token}/index.m3u8")).status_code == 403
    api.c.proxy.pause()
    await api.c.proxy.stop_owned()
    api.c.proxy.paused = False
    stale = await api.post("/playback/multiview", json=request)
    assert stale.status_code == 403 and stale.json()["code"] == "playback_stopped"
    assert api.c.proxy._source_use(source) == 0
    start.assert_not_awaited()


async def test_pair_http_revoked_session_stops_reserved_pair(api, monkeypatch):
    source, request = await fixture_pair(api)
    payload = (await api.post("/playback/multiview", json=request)).json()
    token = payload["panes"][0]["url"].split("/stream/")[1].split("/")[0]
    monkeypatch.setattr(api.c.auth, "session_valid", AsyncMock(side_effect=Forbidden("session revoked")))
    start = AsyncMock(side_effect=AssertionError("revoked session must not start"))
    monkeypatch.setattr(api.c.proxy, "hls_session", start)
    response = await api.client.get(f"/api/v1/stream/{token}/index.m3u8")
    assert response.status_code == 403
    assert not api.c.proxy._pairs and api.c.proxy._source_use(source) == 0
    start.assert_not_awaited()
