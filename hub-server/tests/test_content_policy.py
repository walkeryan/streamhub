from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.infra.models import AgeClassification, Channel, Profile, Programme, Source, Title
from app.services.classification import classification_record, summarize
from app.services.content_policy import active_policy
from app.services.errors import Forbidden, ServiceError


async def activate(api):
    from tests.test_auth_policy_sessions import enroll

    version = await enroll(api)
    owner = await api.c.auth.get_profile(api.profile_id)
    api.headers = {"Authorization": "Bearer " + api.c.auth.issue(owner, owner_pin_version=version)["access_token"]}


def evidence(key, code="PG", scope="movie"):
    value = summarize("tmdb", scope, [classification_record("US", "US-MPA", code)])
    return {**value, "subject_key": key}


async def seed(api):
    c = api.c
    async with c.db.session() as s:
        s.add(
            Profile(
                id="child",
                name="Child",
                kids=True,
                content_policy={"restricted": True, "allowed_movie_codes": ["G", "PG"], "allowed_tv_codes": ["TV-G"]},
            )
        )
        s.add(Source(id="fixture", type="xtream", name="Fixture", config_enc="fixture"))
        await s.commit()
        for n in range(1, 5):
            s.add(Title(source_id="fixture", ext_id=str(n), kind="movie", title=f"Title {n}", tmdb_id=n))
        s.add(Title(source_id="fixture", ext_id="episode", kind="episode", title="Episode", tmdb_id=1))
        s.add(AgeClassification(subject_key="tmdb:movie:1", provider="tmdb", evidence=evidence("tmdb:movie:1")))
        s.add(AgeClassification(subject_key="tmdb:movie:2", provider="tmdb", evidence=evidence("tmdb:movie:2", "R")))
        s.add(
            AgeClassification(
                subject_key="tmdb:movie:3",
                provider="tmdb",
                evidence=evidence("tmdb:movie:3"),
                observed_at=datetime.now(UTC) - timedelta(days=8),
            )
        )
        s.add(
            AgeClassification(
                subject_key="tmdb:movie:4", provider="tmdb", evidence=evidence("tmdb:movie:4", scope="series")
            )
        )
        s.add(
            Channel(source_id="fixture", ext_id="live", name="Live", epg_id="epg", group_id="group", group_name="Group")
        )
        await s.commit()
    return c.content_policy


@pytest.mark.asyncio
async def test_disabled_preserves_content_and_admin_restriction_rejected(api):
    p = await seed(api)
    assert await p.allowed_refs("child", ["tmdb:movie:2", "lib:fixture:episode"]) == {
        "tmdb:movie:2",
        "lib:fixture:episode",
    }
    await activate(api)
    with pytest.raises(ServiceError):
        await p.update(api.profile_id, {"restricted": True})
    with pytest.raises(ServiceError):
        await p.update("child", {"restricted": True, "allow_unknown": True})


@pytest.mark.asyncio
async def test_typed_batched_evidence_unknown_stale_and_episode_denied(api):
    p = await seed(api)
    await activate(api)
    refs = (
        [f"tmdb:movie:{n}" for n in range(1, 6)]
        + [f"lib:fixture:{n}" for n in range(1, 5)]
        + ["lib:fixture:episode", "tmdb:tv:1"]
    )
    assert await p.allowed_refs("child", refs) == {"tmdb:movie:1", "lib:fixture:1"}
    with pytest.raises(Forbidden):
        await p.require("child", "lib:fixture:episode")


@pytest.mark.asyncio
async def test_sql_scope_counts_pagination_and_context_reset(api):
    p = await seed(api)
    await activate(api)
    reset = active_policy.set((p, await p.profile("child")))
    try:
        async with api.c.db.session() as s:
            rows = (await s.execute(select(Title).order_by(Title.id).limit(10))).scalars().all()
            count = await s.scalar(select(func.count()).select_from(Title))
        assert [r.ext_id for r in rows] == ["1"]
        assert count == 1
    finally:
        active_policy.reset(reset)
    async with api.c.db.session() as s:
        assert await s.scalar(select(func.count()).select_from(Title)) == 5


@pytest.mark.asyncio
async def test_channel_interval_overlap_unknown_gap_and_wrong_feed(api):
    p = await seed(api)
    await activate(api)
    now = datetime.now(UTC)
    ev = summarize("xmltv", "programme", [classification_record("US", "US-TVPG", "TV-G")])
    async with api.c.db.session() as s:
        s.add(
            Programme(
                feed="fixture",
                epg_channel="epg",
                start=now - timedelta(minutes=10),
                stop=now + timedelta(minutes=10),
                title="Allowed",
                age_evidence=ev,
            )
        )
        await s.commit()
    await p.require_channel_interval("child", "ch:fixture:live", now - timedelta(minutes=1), now)
    assert "ch:fixture:live" in await p.allowed_refs("child", ["ch:fixture:live"])
    with pytest.raises(Forbidden):
        await p.require_channel_interval("child", "ch:fixture:live", now, now + timedelta(hours=1))
    async with api.c.db.session() as s:
        s.add(
            Programme(
                feed="fixture",
                epg_channel="epg",
                start=now - timedelta(minutes=1),
                stop=now + timedelta(minutes=1),
                title="Unknown",
                age_evidence={},
            )
        )
        await s.commit()
    assert not await p.allowed_refs("child", ["ch:fixture:live"])
    with pytest.raises(Forbidden):
        await p.require_channel_interval("child", "ch:fixture:live", now - timedelta(seconds=1), now)


