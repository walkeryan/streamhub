"""Channels as they are shown: clean slot names, the game as the programme, no placeholder titles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.infra.models import Channel, Programme
from tests.conftest import Api

SLOT_LIVE = "(XX) (VANTIX+ 001) | Field Hockey: Northfield at Lakeside"
SLOT_SOON = "XX (VANTIX+ 002) | Football: Morning Football Show"
SLOT_OLD = "(XX) (VANTIX+ 003) | Soccer (M): Oakdale at Port Linden"
GAME_CHANNEL = "CORVAN LEAGUE | 02 - 1pm Kestrels at Badgers"
COMBAT = "COMBAT HD"
ENDZONE = "CORVAN LEAGUE ENDZONE ᴴᴰ"
NEWS = "Example News"


def stamp(when: datetime) -> str:
    return f"({when:%Y-%m-%d %H:%M:%S})"


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def playlist(at: datetime) -> list[tuple[str, str, str]]:
    """(tvg-id, group, name) of an invented sports line-up around ``at``."""
    return [
        ("vantix1", "XX| VANTIX+ EVENTS", f"{SLOT_LIVE} {stamp(at - timedelta(minutes=30))}"),
        ("vantix2", "XX| VANTIX+ EVENTS", f"{SLOT_SOON} {stamp(at + timedelta(hours=2))}"),
        ("vantix3", "XX| VANTIX+ EVENTS", f"{SLOT_OLD} {stamp(at - timedelta(days=42))}"),
        ("league2", "CORVAN LEAGUE", GAME_CHANNEL),
        ("combat", "XX| SPORTS", COMBAT),
        ("endzone", "XX| SPORTS", ENDZONE),
        ("news", "XX| NEWS", NEWS),
    ]


async def add_lineup(
    api: Api, rows: list[tuple[str, str, str]], name: str = "Panel", config: dict[str, Any] | None = None
) -> dict[str, Any]:
    await api.accept_disclaimer()
    text = "#EXTM3U\n" + "".join(
        f'#EXTINF:-1 tvg-id="{epg}" group-title="{group}",{label}\nhttp://streams.example/live/{epg}.ts\n'
        for epg, group, label in rows
    )
    response = await api.post(
        "/sources", json={"type": "m3u", "name": name, "config": {"file": text, **(config or {})}}
    )
    assert response.status_code == 201, response.text
    await api.c.sync.wait_idle()
    source: dict[str, Any] = response.json()
    return source


async def add_placeholders(api: Api, source: dict[str, Any], at: datetime, skip: tuple[str, ...] = ()) -> None:
    """The manager's filler: every channel "shows" its own name in four-hour blocks."""
    async with api.c.db.session() as s:
        for ch in (await s.execute(select(Channel).where(Channel.source_id == source["id"]))).scalars():
            if ch.epg_id in skip:
                continue
            for block in (-1, 3):
                begin = at + timedelta(hours=block)
                s.add(
                    Programme(
                        feed=ch.source_id,
                        epg_channel=ch.epg_id or "",
                        title=ch.name,
                        start=begin,
                        stop=begin + timedelta(hours=4),
                    )
                )
        await s.commit()


async def add_programme(api: Api, source: dict[str, Any], epg: str, title: str, start: datetime, hours: float) -> None:
    async with api.c.db.session() as s:
        s.add(
            Programme(feed=source["id"], epg_channel=epg, title=title, start=start, stop=start + timedelta(hours=hours))
        )
        await s.commit()


async def listed(api: Api) -> dict[str, dict[str, Any]]:
    response = await api.get("/channels", params={"language": "all"})
    assert response.status_code == 200, response.text
    return {c["epg_id"]: c for c in response.json()["channels"]}


async def test_a_slot_channel_is_named_by_its_network_and_keeps_its_stored_identity(api: Api) -> None:
    at = now()
    source = await add_lineup(api, playlist(at))
    await add_placeholders(api, source, at)
    async with api.c.db.session() as s:
        stored = {c.epg_id: c for c in (await s.execute(select(Channel))).scalars()}
    channels = await listed(api)
    assert {epg: c["name"] for epg, c in channels.items()} == {
        "vantix1": "VANTIX+ 001",
        "vantix2": "VANTIX+ 002",
        "vantix3": "VANTIX+ 003",
        "league2": "CORVAN LEAGUE 02",
        "combat": COMBAT,
        "endzone": ENDZONE,
        "news": NEWS,
    }
    for epg, row in stored.items():
        assert channels[epg]["id"] == f"ch:{row.source_id}:{row.ext_id}"
        assert (channels[epg]["ext_id"], channels[epg]["number"]) == (row.ext_id, row.number)
    # The stored row is what favourites, numbers and recording keys hang on: it is not rewritten.
    assert stored["vantix1"].name.startswith(SLOT_LIVE) and stored["league2"].name == GAME_CHANNEL
    favourite = await api.put("/favorites", json={"kind": "channel", "id": channels["vantix1"]["id"]})
    assert favourite.status_code == 204, favourite.text
    assert (await listed(api))["vantix1"]["is_favorite"] is True
    tile = (await api.get("/favorites")).json()[0]
    assert (tile["id"], tile["title"]) == (channels["vantix1"]["id"], "VANTIX+ 001")
    assert tile["now_playing"]["title"] == "Northfield at Lakeside"


