"""The player's extras on a title's descriptor: markers, the episodes either side, scrub previews.

The library server is respx-mocked. Every show, episode and image here is made up.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import update

from app.infra.models import Source, Title
from app.services import playback as playback_module
from app.services.episode_nav import EpisodeNavigator
from app.services.thumbnails import BifError, parse_bif
from tests.conftest import Api

PLEX = "http://plex.example.test:32400"
MIN = 60_000


def bif(times_s: list[int], multiplier: int = 1000) -> bytes:
    """A small BIF file whose frame i is the bytes ``b"frame-<i>"`` (not real JPEGs: nothing decodes them)."""
    frames = [f"frame-{i}".encode() for i in range(len(times_s))]
    header = b"\x89BIF\r\n\x1a\n" + struct.pack("<III", 0, len(frames), multiplier) + b"\0" * 44
    offset = len(header) + 8 * (len(frames) + 1)
    index = b""
    for stamp, frame in zip(times_s, frames, strict=True):
        index += struct.pack("<II", stamp, offset)
        offset += len(frame)
    index += struct.pack("<II", 0xFFFFFFFF, offset)
    return header + index + b"".join(frames)


class Plex:
    """Metadata with markers for any rating key in ``items``; preview indexes in ``indexes``."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.indexes: dict[str, bytes] = {}
        self.down = False
        self.asked: list[str] = []
        router.get(url__regex=rf"^{PLEX}/library/metadata/(?P<key>\d+)(?P<sub>/allLeaves|/children)?(\?.*)?$").mock(
            side_effect=self.metadata
        )
        router.get(url__regex=rf"^{PLEX}/library/parts/(?P<part>\d+)/indexes/sd(\?.*)?$").mock(side_effect=self.index)

    def metadata(self, request: httpx.Request, key: str, sub: str | None = None) -> httpx.Response:
        self.asked.append(f"{key}{sub or ''}?{request.url.query.decode()}")
        if self.down:
            return httpx.Response(500)
        rows = [self.items[key]] if key in self.items and not sub else []
        return httpx.Response(200, json={"MediaContainer": {"Metadata": rows}})

    def index(self, request: httpx.Request, part: str) -> httpx.Response:
        assert request.headers["x-plex-token"] == "plex-token"
        return httpx.Response(200, content=self.indexes[part]) if part in self.indexes else httpx.Response(404)


@pytest.fixture
def plex() -> Iterator[Plex]:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.route(host="hub.test").pass_through()
        yield Plex(router)


def ep(source: str, show: str, season: int, number: int, *, key: str | None = None, **kw: Any) -> Title:
    return Title(
        source_id=source,
        ext_id=key or f"{show}-s{season}e{number}",
        kind="episode",
        title=kw.pop("title", f"Episode {number}"),
        parent_ext_id=f"{show}-s{season}",
        season_number=season,
        episode_number=number,
        duration_ms=50 * MIN,
        poster=f"{PLEX}/library/metadata/{key or number}/thumb/1" if source == "plex" else None,
        extra={"part_key": f"/library/parts/{key or number}/file.mkv", "container": "mkv"},
        **kw,
    )


def season(source: str, show: str, number: int) -> Title:
    return Title(
        source_id=source,
        ext_id=f"{show}-s{number}",
        kind="season",
        title=f"Season {number}",
        parent_ext_id=show,
        season_number=number,
    )


async def library(api: Api, *rows: Title, sources: tuple[Source, ...] = ()) -> None:
    await api.accept_disclaimer()
    config = api.c.crypto.encrypt_json({"server_url": PLEX, "token": "plex-token"})
    async with api.c.db.session() as s:
        s.add(Source(id="plex", type="plex", name="Library", config_enc=config))
        s.add_all(sources)
        await s.flush()
        s.add(Title(source_id="plex", ext_id="show", kind="show", title="The Lighthouse Keepers", tmdb_id=9001))
        s.add_all([season("plex", "show", 1), season("plex", "show", 2)])
        s.add_all(
            [
                ep("plex", "show", 1, 1, key="71", title="Arrival"),
                ep("plex", "show", 1, 2, key="72", title="The Storm"),
                ep("plex", "show", 2, 1, key="73"),
                ep("plex", "show", 2, 2, key="74", title="Low Tide"),
            ]
        )
        film = {"part_key": "/library/parts/55/file.mkv", "container": "mkv"}
        s.add(Title(source_id="plex", ext_id="55", kind="movie", title="The Mill", duration_ms=120 * MIN, extra=film))
        s.add_all(rows)
        await s.commit()


