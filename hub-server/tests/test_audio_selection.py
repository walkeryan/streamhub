from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.infra.models import Title
from app.services.audio_selection import audio_info, preferred_audio, provider_audio_extra
from app.services.errors import Conflict, NotFound
from app.services.playback import PlaybackResolver


def row(ext, extra=None):
    return Title(
        source_id="iptv", ext_id=ext, kind="movie", title="EN - The Force Awakens", tmdb_id=140607, extra=extra or {}
    )


def resolver():
    r = object.__new__(PlaybackResolver)
    r._sources = SimpleNamespace(
        rows_by_id=AsyncMock(
            return_value={
                "iptv": SimpleNamespace(enabled=True, name="Provider"),
            }
        )
    )
    r._catalog = SimpleNamespace(preferred_languages=AsyncMock(return_value=["eng"]))
    return r


def tracks(*langs, default=None):
    return {
        "audio_tracks": [{"language": lang, "default": i == default} for i, lang in enumerate(langs)],
        "audio_evidence": "provider_audio_tracks",
    }


def test_hints_never_verify_audio():
    info = audio_info({"original_language": "en", "language": "en"}, label="EN - Star Wars", group="English")
    assert info["language_hint"] == "eng"
    assert not info["audio_verified"]
    assert not preferred_audio(info, ["eng"])
    assert not audio_info({"audio_tracks": [{"language": "eng"}]})["audio_verified"]


def test_mixed_audio_requires_default():
    assert not preferred_audio(audio_info(tracks("eng", "fra")), ["eng"])
    assert not preferred_audio(audio_info(tracks("eng", "fra", default=1)), ["eng"])
    assert preferred_audio(audio_info(tracks("eng", "fra", default=0)), ["eng"])
    assert not preferred_audio(audio_info(tracks("eng", "und")), ["eng"])
    assert preferred_audio(audio_info(tracks("eng")), ["eng"])


def test_track_normalization():
    extra = provider_audio_extra([{"Language": "en", "IsDefault": True}, {"languageCode": "fr"}])
    assert audio_info(extra)["default_audio_language"] == "eng"
    assert audio_info(extra)["audio_languages"] == ["eng", "fra"]


async def test_same_source_variants_preserved():
    r = resolver()
    en, fr = row("en", tracks("eng")), row("fr", tracks("fra"))
    options = await r._audio_options("p", [en, fr])
    assert len(options) == 2
    assert [o["preferred_audio_available"] for o in options] == [True, False]
    assert await r._select_audio("p", [en, fr], None, False) is en


async def test_unknown_requires_exact_consent():
    r = resolver()
    en, fr = row("en"), row("fr", tracks("fra"))
    for variant, consent in [(None, False), (None, True), ("lib:iptv:en", False)]:
        with pytest.raises(Conflict) as error:
            await r._select_audio("p", [en, fr], variant, consent)
        assert error.value.code == "audio_selection_required"
    assert await r._select_audio("p", [en, fr], "lib:iptv:en", True) is en
    assert await r._select_audio("p", [en, fr], "lib:iptv:fr", True) is fr
    with pytest.raises(NotFound):
        await r._select_audio("p", [en, fr], "lib:iptv:other", True)
    with pytest.raises(NotFound):
        await r._select_audio("p", [en, fr], "lib:iptv:en", True, "other-source")


async def test_disabled_source_rejected():
    r = resolver()
    r._sources.rows_by_id.return_value["iptv"].enabled = False
    assert await r._audio_options("p", [row("en")]) == []
    with pytest.raises(NotFound):
        await r._select_audio("p", [row("en")], "lib:iptv:en", True)


async def test_series_checks_episode_audio():
    r = resolver()
    show = row("show", tracks("eng"))
    show.kind = "series"
    episode = row("ep", tracks("fra"))
    r._next_episode = AsyncMock(return_value=episode)
    with pytest.raises(Conflict):
        await r._select_audio("p", [show], None, False)
    assert await r._select_audio("p", [show], "lib:iptv:show", True) is episode