async def test_the_game_is_the_programme_of_a_live_or_coming_slot_and_placeholders_are_not_listings(
    api: Api,
) -> None:
    at = now()
    source = await add_lineup(api, playlist(at))
    await add_placeholders(api, source, at, skip=("news",))
    await add_programme(api, source, "news", "Evening Report", at - timedelta(minutes=10), 1)
    await add_programme(api, source, "news", "Late Report", at + timedelta(minutes=50), 1)
    channels = await listed(api)

    live = channels["vantix1"]
    assert live["next"] is None
    assert (live["now"]["title"], live["now"]["category"]) == ("Northfield at Lakeside", "Field Hockey")
    assert (live["now"]["is_sport"], live["now"]["teams"]) == (True, ["Northfield", "Lakeside"])
    start = datetime.fromisoformat(live["now"]["start"])
    assert start == at - timedelta(minutes=30)
    assert datetime.fromisoformat(live["now"]["stop"]) == start + timedelta(minutes=210)
    assert live["now"]["channel_id"] == live["id"] and live["now"]["id"].startswith("prog:slot-")

    coming = channels["vantix2"]
    assert coming["now"] is None
    assert (coming["next"]["title"], coming["next"]["teams"]) == ("Morning Football Show", [])
    assert datetime.fromisoformat(coming["next"]["start"]) == at + timedelta(hours=2)

    # A name left over from six weeks ago, a dateless game channel and a network without a guide: no programme.
    for epg in ("vantix3", "league2", "combat", "endzone"):
        assert (channels[epg]["now"], channels[epg]["next"]) == (None, None), epg
    assert (channels["news"]["now"]["title"], channels["news"]["next"]["title"]) == ("Evening Report", "Late Report")


async def test_a_real_listing_on_a_network_is_kept_beside_its_placeholders(api: Api) -> None:
    at = now()
    source = await add_lineup(api, playlist(at))
    await add_placeholders(api, source, at, skip=("combat",))
    await add_programme(api, source, "combat", "combat   hd", at - timedelta(hours=1), 2)
    await add_programme(api, source, "combat", "Boxing: Rivers vs Stone", at + timedelta(hours=1), 2)
    combat = (await listed(api))["combat"]
    assert combat["now"] is None
    assert (combat["next"]["title"], combat["next"]["teams"]) == ("Boxing: Rivers vs Stone", ["Rivers", "Stone"])


async def test_the_guide_grid_leaves_placeholders_out_and_shows_the_game_of_a_slot(api: Api) -> None:
    at = now()
    source = await add_lineup(api, playlist(at))
    await add_placeholders(api, source, at, skip=("news",))
    await add_programme(api, source, "news", "Evening Report", at - timedelta(minutes=10), 1)
    channels = await listed(api)
    grid = (
        await api.get(
            "/epg",
            params={
                "channel_ids": ",".join(c["id"] for c in channels.values()),
                "from": (at - timedelta(hours=1)).isoformat(),
                "to": (at + timedelta(hours=6)).isoformat(),
            },
        )
    ).json()
    titles = {epg: [p["title"] for p in grid[c["id"]]] for epg, c in channels.items()}
    assert titles == {
        "vantix1": ["Northfield at Lakeside"],
        "vantix2": ["Morning Football Show"],
        "vantix3": [],
        "league2": [],
        "combat": [],
        "endzone": [],
        "news": ["Evening Report"],
    }


async def test_home_on_now_shows_clean_names_and_never_a_placeholder_title(api: Api) -> None:
    at = now()
    source = await add_lineup(api, playlist(at))
    await add_placeholders(api, source, at, skip=("news",))
    await add_programme(api, source, "news", "Evening Report", at - timedelta(minutes=10), 1)
    home = (await api.get("/home")).json()
    row = next(r for r in home["rows"] if r["id"] == "on_now")
    # Only a channel with a real listing counts as scheduled; filler does not put a channel "on now".
    assert [(t["title"], t["now_playing"]["title"]) for t in row["tiles"]] == [(NEWS, "Evening Report")]

    async with api.c.db.session() as s:
        news = (await s.execute(select(Programme).where(Programme.title == "Evening Report"))).scalar_one()
        await s.delete(news)
        await s.commit()
    home = (await api.get("/home")).json()
    row = next(r for r in home["rows"] if r["id"] == "on_now")
    by_title = {t["title"]: t["now_playing"] for t in row["tiles"]}
    # Without any guide the row falls back to plain channels: clean names, the live game, no repeated name.
    assert by_title["VANTIX+ 001"]["title"] == "Northfield at Lakeside"
    assert by_title["VANTIX+ 003"] is None and by_title[COMBAT] is None and by_title["CORVAN LEAGUE 02"] is None
    assert not any(name.startswith(("(XX)", "XX (")) for name in by_title)


async def test_the_slot_clock_follows_the_source_event_timezone_and_length(api: Api) -> None:
    at = now()
    local = at.astimezone(UTC) - timedelta(hours=5)  # what a wall clock five hours behind UTC shows
    rows = [("vantix9", "XX| VANTIX+ EVENTS", f"(XX) (VANTIX+ 009) | Rugby: Oakdale v Riverton {stamp(local)}")]
    source = await add_lineup(api, rows, config={"event_timezone": "Etc/GMT+5", "event_duration_minutes": 90})
    assert source["config_summary"]["event_timezone"] == "Etc/GMT+5"
    assert source["config_summary"]["event_duration_minutes"] == 90
    game = (await listed(api))["vantix9"]["now"]
    assert datetime.fromisoformat(game["start"]) == at
    assert datetime.fromisoformat(game["stop"]) == at + timedelta(minutes=90)
    assert game["teams"] == ["Oakdale", "Riverton"]

    # Read as UTC (the default) the same name is a game that started five hours ago and is over.
    patched = await api.patch(f"/sources/{source['id']}", json={"config": {"event_timezone": None}})
    assert patched.status_code == 200, patched.text
    assert patched.json()["config_summary"]["event_timezone"] == "UTC"
    assert (await listed(api))["vantix9"]["now"] is None