async def resolve(api: Api, ref: str) -> dict[str, Any]:
    r = await api.post("/playback/resolve", json={"id": ref, "client": "android_tv"})
    assert r.status_code == 200, r.text
    return dict(r.json())


def marked(key: str, *markers: tuple[str, int, int], part: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ratingKey": key,
        "type": "episode",
        "Marker": [{"type": t, "startTimeOffset": a, "endTimeOffset": b} for t, a, b in markers],
        "Media": [{"Part": [part or {"id": 900 + int(key), "key": f"/library/parts/{key}/file.mkv"}]}],
    }


# ---------------------------------------------------------------- markers and numbers
async def test_an_episode_carries_its_markers_numbers_show_and_neighbours(api: Api, plex: Plex) -> None:
    await library(api)
    plex.items["72"] = marked(
        "72", ("credits", 48 * MIN, 50 * MIN), ("intro", 30_000, 95_000), ("commercial", 600_000, 660_000)
    )
    d = await resolve(api, "lib:plex:72")
    assert d["markers"] == [
        {"type": "intro", "start_ms": 30_000, "end_ms": 95_000},
        {"type": "credits", "start_ms": 48 * MIN, "end_ms": 50 * MIN},
    ]
    assert (d["show"], d["season_number"], d["episode_number"]) == (
        {"id": "tmdb:tv:9001", "title": "The Lighthouse Keepers"},
        1,
        2,
    )
    assert any("includeMarkers=1" in q for q in plex.asked)
    # The last episode of season 1: next crosses into season 2, previous stays in season 1.
    assert d["next"] == {
        "id": "lib:plex:73",
        "title": "Episode 1",
        "subtitle": "S2 · E1",
        "image": d["next"]["image"],
        "duration_ms": 50 * MIN,
    }
    assert d["next"]["image"].startswith("http://hub.test/api/v1/images/")
    assert d["previous"]["id"] == "lib:plex:71" and d["previous"]["title"] == "Arrival"
    assert d["thumbnails"] is None  # this part has no preview index


async def test_first_and_last_episodes_have_one_side_and_a_film_has_none(api: Api, plex: Plex) -> None:
    await library(api)
    first, last = await resolve(api, "lib:plex:71"), await resolve(api, "lib:plex:74")
    assert first["previous"] is None and first["next"]["id"] == "lib:plex:72"
    assert last["next"] is None and last["previous"] == {**last["previous"], "id": "lib:plex:73", "subtitle": "S2 · E1"}
    # Season 2's first episode goes back to season 1's last.
    assert (await resolve(api, "lib:plex:73"))["previous"]["id"] == "lib:plex:72"
    plex.items["55"] = {
        "ratingKey": "55",
        "type": "movie",
        "Marker": [{"type": "credits", "startTimeOffset": 110 * MIN, "endTimeOffset": 120 * MIN}],
    }
    film = await resolve(api, "lib:plex:55")
    assert film["markers"] == [{"type": "credits", "start_ms": 110 * MIN, "end_ms": 120 * MIN}]
    assert (film["show"], film["next"], film["previous"], film["season_number"]) == (None, None, None, None)


async def test_a_season_held_only_by_another_copy_still_counts(api: Api, plex: Plex) -> None:
    other = Source(
        id="vod", type="m3u", name="Provider", config_enc=api.c.crypto.encrypt_json({"url": "http://x.test"})
    )
    await library(
        api,
        Title(source_id="vod", ext_id="sh", kind="series", title="The Lighthouse Keepers", tmdb_id=9001),
        season("vod", "sh", 2),
        season("vod", "sh", 3),
        ep("vod", "sh", 2, 1),
        ep("vod", "sh", 3, 1, title="New Light"),
        ep("vod", "sh", 3, 2),
        sources=(other,),
    )
    d = await resolve(api, "lib:plex:74")
    # Season 3 exists only in the other copy; season 2 is the library's own, so its episodes come first.
    assert d["next"]["id"] == "lib:vod:sh-s3e1" and d["next"]["title"] == "New Light"
    assert d["next"]["image"] is None
    assert d["previous"]["id"] == "lib:plex:73"