@pytest.mark.asyncio
async def test_final_external_metadata_and_nested_feeds_filtered(api):
    p = await seed(api)
    await activate(api)
    payload = {
        "results": [{"id": "tmdb:movie:1", "sources": []}, {"id": "tmdb:movie:2", "sources": []}],
        "events": [{"id": "event", "feeds": [{"channel_id": "ch:fixture:live"}]}],
    }
    result = await p.filter_payload("child", payload)
    assert [x["id"] for x in result["results"]] == ["tmdb:movie:1"]
    assert result["events"] == []


@pytest.mark.asyncio
async def test_grant_checks_session_source_and_current_policy(api):
    p = await seed(api)
    await activate(api)
    profile = await p.profile("child")
    token = api.c.auth.issue(profile)
    principal = await api.c.auth.resolve_principal(token["access_token"])
    grant = SimpleNamespace(
        content_ref="lib:fixture:1",
        profile_id="child",
        profile_version=principal.token_version,
        session_id=principal.session_id,
        source_id="fixture",
        policy_at=None,
        policy_until=None,
    )
    await p.require_grant(grant, api.c.auth)
    await api.c.auth.revoke_session(principal.session_id)
    with pytest.raises(ServiceError):
        await p.require_grant(grant, api.c.auth)


@pytest.mark.asyncio
async def test_http_listing_search_direct_ids_and_policy_admin_guard(api):
    p = await seed(api)
    await activate(api)
    tokens = api.c.auth.issue(await p.profile("child"))
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    response = await api.client.get("/api/v1/browse/movies?all_languages=true", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1, body
    response = await api.client.get("/api/v1/search?q=Title&all_languages=true", headers=headers)
    assert response.status_code == 200, response.text
    assert all(x["id"] in {"tmdb:movie:1", "lib:fixture:1"} for x in response.json()["results"])
    for url in ["/api/v1/titles/lib:fixture:2", "/api/v1/playback/options?id=lib:fixture:2"]:
        response = await api.client.get(url, headers=headers)
        assert response.status_code in {403, 404}, response.text
    response = await api.client.put(
        "/api/v1/profiles/child/content-policy", headers=headers, json={"restricted": False}
    )
    assert response.status_code == 403
    response = await api.client.post(
        "/api/v1/auth/profile-switch", headers=headers, json={"profile_id": api.profile_id, "password": "test-admin-pw"}
    )
    assert response.status_code == 403 and response.json()["code"] == "owner_pin_required"
    response = await api.client.get("/api/v1/browse/movies?all_languages=true", headers=api.headers)
    assert response.status_code == 200 and response.json()["total"] == 4


@pytest.mark.asyncio
async def test_segment_timestamp_gate_and_unknown_expiry(api, tmp_path):
    p = await seed(api)
    await activate(api)
    now = datetime.now(UTC)
    ev = summarize("xmltv", "programme", [classification_record("US", "US-TVPG", "TV-G")])
    async with api.c.db.session() as s:
        s.add(
            Programme(
                feed="fixture",
                epg_channel="epg",
                start=now - timedelta(minutes=10),
                stop=now + timedelta(minutes=10),
                title="Allowed",
                age_evidence=ev,
            )
        )
        await s.commit()
    tokens = api.c.auth.issue(await p.profile("child"))
    principal = await api.c.auth.resolve_principal(tokens["access_token"])
    grant = SimpleNamespace(
        content_ref="ch:fixture:live",
        profile_id="child",
        profile_version=principal.token_version,
        session_id=principal.session_id,
        source_id="fixture",
        policy_at=None,
        policy_until=None,
        live=True,
    )
    path = tmp_path / "seg.ts"
    manifest = tmp_path / "index.m3u8"
    manifest.write_text(
        f"#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:{(now - timedelta(seconds=10)).isoformat()}\n#EXTINF:5,\nseg.ts\n"
    )
    await p.require_segment(grant, api.c.auth, path, manifest)
    with pytest.raises(Forbidden):
        await p.require_segment(grant, api.c.auth, tmp_path / "other.ts", manifest)
    manifest.write_text("#EXTM3U\n#EXTINF:5,\nseg.ts\n")
    with pytest.raises(Forbidden):
        await p.require_segment(grant, api.c.auth, path, manifest)


@pytest.mark.asyncio
async def test_owner_legacy_kids_label_does_not_lock_out_owner(api):
    await activate(api)
    async with api.c.db.session() as session:
        owner = await session.get(Profile, api.profile_id)
        owner.kids = True
        await session.commit()
    assert not api.c.content_policy.is_restricted(await api.c.auth.get_profile(api.profile_id))
