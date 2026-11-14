from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.infra.models import Channel, Profile, Programme, Source
from tests.conftest import FIXTURES, add_m3u
from tests.test_audio_selection import tracks


async def live_fixture(api):
    src = await add_m3u(api, FIXTURES / "sample.m3u")
    async with api.c.db.session() as session:
        profile = await session.get(Profile, api.profile_id)
        profile.languages = ["eng"]
        ch = (
            await session.execute(select(Channel).where(Channel.source_id == src["id"], Channel.name == "TF1"))
        ).scalar_one()
        ch.language, ch.language_source = "eng", "tag"
        ch.data = {**(ch.data or {}), "tvg_language": "English"}
        await session.commit()
        return src["id"], f"ch:{ch.source_id}:{ch.ext_id}", ch.id


async def test_a_channel_plays_when_opened_without_proof_of_its_audio(api):
    # The household asked never to be stopped to confirm a channel's audio: a provider's language
    # hint is all almost every channel has, and the confirmation stood in front of every zap.
    _, hub, _ = await live_fixture(api)
    response = await api.get("/playback/options", params={"id": hub})
    assert response.status_code == 200
    option = response.json()["options"][0]
    assert option["variant_id"] == hub and option["kind"] == "channel"
    # The hint is still only a hint: nothing claims the audio was verified.
    assert option["language_hint"] == "eng" and not option["audio_verified"]
    assert not response.json()["selection_required"] and response.json()["automatic_variant_id"] == hub
    for client in ["web", "android_tv"]:
        for fields in [{}, {"allow_unverified_audio": True}, {"variant_id": hub}]:
            played = await api.post("/playback/resolve", json={"id": hub, "client": client, **fields})
            assert played.status_code == 200, played.text
            # The profile's languages still steer the player's track choice; nothing is held for them.
            assert played.json()["preferred_audio_languages"] == ["eng"]
            assert played.json()["audio_language_required"] is False


async def test_channel_exact_consent_unknown_live_and_rewind(api):
    _, hub, _ = await live_fixture(api)
    req = {"id": hub, "variant_id": hub, "allow_unverified_audio": True, "client": "android_tv"}
    live = await api.post("/playback/resolve", json=req)
    assert live.status_code == 200 and live.json()["live"]
    rewind_at = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    rewind = await api.post("/playback/resolve", json={**req, "rewind_at": rewind_at})
    assert rewind.status_code == 200 and not rewind.json()["live"]
    assert rewind.json()["archive_start"] and rewind.json()["archive_end"]
    plain = await api.post("/playback/resolve", json={"id": hub, "rewind_at": rewind_at})
    assert plain.status_code == 200 and not plain.json()["live"]
    assert (await api.post("/playback/resolve", json={**req, "variant_id": "ch:other:1"})).status_code == 404
    assert (await api.post("/playback/resolve", json={**req, "source_id": "other"})).status_code == 404
    assert (await api.get("/playback/options", params={"id": hub, "source_id": "other"})).status_code == 404


async def test_track_evidence_never_holds_a_channel_and_sources_still_gate_it(api):
    sid, hub, pk = await live_fixture(api)
    async with api.c.db.session() as session:
        ch = await session.get(Channel, pk)
        ch.data = {**ch.data, **tracks("eng")}
        await session.commit()
    assert (await api.get("/playback/options", params={"id": hub})).json()["automatic_variant_id"] == hub
    assert (await api.post("/playback/resolve", json={"id": hub})).status_code == 200
    async with api.c.db.session() as session:
        ch = await session.get(Channel, pk)
        ch.data = {**ch.data, **tracks("eng", "fra", default=1)}
        await session.commit()
    # Tagged tracks in another language as the default: still played, the player picks English.
    mixed = await api.post("/playback/resolve", json={"id": hub})
    assert mixed.status_code == 200 and mixed.json()["audio_languages"] == ["eng", "fra"]
    async with api.c.db.session() as session:
        p = await session.get(Profile, api.profile_id)
        p.languages = ["all"]
        await session.commit()
    assert (await api.get("/playback/options", params={"id": hub})).json()["automatic_variant_id"] == hub
    assert (await api.post("/playback/resolve", json={"id": hub})).status_code == 200
    async with api.c.db.session() as session:
        source = await session.get(Source, sid)
        source.enabled = False
        await session.commit()
    assert (await api.get("/playback/options", params={"id": hub})).status_code == 404
    assert (await api.post("/playback/resolve", json={"id": hub})).status_code == 404


async def test_programme_options_preserve_feed_ownership_and_catchup_consent(api):
    sid, hub, pk = await live_fixture(api)
    async with api.c.db.session() as session:
        ch = await session.get(Channel, pk)
        now = datetime.now(UTC)
        own = Programme(
            feed=sid,
            epg_channel=ch.epg_id,
            start=now - timedelta(hours=1),
            stop=now - timedelta(minutes=10),
            title="Archive",
        )
        foreign = Programme(
            feed="other-feed",
            epg_channel=ch.epg_id,
            start=now - timedelta(hours=1),
            stop=now - timedelta(minutes=10),
            title="Foreign",
        )
        session.add_all([own, foreign])
        await session.commit()
        own_id, foreign_id = f"prog:{own.id}", f"prog:{foreign.id}"
    options = (await api.get("/playback/options", params={"id": own_id})).json()
    assert options["id"] == own_id and options["options"][0]["variant_id"] == hub
    assert not options["selection_required"]
    assert (await api.post("/playback/resolve", json={"id": own_id})).status_code == 200
    response = await api.post(
        "/playback/resolve", json={"id": own_id, "variant_id": hub, "allow_unverified_audio": True}
    )
    assert response.status_code == 200 and response.json()["live"] is False
    assert (await api.get("/playback/options", params={"id": foreign_id})).status_code == 404
    assert (
        await api.post("/playback/resolve", json={"id": foreign_id, "variant_id": hub, "allow_unverified_audio": True})
    ).status_code == 404
    assert (await api.client.get("/api/v1/playback/options", params={"id": hub})).status_code == 401
