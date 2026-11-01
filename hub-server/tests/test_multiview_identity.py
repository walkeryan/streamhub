from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routers.playback import _grant, _pair_identity
from app.services.errors import Forbidden
from app.services.playback import StreamGrant


def grant():
    return StreamGrant(
        "profile",
        "source",
        "https://fixture.invalid/live",
        {},
        "hls",
        True,
        None,
        False,
        profile_version=0,
        session_id="session",
        content_ref="ch:source:channel",
        pair_lease_id="lease",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile_version", None),
        ("session_id", None),
        ("session_id", ""),
        ("content_ref", None),
        ("content_ref", ""),
        ("content_ref", "lib:source:title"),
    ],
)
async def test_missing_pair_binding_rejects_before_session_or_audio_lookup(field, value):
    invalid = replace(grant(), **{field: value})
    c = SimpleNamespace(
        proxy=SimpleNamespace(ensure_running=lambda: None, release_pair=AsyncMock()),
        tokens=SimpleNamespace(verify=lambda token: invalid),
        content_policy=SimpleNamespace(require_grant=AsyncMock()),
        auth=SimpleNamespace(session_valid=AsyncMock()),
        playback=SimpleNamespace(_check_channel_audio=AsyncMock()),
    )
    with pytest.raises(Forbidden) as error:
        await _grant(c, "fixture-token")
    assert error.value.code == "multiview_expired"
    c.auth.session_valid.assert_not_awaited()
    c.playback._check_channel_audio.assert_not_awaited()
    c.proxy.release_pair.assert_awaited_once_with("lease", "profile", invalid.session_id)


def test_zero_version_is_valid_and_channel_identity_is_retained():
    assert _pair_identity(grant()) == (0, "session", "ch:source:channel")


@pytest.mark.parametrize("field", ["profile_version", "session_id", "content_ref"])
async def test_incomplete_resolved_grant_releases_pair_before_response(api, monkeypatch, field):
    from tests.test_multiview_api import fixture_pair

    source, body = await fixture_pair(api)
    original = api.c.tokens.verify

    def incomplete(token):
        decoded = original(token)
        return replace(decoded, **{field: None}) if decoded is not None else None

    monkeypatch.setattr(api.c.tokens, "verify", incomplete)
    result = await api.post("/playback/multiview", json=body)
    assert result.status_code == 403 and result.json()["code"] == "multiview_expired"
    assert not api.c.proxy._pairs and api.c.proxy._source_use(source) == 0
