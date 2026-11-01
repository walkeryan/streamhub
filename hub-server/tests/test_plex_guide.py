from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.adapters.plex.guide import advertised_grids, read_guide
from app.adapters.plex.provider import PlexSource
from app.domain.entities import SourceRef, SourceType
from app.domain.ports import EpgSourceProvider, LiveProvider

PROVIDER = "tv.plex.providers.epg.cloud:12"
GRID = f"/{PROVIDER}/grid"
CHANNELS = f"/{PROVIDER}/lineups/dvr/channels"
DAY = date(2026, 11, 1)
START = 1793491200


def providers():
    return {"MediaProvider": [{"identifier": PROVIDER, "Feature": [{"type": "grid", "key": GRID}]}]}


def channel(identity="lineup-station"):
    return {"id": identity, "gridKey": "grid-" + identity, "title": "EN - Fixture", "vcn": "6.1", "language": "en"}


def programme(identity="lineup-station", **overrides):
    return {
        "ratingKey": "plex%3A%2F%2Fepisode%2Fexact",
        "title": "Fixture programme",
        "summary": "Synthetic",
        "contentRating": "TV-G",
        "Media": [
            {
                "id": 17,
                "channelIdentifier": identity,
                "protocol": "livetv",
                "beginsAt": START,
                "endsAt": START + 1800,
                **overrides,
            }
        ],
    }


def adapter(handler, token="fixture-token"):
    ctx = SimpleNamespace(
        client_id="fixture-client",
        product="Fixture",
        http_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
    )
    return PlexSource(
        SourceRef("plex-source", SourceType.PLEX, "Fixture"),
        {"server_url": "https://plex.invalid", "token": token},
        ctx,
    )


async def test_existing_token_reads_only_advertised_metadata_and_preserves_identity():
    calls = []

    def handle(request):
        assert request.method == "GET"
        assert request.headers["X-Plex-Token"] == "fixture-token"
        assert "token" not in str(request.url).lower()
        calls.append(request)
        if request.url.path == "/media/providers":
            body = providers()
        elif request.url.path == CHANNELS:
            body = {"Channel": [channel()]}
        else:
            assert request.url.path == GRID
            assert dict(request.url.params) == {"channelGridKey": "grid-lineup-station", "date": DAY.isoformat()}
            body = {"Metadata": [programme()]}
        return httpx.Response(200, json={"MediaContainer": body})

    source = adapter(handle)
    result = await source.read_guide(DAY)
    assert len(calls) == 3 and result.status == "available"
    ch, airing = result.channels[0], result.programmes[0]
    assert (ch.source_id, ch.provider_id, ch.channel_id, ch.grid_key) == (
        "plex-source",
        PROVIDER,
        "lineup-station",
        "grid-lineup-station",
    )
    assert ch.audio_language is None and ch.audio_verified is False and ch.playable is False
    assert ch.language_hint == "en" and ch.number == "6.1"
    assert (airing.source_id, airing.provider_id, airing.channel_id, airing.programme_id, airing.airing_id) == (
        "plex-source",
        PROVIDER,
        "lineup-station",
        "plex%3A%2F%2Fepisode%2Fexact",
        "17",
    )
    assert airing.start == datetime.fromtimestamp(START, UTC)
    assert airing.stop == datetime.fromtimestamp(START + 1800, UTC)
    assert not isinstance(source, LiveProvider) and not isinstance(source, EpgSourceProvider)


async def test_absent_guide_is_explicit_and_never_invented():
    get = AsyncMock(return_value={"MediaProvider": [{"identifier": "com.plexapp.plugins.library"}]})
    result = await read_guide("source", DAY, get)
    assert result.status == "no_advertised_guide" and not result.channels and not result.programmes
    get.assert_awaited_once_with("/media/providers")


def test_unadvertised_foreign_and_unmatched_grid_paths_are_not_followed():
    for key in [
        "https://other.invalid/grid",
        "//other.invalid/grid",
        GRID + "?token=secret",
        GRID + "/../grid",
        "/unknown/grid",
    ]:
        assert (
            advertised_grids({"MediaProvider": [{"identifier": PROVIDER, "Feature": [{"type": "grid", "key": key}]}]})
            == []
        )
    assert (
        advertised_grids({"MediaProvider": [{"identifier": "different", "Feature": [{"type": "grid", "key": GRID}]}]})
        == []
    )


async def test_airings_need_exact_channel_identity_and_both_valid_times():
    rows = [
        programme("foreign"),
        programme(beginsAt=None),
        programme(endsAt=None),
        programme(endsAt=START),
        programme(beginsAt=True),
        programme(beginsAt="not-time"),
        programme(id=None),
        programme(protocol="http"),
        programme(),
    ]
    get = AsyncMock(side_effect=[providers(), {"Channel": [channel()]}, {"Metadata": rows}])
    result = await read_guide("source", DAY, get)
    assert len(result.programmes) == 1 and result.rejected_airings == 8


async def test_duplicate_names_and_numbers_never_merge_providers_or_channel_ids():
    other = "tv.plex.providers.epg.xmltv:13"
    available = providers()
    available["MediaProvider"].append({"identifier": other, "Feature": [{"type": "grid", "key": f"/{other}/grid"}]})
    get = AsyncMock(
        side_effect=[
            available,
            {"Channel": [channel()]},
            {"Metadata": [programme()]},
            {"Channel": [channel()]},
            {"Metadata": [programme()]},
        ]
    )
    result = await read_guide("source", DAY, get)
    assert len(result.channels) == len(result.programmes) == 2
    assert {row.provider_id for row in result.programmes} == {PROVIDER, other}


async def test_request_and_airing_bounds_are_explicit(monkeypatch):
    import app.adapters.plex.guide as module

    monkeypatch.setattr(module, "MAX_CHANNELS", 2)
    monkeypatch.setattr(module, "MAX_AIRINGS", 1)
    get = AsyncMock(
        side_effect=[
            providers(),
            {"Channel": [channel("one"), channel("two"), channel("three")]},
            {"Metadata": [programme("one"), programme("one", id=18)]},
            {"Metadata": [programme("two")]},
        ]
    )
    result = await read_guide("source", DAY, get)
    assert get.await_count == 4 and len(result.channels) == 2 and len(result.programmes) == 2
    assert result.truncated


@pytest.mark.parametrize("status", [401, 403, 302])
async def test_denied_or_redirected_metadata_never_falls_back_or_leaks_token(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://unrelated.invalid/guide"})

    with pytest.raises(PermissionError if status in (401, 403) else httpx.HTTPStatusError):
        await adapter(handle).read_guide(DAY)
    assert len(calls) == 1 and calls[0].url.host == "plex.invalid"


async def test_missing_link_malformed_response_and_oversize_fail_explicitly():
    never = AsyncMock(side_effect=AssertionError("no requests without linked capability"))
    with pytest.raises(PermissionError):
        await adapter(never, token="").read_guide(DAY)
    never.assert_not_called()
    for content in [b"{}", b"x" * (2 * 1024 * 1024 + 1)]:
        with pytest.raises(ValueError):
            await adapter(lambda request, body=content: httpx.Response(200, content=body)).read_guide(DAY)


async def test_later_grid_failure_does_not_return_partial_success():
    get = AsyncMock(
        side_effect=[
            providers(),
            {"Channel": [channel("one"), channel("two")]},
            {"Metadata": [programme("one")]},
            PermissionError("guide denied"),
        ]
    )
    with pytest.raises(PermissionError):
        await read_guide("source", DAY, get)