async def test_an_episode_the_viewer_may_not_open_is_not_offered(api: Api, plex: Plex) -> None:
    await library(api)
    async with api.c.db.session() as s:
        await s.execute(update(Title).where(Title.ext_id == "73").values(adult=True))
        await s.commit()
    # Adult and not unlocked on this session: neither direction offers it.
    assert (await resolve(api, "lib:plex:72"))["next"] is None
    assert (await resolve(api, "lib:plex:74"))["previous"] is None

    class Policy:
        enabled = True

        def applies_to(self, principal: Any) -> bool:
            return True

        async def require(self, profile_id: str, ref: str, at: Any = None) -> None:
            return None

        async def allowed_refs(self, profile_id: str, refs: list[str], at: Any = None, **kw: Any) -> set[str]:
            return {r for r in refs if r != "lib:plex:71"}

        def is_restricted(self, profile: Any) -> bool:
            return False

    original = api.c.playback.content_policy
    api.c.playback.content_policy = Policy()
    try:
        # A rating limit that refuses episode 1 leaves episode 2 without a previous one.
        d = await resolve(api, "lib:plex:72")
        assert d["previous"] is None
    finally:
        api.c.playback.content_policy = original


async def test_the_library_server_failing_or_hanging_never_fails_playback(
    api: Api, plex: Plex, monkeypatch: pytest.MonkeyPatch
) -> None:
    await library(api)
    plex.down = True
    d = await resolve(api, "lib:plex:72")
    assert d["markers"] == [] and d["thumbnails"] is None
    assert d["next"]["id"] == "lib:plex:73"  # the catalog still knows the episodes

    async def hangs(*args: Any, **kwargs: Any) -> Any:
        import asyncio

        await asyncio.sleep(30)

    monkeypatch.setattr(playback_module, "EXTRAS_TIMEOUT_S", 0.05)
    monkeypatch.setattr(api.c.playback._navigator, "neighbours", hangs)
    plex.down = False
    d = await resolve(api, "lib:plex:71")
    assert (d["next"], d["previous"], d["show"]) == (None, None, None)
    assert d["episode_number"] == 1 and d["url"]


# ---------------------------------------------------------------- scrub previews
async def test_preview_frames_are_served_from_the_parts_index(api: Api, plex: Plex) -> None:
    await library(api)
    plex.items["72"] = marked("72", part={"id": 501, "key": "/library/parts/72/file.mkv", "indexes": "sd"})
    plex.indexes["501"] = bif([0, 5, 10, 15], multiplier=1000)
    d = await resolve(api, "lib:plex:72")
    thumbs = d["thumbnails"]
    assert thumbs["url"].startswith("http://hub.test/api/v1/thumbnails/") and thumbs["interval_ms"] > 0
    path = thumbs["url"].removeprefix("http://hub.test/api/v1")
    for ms, frame in (
        (0, b"frame-0"),
        (4_999, b"frame-0"),
        (5_000, b"frame-1"),
        (12_000, b"frame-2"),
        (99_000, b"frame-3"),
    ):
        r = await api.client.get("/api/v1" + path, params={"ms": ms})
        assert r.status_code == 200 and r.content == frame
        assert r.headers["content-type"] == "image/jpeg"
        assert r.headers["cache-control"].startswith("public, max-age=") and "immutable" in r.headers["cache-control"]
    # The index was fetched once and kept; the next descriptor knows its spacing.
    assert (await resolve(api, "lib:plex:72"))["thumbnails"]["interval_ms"] == 5_000
    assert (await api.client.get("/api/v1/thumbnails/not-a-grant", params={"ms": 0})).status_code == 404
    assert (await api.client.get("/api/v1" + path, params={"ms": -1})).status_code == 422


