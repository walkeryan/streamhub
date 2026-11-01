from unittest.mock import AsyncMock

from sqlalchemy import insert

from app.adapters.plex.provider import PlexSource
from app.infra.models import Profile, Source, Title
from app.services.audio_selection import provider_audio_extra
from app.services.sports_recordings import sports_recordings


async def seed(api):
    async with api.c.db.session() as s:
        p = await s.get(Profile, api.profile_id)
        p.languages = ["eng"]
        s.add_all(
            [
                Source(id="plex", name="Plex library", type="plex", config_enc="fixture", enabled=True),
                Source(id="off", name="Disabled Plex", type="plex", config_enc="fixture", enabled=False),
                Source(id="iptv", name="IPTV", type="xtream", config_enc="fixture", enabled=True),
            ]
        )
        await s.flush()

        def title(ext, **kw):
            values = dict(
                source_id="plex",
                ext_id=ext,
                kind="movie",
                title="NBA: A vs B",
                genres=["Basketball"],
                duration_ms=600_000,
                extra={"part_key": "/library/parts/1/file.mp4", "originally_available_at": "2026-10-31"},
            )
            values.update(kw)
            return Title(**values)

        s.add_all(
            [
                title("unknown"),
                title(
                    "english",
                    extra={"part_key": "/library/parts/2/file.mp4", **provider_audio_extra({"language": "eng"})},
                ),
                title(
                    "french",
                    extra={"part_key": "/library/parts/3/file.mp4", **provider_audio_extra({"language": "fra"})},
                ),
                title("generic", genres=["Drama"]),
                title("missing", extra={}),
                title("gone", extra={"part_key": "/library/parts/4/file.mp4", "part_available": False}),
                title("series", kind="show"),
                title("off", source_id="off"),
                title("iptv", source_id="iptv"),
            ]
        )
        await s.commit()


async def permit(_profile, refs):
    return set(refs)


async def test_recordings_explicit_metadata_identity_audio_and_no_live_equivalence(api):
    await seed(api)
    result = await sports_recordings(api.c.catalog, api.profile_id, allowed_refs=permit)
    assert {x["title_id"] for x in result["items"]} == {"lib:plex:unknown", "lib:plex:english"}
    assert result["total"] == 2 and not result["truncated"]
    unknown = next(x for x in result["items"] if x["title_id"].endswith(":unknown"))
    assert unknown["kind"] == "library_video" and unknown["live"] is False
    assert not unknown["audio_verified"] and unknown["audio_languages"] == []
    assert unknown["originally_available_at"] == "2026-10-31"
    assert "event_id" not in unknown and "channel_id" not in unknown and "part_key" not in unknown
    all_languages = await sports_recordings(api.c.catalog, api.profile_id, allowed_refs=permit, language="all")
    assert all_languages["total"] == 3
    only_unknown = await sports_recordings(api.c.catalog, api.profile_id, allowed_refs=permit, language="und")
    assert [x["title_id"] for x in only_unknown["items"]] == ["lib:plex:unknown"]


async def test_policy_batch_gate_precedes_counts_and_pagination(api):
    await seed(api)
    allowed = AsyncMock(return_value={"lib:plex:unknown"})
    result = await sports_recordings(api.c.catalog, api.profile_id, allowed_refs=allowed, limit=1)
    assert result["total"] == 1 and result["items"][0]["title_id"] == "lib:plex:unknown"
    assert allowed.call_args.args[0] == api.profile_id
    assert all(ref.startswith("lib:plex:") for ref in allowed.call_args.args[1])
    assert (await sports_recordings(api.c.catalog, api.profile_id, allowed_refs=allowed, offset=1))["items"] == []
    denied = AsyncMock(return_value=set())
    assert (await sports_recordings(api.c.catalog, api.profile_id, allowed_refs=denied))["total"] == 0


async def test_endpoint_fail_closed_without_policy_and_authenticated(api, monkeypatch):
    await seed(api)
    policy = api.c.content_policy
    monkeypatch.setattr(api.c, "content_policy", None, raising=False)
    assert (await api.get("/sports/recordings")).status_code == 403
    monkeypatch.setattr(api.c, "content_policy", policy)
    monkeypatch.setattr(policy, "allowed_refs", AsyncMock(side_effect=permit))
    response = await api.get("/sports/recordings")
    assert response.status_code == 200 and response.json()["total"] == 2
    assert response.headers["cache-control"] == "private, no-store"
    assert (await api.client.get("/api/v1/sports/recordings")).status_code == 401
    assert (await api.get("/sports/recordings", params={"limit": 101})).status_code == 422


def test_plex_preserves_release_date_and_explicit_missing_part():
    src = object.__new__(PlexSource)
    src.server = "http://fixture.invalid"
    item = src.map_meta(
        {
            "ratingKey": "123",
            "type": "episode",
            "title": "NBA: A vs B",
            "Genre": [{"tag": "Basketball"}],
            "originallyAvailableAt": "2026-10-31",
            "Media": [{"Part": [{"key": "/library/parts/1", "exists": 0}]}],
        }
    )
    assert item.extra["originally_available_at"] == "2026-10-31"
    assert item.extra["part_available"] is False
    assert item.ext_id == "123" and item.kind.value == "episode"
    invalid = src.map_meta({"ratingKey": "124", "type": "movie", "originallyAvailableAt": "not-a-date"})
    assert invalid.extra["originally_available_at"] is None


async def test_large_catalog_cap_is_reported_and_generic_titles_do_not_match(api):
    await seed(api)
    async with api.c.db.session() as s:
        await s.execute(
            insert(Title),
            [
                dict(
                    source_id="plex",
                    ext_id=f"bulk{i}",
                    kind="movie",
                    title="NBA: A vs B",
                    genres=["Basketball"],
                    extra={"part_key": "/library/parts/1"},
                )
                for i in range(2100)
            ],
        )
        await s.commit()
    result = await sports_recordings(api.c.catalog, api.profile_id, allowed_refs=permit, limit=10)
    assert result["truncated"] and len(result["items"]) == 10 and result["total"] == 2000
    assert (await sports_recordings(api.c.catalog, api.profile_id, allowed_refs=permit, q="not present"))["items"] == []
