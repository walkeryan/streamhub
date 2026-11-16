"""Switching to another feed of a game: the alternates a player asks for, and the stream it leaves."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.infra.models import Channel, ChannelGroup, Programme, Source
from app.proxy.stream_proxy import StreamProxy, Viewer
from app.services.classification import classification_record, summarize
from app.services.sports import feed_alternates
from tests.conftest import Api
from tests.people import member
from tests.test_hls_admission import harness as base_harness

harness = base_harness

NOW = datetime.now(UTC).replace(microsecond=0)
GAME = "NHL: Ravens vs Otters"


def listing(channel: str, title: str = GAME, code: str | None = "TV-G", feed: str = "games") -> Programme:
    rating = summarize("xmltv", "programme", [classification_record("US", "US-TVPG", code)]) if code else {}
    return Programme(
        feed=feed,
        epg_channel=channel,
        start=NOW - timedelta(minutes=30),
        stop=NOW + timedelta(minutes=90),
        title=title,
        age_evidence=rating,
    )


@pytest.fixture
async def games(api: Api) -> Api:
    """One game on four channels (one of them hidden, one rated for adults), and a studio show on one."""
    async with api.c.db.session() as s:
        s.add(Source(id="games", name="Games", type="xtream", config_enc="unused"))
        await s.flush()
        s.add_all(
            [
                ChannelGroup(id="open", source_id="games", name="Open"),
                ChannelGroup(id="shut", source_id="games", name="Shut", hidden=True),
                *(
                    Channel(
                        source_id="games",
                        ext_id=ext,
                        name=name,
                        number=number,
                        epg_id=ext,
                        group_id=group,
                        group_name=group.title(),
                    )
                    for ext, name, number, group in [
                        ("north", "Harbor Sports 1", 11, "open"),
                        ("south", "Harbor Sports 2", 12, "open"),
                        ("east", "Ridge Sports", 13, "open"),
                        ("late", "Ridge Late", 14, "open"),
                        ("back", "Backroom Sports", 15, "shut"),
                        ("solo", "Lone Arena", 16, "open"),
                    ]
                ),
                listing("north"),
                listing("south"),
                listing("east"),
                listing("late", code="TV-MA"),
                listing("back"),
                listing("solo", "NBA: Herons vs Lynx"),
            ]
        )
        await s.commit()
    return api


async def test_alternates_go_round_the_game_from_the_channel_after_this_one(games: Api) -> None:
    api = games
    found = await feed_alternates(api.c.channels, api.profile_id, "ch:games:south", now=NOW)
    assert found["title"] == GAME and found["event_id"]
    # In the event's own order (by channel number), starting after the channel itself and wrapping round.
    assert [a["channel_id"] for a in found["alternates"]] == ["ch:games:east", "ch:games:late", "ch:games:north"]
    assert found["alternates"][0]["channel_name"] == "Ridge Sports" and found["alternates"][0]["channel_number"] == 13
    assert all(a["programme_id"].startswith("prog:") for a in found["alternates"])
    # A hidden group's channel is no feed for anyone, as on the Sports screen.
    assert all(a["channel_id"] != "ch:games:back" for a in found["alternates"])


async def test_no_alternates_without_a_second_feed_or_a_current_game(games: Api) -> None:
    api = games
    for ref in ("ch:games:solo", "ch:games:missing", "lib:games:1", "ch:games:back"):
        assert (await feed_alternates(api.c.channels, api.profile_id, ref, now=NOW))["alternates"] == []
    later = await feed_alternates(api.c.channels, api.profile_id, "ch:games:north", now=NOW + timedelta(hours=3))
    assert later == {"event_id": None, "title": None, "alternates": []}


async def test_alternates_endpoint_answers_within_the_viewers_ceiling(games: Api) -> None:
    api = games
    r = await api.get("/playback/alternates", params={"id": "ch:games:north"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    assert [a["channel_id"] for a in r.json()["alternates"]] == ["ch:games:south", "ch:games:east", "ch:games:late"]
    kid = await member(api, "kid", "Kid")
    r = await kid.get("/playback/alternates", params={"id": "ch:games:north"})
    assert r.status_code == 200, r.text
    # The feed whose programme is above the kid's ceiling is never offered, here or on the Sports screen.
    assert [a["channel_id"] for a in r.json()["alternates"]] == ["ch:games:south", "ch:games:east"]
    sports = await kid.get("/sports/events", params={"status": "current", "language": "all"})
    listed = {f["channel_id"] for e in sports.json()["events"] if e["title"] == GAME for f in e["feeds"]}
    assert listed == {"ch:games:north", "ch:games:south", "ch:games:east"}
    assert (await api.get("/playback/alternates")).status_code == 422
    assert (await api.client.get("/api/v1/playback/alternates", params={"id": "ch:games:north"})).status_code == 401


async def test_resolve_names_the_channel_it_replaces_only_for_a_live_switch(games: Api, monkeypatch) -> None:
    api = games
    release = AsyncMock(return_value=1)
    monkeypatch.setattr(api.c.proxy, "release_replaced", release)
    generation = (await api.get("/playback/generation")).json()["generation"]
    body: dict[str, Any] = {"id": "ch:games:east", "client": "web", "recovery_generation": generation}
    # The resolve itself may fail on this fixture source; what matters is what was released before it.
    await api.post("/playback/resolve", json={**body, "replaces": "ch:games:north"})
    release.assert_awaited_once()
    assert release.await_args.args[0] == api.profile_id and release.await_args.args[2] == "ch:games:north"
    release.reset_mock()
    for extra in (
        {},
        {"replaces": "ch:games:east"},
        {"replaces": "lib:games:1"},
        {"replaces": "ch:games:north", "rewind_at": (NOW - timedelta(minutes=5)).isoformat()},
    ):
        await api.post("/playback/resolve", json={**body, **extra})
    release.assert_not_awaited()


# ---------------------------------------------------------------- the proxy


class Blocked(httpx.AsyncByteStream):
    """A live upstream that answers and then sends nothing: a stalled feed."""

    async def __aiter__(self):
        await asyncio.Event().wait()
        yield b""


def shared_proxy(tmp_path) -> StreamProxy:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Blocked()))
    return StreamProxy(lambda **kw: httpx.AsyncClient(transport=transport, **kw), tmp_path)


def viewer(profile: str = "p", session: str = "tv", ref: str = "ch:games:north") -> Viewer:
    return Viewer(profile, "Harbor Sports 1", "channel", ref, session_id=session)


async def open_shared(proxy: StreamProxy, who: Viewer, key: str = "games:north") -> Any:
    _, stream = await proxy.live_shared(
        key, "https://fixture.invalid/north", {}, "games", max_connections=1, viewer=who
    )
    return stream


async def test_a_lingering_upstream_is_given_back_at_once_by_the_device_that_left_it(tmp_path) -> None:
    proxy = shared_proxy(tmp_path)
    try:
        stream = await open_shared(proxy, viewer())
        await stream.aclose()
        assert "games:north" in proxy._shared and proxy._source_use("games") == 1  # lingering
        # Another profile, another device of the same profile, or another channel gives nothing back.
        assert await proxy.release_replaced("q", "tv", "ch:games:north") == 0
        assert await proxy.release_replaced("p", "phone", "ch:games:north") == 0
        assert await proxy.release_replaced("p", "tv", "ch:games:south") == 0
        assert await proxy.release_replaced("p", None, "ch:games:north") == 0
        assert proxy._source_use("games") == 1
        assert await proxy.release_replaced("p", "tv", "ch:games:north") == 1
        assert proxy._source_use("games") == 0 and "games:north" not in proxy._shared
        # The next feed on the one-connection source is admitted straight away.
        replacement = await open_shared(proxy, viewer(ref="ch:games:south"), key="games:south")
        await replacement.aclose()
    finally:
        proxy.pause()
        await proxy.stop_owned()


async def test_an_upstream_this_device_still_reads_alone_is_closed_with_its_reader(tmp_path) -> None:
    proxy = shared_proxy(tmp_path)
    try:
        stream = await open_shared(proxy, viewer())
        assert await proxy.release_replaced("p", "tv", "ch:games:north") == 1
        assert proxy._source_use("games") == 0
        # The reader is told the stream ended rather than left waiting.
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 1)
    finally:
        proxy.pause()
        await proxy.stop_owned()


async def test_an_upstream_someone_else_uses_is_never_given_back(tmp_path) -> None:
    proxy = shared_proxy(tmp_path)
    try:
        mine = await open_shared(proxy, viewer())
        theirs = await open_shared(proxy, viewer("q", "living-room"))
        assert await proxy.release_replaced("p", "tv", "ch:games:north") == 0
        await mine.aclose()
        assert await proxy.release_replaced("p", "tv", "ch:games:north") == 0
        assert proxy._shared["games:north"].subscribers == 1
        await theirs.aclose()
        # The other viewer left last: the linger is theirs, not this device's to cut short.
        assert await proxy.release_replaced("p", "tv", "ch:games:north") == 0
        # A recording's tap keeps the upstream whatever the viewer does.
        again = await open_shared(proxy, viewer())
        tap = proxy.tap_shared("games:north")
        assert tap is not None
        await again.aclose()
        assert await proxy.release_replaced("p", "tv", "ch:games:north") == 0
        assert proxy._shared["games:north"].subscribers == 1
        tap.close()
    finally:
        proxy.pause()
        await proxy.stop_owned()


async def hls(proxy: StreamProxy, key: str, who: Viewer | None = None, maximum: int = 1) -> Any:
    return await proxy.hls_session(
        key,
        f"https://provider.invalid/{key}",
        {},
        live=True,
        transcode="none",
        source_id="source-a",
        max_connections=maximum,
        viewer=who,
    )


async def test_a_switch_frees_the_old_hls_session_without_the_eviction_grace(harness) -> None:
    proxy, clock, processes, _ = harness
    old = await hls(proxy, "north", viewer())
    assert await proxy.release_replaced("p", "tv", "ch:games:north") == 1
    assert old.proc.stopped and "north" not in proxy._hls
    await hls(proxy, "south", viewer(ref="ch:games:south"))
    # No waiting for the old session to look abandoned (HLS_SWITCH_GRACE_S).
    assert clock.now < 1 and len(processes) == 2
    await proxy.shutdown()


async def test_an_hls_session_another_device_watches_or_a_room_keeps_stays(harness) -> None:
    proxy, clock, _, _ = harness
    shared = await hls(proxy, "north", viewer(), maximum=3)
    await hls(proxy, "north", viewer("p", "phone"), maximum=3)
    assert await proxy.release_replaced("p", "tv", "ch:games:north") == 0
    assert not shared.proc.stopped
    # Once the other device has not asked for the playlist for the idle window, it is not watching.
    clock.now += 61
    await hls(proxy, "north", viewer(), maximum=3)
    assert await proxy.release_replaced("p", "tv", "ch:games:north") == 1
    room = await hls(proxy, "room", viewer(ref="ch:games:east"), maximum=3)
    proxy.keep("room")
    assert await proxy.release_replaced("p", "tv", "ch:games:east") == 0
    assert not room.proc.stopped
    await proxy.shutdown()