async def test_all_languages_keeps_unknown_option():
    r = resolver()
    r._catalog.preferred_languages.return_value = []
    unknown = row("en")
    assert await r._select_audio("p", [unknown], None, False) is unknown
    assert not (await r._audio_options("p", [unknown]))[0]["audio_verified"]


async def test_options_and_resolve_enforce_same_identity_and_preference(api):
    from app.infra.models import Profile, Source

    async with api.c.db.session() as session:
        profile = await session.get(Profile, api.profile_id)
        profile.languages = ["eng"]
        session.add(Source(id="iptv", type="xtream", name="Fixture IPTV", config_enc=api.c.crypto.encrypt_json({})))
        session.add_all([row("en"), row("fr", tracks("fra"))])
        await session.commit()
    response = await api.get("/playback/options", params={"id": "tmdb:movie:140607"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["options"]) == 2
    assert data["selection_required"] and data["automatic_variant_id"] is None
    assert not data["options"][0]["audio_verified"]
    denied = await api.post("/playback/resolve", json={"id": "tmdb:movie:140607"})
    assert denied.status_code == 409
    assert denied.json()["code"] == "audio_selection_required"
    with pytest.MonkeyPatch.context() as patch:
        resolve_title = AsyncMock(return_value={})
        patch.setattr(api.c.playback, "_resolve_title", resolve_title)
        await api.c.playback.resolve(
            api.profile_id, "tmdb:movie:140607", variant_id="lib:iptv:en", allow_unverified_audio=True
        )
        assert resolve_title.call_args.args[1].ext_id == "en"
        with pytest.raises(NotFound):
            await api.c.playback.resolve(
                api.profile_id, "tmdb:movie:999", variant_id="lib:iptv:en", allow_unverified_audio=True
            )
    unauthenticated = await api.client.get("/api/v1/playback/options", params={"id": "tmdb:movie:140607"})
    assert unauthenticated.status_code == 401


def test_plex_audio_evidence_belongs_to_selected_part_only():
    from app.adapters.plex.provider import PlexSource

    source = object.__new__(PlexSource)
    source.server = "https://fixture.invalid"
    item = source.map_meta(
        {
            "type": "movie",
            "ratingKey": "1",
            "title": "Film",
            "Media": [
                {
                    "Part": [
                        {
                            "key": "/part/first",
                            "Stream": [
                                {"streamType": 1, "languageCode": "en"},
                                {"streamType": 2, "languageCode": "fr", "default": 1},
                            ],
                        }
                    ]
                },
                {"Part": [{"key": "/part/second", "Stream": [{"streamType": 2, "languageCode": "en"}]}]},
            ],
        }
    )
    assert item is not None
    assert item.extra["part_key"] == "/part/first"
    assert audio_info(item.extra)["audio_languages"] == ["fra"]
    assert not preferred_audio(audio_info(item.extra), ["eng"])


async def test_episode_progress_race_does_not_skip_audio_check():
    r = resolver()
    show = row("show")
    show.kind = "series"
    r._next_episode = AsyncMock(side_effect=[row("en", tracks("eng")), row("fr", tracks("fra"))])
    with pytest.raises(Conflict):
        await r._select_audio("p", [show], None, False)


async def test_invalid_tmdb_type_rejected_and_library_episode_has_own_progress_id():
    r = resolver()
    for method in (r.options, r.resolve):
        with pytest.raises(NotFound):
            await method("p", "tmdb:garbage:140607")
    show, episode = row("show"), row("ep", tracks("eng"))
    show.kind, episode.kind = "series", "episode"
    r._catalog.title_row = AsyncMock(return_value=show)
    r._next_episode = AsyncMock(return_value=episode)
    r._resolve_title = AsyncMock(return_value={})
    await r.resolve("p", "lib:iptv:show")
    assert r._resolve_title.call_args.args[2] == "lib:iptv:ep"