async def test_a_missing_preview_index_is_a_404_not_an_error(api: Api, plex: Plex) -> None:
    await library(api)
    plex.items["72"] = marked("72", part={"id": 502, "key": "/library/parts/72/file.mkv", "indexes": "sd"})
    d = await resolve(api, "lib:plex:72")
    r = await api.client.get(
        "/api/v1" + d["thumbnails"]["url"].removeprefix("http://hub.test/api/v1"), params={"ms": 0}
    )
    assert r.status_code == 404


def test_bif_parsing() -> None:
    parsed = parse_bif(bif([0, 2, 4, 6, 8], multiplier=1000))
    assert (len(parsed), parsed.interval_ms, parsed.times_ms[-1]) == (5, 2000, 8000)
    assert parsed.frame(3_999) == b"frame-1" and parsed.frame(4_000) == b"frame-2"
    # A multiplier of 0 means seconds.
    assert parse_bif(bif([0, 10], multiplier=0)).times_ms == [0, 10_000]
    for broken in (b"", b"not a bif" * 20, bif([0, 1])[:70], bif([5, 1])):
        with pytest.raises(BifError):
            parse_bif(broken)
    corrupt = bytearray(bif([0, 1]))
    struct.pack_into("<I", corrupt, 64 + 4, 10_000)  # first frame's offset past the end of the file
    with pytest.raises(BifError):
        parse_bif(bytes(corrupt))


# ---------------------------------------------------------------- the navigator on its own
def nav(rows: list[Title], copies: dict[str, list[Title]] | None = None) -> EpisodeNavigator:
    by_key = {(r.source_id, r.ext_id): r for r in rows}

    async def title_row(source: str, ext: str) -> Title | None:
        return by_key.get((source, ext))

    async def children(row: Title) -> list[Title]:
        if row.kind in ("show", "series"):
            seasons = [r for r in rows if r.source_id == row.source_id and r.parent_ext_id == row.ext_id]
            ids = {s.ext_id for s in seasons if s.kind == "season"}
            return seasons + [r for r in rows if r.source_id == row.source_id and r.parent_ext_id in ids]
        return [r for r in rows if r.source_id == row.source_id and r.parent_ext_id == row.ext_id]

    async def nothing(row: Title) -> list[Title]:
        return []

    async def copy_rows(show: Title) -> list[Title]:
        return (copies or {}).get(show.ext_id, [show])

    return EpisodeNavigator(title_row=title_row, stored_children=children, fetch_children=nothing, copies=copy_rows)


def show(source: str, ext: str) -> Title:
    return Title(source_id=source, ext_id=ext, kind="series", title="Fixture")


async def test_previous_reaches_back_into_a_season_only_another_copy_has() -> None:
    a, b = show("a", "x"), show("b", "y")
    rows = [
        a,
        b,
        season("a", "x", 3),
        ep("a", "x", 3, 1),
        ep("a", "x", 3, 2),
        season("b", "y", 1),
        season("b", "y", 2),
        ep("b", "y", 2, 1),
        ep("b", "y", 2, 7),
        season("b", "y", 0),
        ep("b", "y", 0, 1),
    ]
    navigator = nav(rows, {"x": [a, b], "y": [b, a]})
    near = await navigator.neighbours(rows[3])
    # Season 2 is the other copy's; its last episode (by number) comes before season 3.
    assert near.previous is not None and near.previous.ext_id == "y-s2e7"
    assert near.next is not None and near.next.ext_id == "x-s3e2"
    # Specials are never stepped into: season 1 has no episodes, so nothing comes before season 2.
    first = await navigator.neighbours(rows[7])
    assert first.previous is None and first.next is not None and first.next.ext_id == "y-s2e7"
    special = await navigator.neighbours(rows[10])
    assert (special.previous, special.next) == (None, None)


async def test_gaps_in_numbering_and_missing_parents() -> None:
    a = show("a", "x")
    rows = [a, season("a", "x", 1), ep("a", "x", 1, 1), ep("a", "x", 1, 4), ep("a", "x", 1, 9)]
    near = await nav(rows).neighbours(rows[3])
    assert near.previous is not None and near.next is not None
    assert (near.previous.episode_number, near.next.episode_number) == (1, 9)
    orphan = Title(source_id="a", ext_id="o", kind="episode", title="E", parent_ext_id="gone", episode_number=2)
    assert await nav(rows).neighbours(orphan) == type(near)()
