"""Channel groups of two levels: panel → sync → groups → channel filter. Every name is invented."""

from __future__ import annotations

from typing import Any

import respx
from sqlalchemy import select

from app.infra.models import Channel, ChannelGroup, Source
from app.services.sources import group_id
from tests.conftest import Api
from tests.test_presentation_sync import MANAGER, add_xtream, panel

GROUP_MAP = {
    "US| NETWORK-A ᴴᴰ": "Locals",
    "US| NETWORK-B ᴴᴰ": "Locals",
    "US| SPORT ᴴᴰ": "Sports",
    "US| LEAGUE-ONE PPV": "Sports / League One",
    "US| LEAGUE-ONE REPLAY": "Sports / League One Replays",
    "US| COLLEGE HOOPS PPV": "Sports / College Basketball",
}
# (raw group, channel names); the last group is not in the map.
LINEUP: list[tuple[str, list[str]]] = [
    ("US| NETWORK-A ᴴᴰ", ["Network A East"]),
    ("US| NETWORK-B ᴴᴰ", ["Network B East"]),
    ("US| SPORT ᴴᴰ", ["Sport One", "Sport Two"]),
    ("US| LEAGUE-ONE PPV", ["League One 01", "League One 02", "League One 03"]),
    ("US| LEAGUE-ONE REPLAY", ["League One Replay"]),
    ("US| COLLEGE HOOPS PPV", ["College Hoops 01", "College Hoops 02"]),
    ("US| NEWS ᴴᴰ", ["News One"]),
]
SPORTS = [
    "Sport One",
    "Sport Two",
    "League One 01",
    "League One 02",
    "League One 03",
    "League One Replay",
    "College Hoops 01",
    "College Hoops 02",
]
LEAGUE = ["League One 01", "League One 02", "League One 03"]
ALL = {"language": "all"}


def live(lineup: list[tuple[str, list[str]]]) -> dict[str, Any]:
    categories = [{"category_id": str(n), "category_name": raw} for n, (raw, _) in enumerate(lineup, 1)]
    streams = [
        {"stream_id": n * 100 + i, "name": name, "category_id": str(n)}
        for n, (_, names) in enumerate(lineup, 1)
        for i, name in enumerate(names)
    ]
    return {"get_live_categories": categories, "get_live_streams": streams}


async def add_panel(
    api: Api, lineup: list[tuple[str, list[str]]] = LINEUP, group_map: dict[str, str] = GROUP_MAP, **config: Any
) -> str:
    respx.get(f"{MANAGER}/player_api.php").mock(side_effect=panel(**live(lineup)))
    return await add_xtream(api, group_map=group_map, **config)


async def groups(api: Api, **params: Any) -> list[dict[str, Any]]:
    response = await api.get("/channels/groups", params={**ALL, **params})
    assert response.status_code == 200, response.text
    listed: list[dict[str, Any]] = response.json()
    return listed


def shape(listed: list[dict[str, Any]]) -> list[tuple[str, str | None, str, int]]:
    return [(g["path"], g["parent"], g["name"], g["channel_count"]) for g in listed]


async def names(api: Api, **params: Any) -> list[str]:
    response = await api.get("/channels", params={**ALL, **params})
    assert response.status_code == 200, response.text
    return [c["name"] for c in response.json()["channels"]]


async def by_path(api: Api, **params: Any) -> dict[str, dict[str, Any]]:
    return {g["path"]: g for g in await groups(api, **params)}


@respx.mock
async def test_raw_groups_merge_and_children_are_listed_under_their_parent(api: Api) -> None:
    sid = await add_panel(api)
    listed = await groups(api)
    assert shape(listed) == [
        ("Locals", None, "Locals", 2),  # two raw groups, one row
        ("Sports", None, "Sports", 8),  # its own two channels and its children's six
        ("Sports / League One", "Sports", "League One", 3),
        ("Sports / League One Replays", "Sports", "League One Replays", 1),
        ("Sports / College Basketball", "Sports", "College Basketball", 2),
        ("News", None, "News", 1),
    ]
    assert len({g["id"] for g in listed}) == len(listed)
    assert all(g["source_id"] == sid and not g["hidden"] for g in listed)
    # The group a channel reports is the one it is stored under: the full path.
    channels = (await api.get("/channels", params=ALL)).json()["channels"]
    assert {c["group"] for c in channels if c["name"] in LEAGUE} == {"Sports / League One"}
    # A search narrows every count, the parent's with its children's.
    assert shape(await groups(api, q="League One 0")) == [
        ("Sports", None, "Sports", 3),
        ("Sports / League One", "Sports", "League One", 3),
    ]


@respx.mock
async def test_a_parent_nobody_is_mapped_to_is_still_a_group(api: Api) -> None:
    sid = await add_panel(api, [row for row in LINEUP if row[0] != "US| SPORT ᴴᴰ"])
    listed = await groups(api)
    assert shape(listed) == [
        ("Locals", None, "Locals", 2),
        ("Sports", None, "Sports", 6),
        ("Sports / League One", "Sports", "League One", 3),
        ("Sports / League One Replays", "Sports", "League One Replays", 1),
        ("Sports / College Basketball", "Sports", "College Basketball", 2),
        ("News", None, "News", 1),
    ]
    parent = listed[1]
    # The id is the one the group has when channels are mapped to it directly.
    assert parent["id"] == group_id(sid, "Sports")
    assert await names(api, group=parent["id"]) == SPORTS[2:]
    assert await names(api, group="Sports") == SPORTS[2:]


@respx.mock
async def test_channels_are_filtered_by_parent_child_path_or_bare_child(api: Api) -> None:
    await add_panel(api)
    listed = await by_path(api)
    assert await names(api, group="Sports") == SPORTS
    assert await names(api, group=listed["Sports"]["id"]) == SPORTS
    assert await names(api, group="Sports / League One") == LEAGUE
    assert await names(api, group="Sports /League One") == LEAGUE  # as loosely as the config may write it
    assert await names(api, group=listed["Sports / League One"]["id"]) == LEAGUE
    assert await names(api, group="League One") == LEAGUE
    assert await names(api, group="College Basketball") == ["College Hoops 01", "College Hoops 02"]
    assert await names(api, group="Locals") == ["Network A East", "Network B East"]
    for unknown in ("Nowhere", "Sports / Nowhere", "Nowhere / League One", "League", "sports"):
        assert await names(api, group=unknown) == []


@respx.mock
async def test_a_bare_child_name_must_name_one_group(api: Api) -> None:
    lineup = [*LINEUP, ("US| FILM REPLAY", ["Film Replay"]), ("US| REPLAYS", ["Replay One"])]
    extra = {"US| LEAGUE-ONE REPLAY": "Sports / Replays", "US| FILM REPLAY": "Movies / Replays"}
    await add_panel(api, lineup, {**GROUP_MAP, **extra})
    # Two parents have a child of this name, and a top-level group is called "Replays" outright.
    assert await names(api, group="Replays") == ["Replay One"]
    assert await names(api, group="Sports / Replays") == ["League One Replay"]
    assert await names(api, group="Movies / Replays") == ["Film Replay"]
    await api.patch(f"/channels/groups/{(await by_path(api))['Replays']['id']}", json={"hidden": True})
    assert await names(api, group="Replays") == ["Replay One"]  # an explicit group query still shows a hidden group

    async with api.c.db.session() as session:
        top = (await session.execute(select(ChannelGroup).where(ChannelGroup.name == "Replays"))).scalar_one()
        channel = (await session.execute(select(Channel).where(Channel.group_id == top.id))).scalar_one()
        await session.delete(channel)
        await session.delete(top)
        await session.commit()
    assert await names(api, group="Replays") == []  # which of the two is not ours to guess
    assert await names(api, group="League One") == LEAGUE


@respx.mock
async def test_a_hidden_child_leaves_its_parent_and_a_hidden_parent_takes_its_children(api: Api) -> None:
    await add_panel(api)
    listed = await by_path(api)
    replays, sports = listed["Sports / League One Replays"]["id"], listed["Sports"]["id"]

    hidden = await api.patch(f"/channels/groups/{replays}", json={"hidden": True})
    assert hidden.status_code == 200 and hidden.json()["hidden"] and hidden.json()["parent"] == "Sports"
    assert shape(await groups(api)) == [
        ("Locals", None, "Locals", 2),
        ("Sports", None, "Sports", 7),
        ("Sports / League One", "Sports", "League One", 3),
        ("Sports / College Basketball", "Sports", "College Basketball", 2),
        ("News", None, "News", 1),
    ]
    assert "League One Replay" not in await names(api)
    assert await names(api, group="Sports") == [name for name in SPORTS if name != "League One Replay"]
    assert await names(api, group=sports) == [name for name in SPORTS if name != "League One Replay"]
    # Asked for by name, a hidden group is still shown.
    assert await names(api, group="Sports / League One Replays") == ["League One Replay"]
    settings = await by_path(api, include_hidden=True)
    assert settings["Sports / League One Replays"]["hidden"] and not settings["Sports"]["hidden"]
    assert (settings["Sports"]["channel_count"], settings["Sports / League One Replays"]["channel_count"]) == (8, 1)

    assert (await api.patch(f"/channels/groups/{sports}", json={"hidden": True})).json()["hidden"]
    assert shape(await groups(api)) == [("Locals", None, "Locals", 2), ("News", None, "News", 1)]
    assert await names(api) == ["Network A East", "Network B East", "News One"]
    assert [x["count"] for x in (await api.get("/channels/languages")).json()] == [3]
    settings = await by_path(api, include_hidden=True)
    assert all(settings[path]["hidden"] for path in settings if path.startswith("Sports"))
    assert not settings["Locals"]["hidden"] and settings["Sports"]["channel_count"] == 8
    # The parent asked for by name comes with the children that are not hidden themselves.
    assert await names(api, group="Sports") == [name for name in SPORTS if name != "League One Replay"]

    # Showing the parent again does not show the child that was hidden on its own.
    assert not (await api.patch(f"/channels/groups/{sports}", json={"hidden": False})).json()["hidden"]
    assert [path for path, *_ in shape(await groups(api))] == [
        "Locals",
        "Sports",
        "Sports / League One",
        "Sports / College Basketball",
        "News",
    ]
    settings = await by_path(api, include_hidden=True)
    assert settings["Sports / League One Replays"]["hidden"] and not settings["Sports / League One"]["hidden"]


@respx.mock
async def test_a_parent_is_patched_like_any_group_and_keeps_its_settings_across_a_sync(api: Api) -> None:
    # Nothing is mapped to the parent itself here: its row exists only because it has children.
    sid = await add_panel(api, [row for row in LINEUP if row[0] != "US| SPORT ᴴᴰ"])
    listed = await by_path(api)
    sports, league = listed["Sports"]["id"], listed["Sports / League One"]["id"]

    assert (await api.patch(f"/channels/groups/{league}", json={"language_override": "fr"})).status_code == 200
    patched = await api.patch(f"/channels/groups/{sports}", json={"language_override": "de", "sort": 99})
    assert patched.status_code == 200, patched.text
    assert (patched.json()["id"], patched.json()["path"], patched.json()["language_override"]) == (
        sports,
        "Sports",
        "deu",
    )
    channels = {c["name"]: c["language"] for c in (await api.get("/channels", params=ALL)).json()["channels"]}
    # The parent's language reaches the children that set none.
    assert {channels[name] for name in LEAGUE} == {"fra"}
    assert {channels[name] for name in ("League One Replay", "College Hoops 01", "College Hoops 02")} == {"deu"}
    assert channels["News One"] == "eng"
    assert [path for path, *_ in shape(await groups(api))][-4:] == [
        "Sports",  # sort 99: after News now, and its children with it
        "Sports / League One",
        "Sports / League One Replays",
        "Sports / College Basketball",
    ]

    assert (await api.patch(f"/channels/groups/{sports}", json={"hidden": True})).json()["hidden"]
    assert await api.c.sync.sync_source(sid)
    kept = (await by_path(api, include_hidden=True))["Sports"]
    assert (kept["id"], kept["hidden"], kept["language_override"]) == (sports, True, "deu")
    assert await names(api) == ["Network A East", "Network B East", "News One"]

    cleared = await api.patch(f"/channels/groups/{sports}", json={"language_override": None, "hidden": False})
    assert cleared.json()["language_override"] is None and not cleared.json()["hidden"]
    channels = {c["name"]: c["language"] for c in (await api.get("/channels", params=ALL)).json()["channels"]}
    assert {channels[name] for name in LEAGUE} == {"fra"} and channels["College Hoops 01"] == "eng"
    assert (await api.patch("/channels/groups/nothing", json={"hidden": True})).status_code == 404


async def test_groups_stored_before_parents_had_rows_get_one_when_it_is_needed(api: Api) -> None:
    """A playlist's own ``Parent / Child`` names nest too, and a catalog synced earlier has no parent rows."""
    async with api.c.db.session() as session:
        session.add_all([Source(id=sid, name=sid, type="m3u", config_enc="unused") for sid in ("one", "two")])
        await session.flush()
        for sid in ("one", "two"):
            for sort, path in enumerate(["News", "Sports / League One", "Sports / College Basketball"], 1):
                gid = group_id(sid, path)
                session.add(ChannelGroup(id=gid, source_id=sid, name=path, sort=sort, sync_gen=4))
                session.add(
                    Channel(source_id=sid, ext_id=str(sort), name=f"{sid} {path}", group_id=gid, group_name=path)
                )
        await session.commit()

    listed = await groups(api, source_id="one")
    assert shape(listed) == [
        ("News", None, "News", 1),
        ("Sports", None, "Sports", 2),
        ("Sports / League One", "Sports", "League One", 1),
        ("Sports / College Basketball", "Sports", "College Basketball", 1),
    ]
    sports = listed[1]["id"]
    assert sports == group_id("one", "Sports")
    # The same paths in a second source stay that source's own groups, as same-named groups always have.
    assert sorted((g["source_id"], g["path"]) for g in await groups(api) if g["parent"] is None) == [
        ("one", "News"),
        ("one", "Sports"),
        ("two", "News"),
        ("two", "Sports"),
    ]
    assert await names(api, group=sports) == ["one Sports / League One", "one Sports / College Basketball"]
    assert len(await names(api, group="Sports")) == 4 and len(await names(api, group="League One")) == 2
    assert await names(api, group="Sports", source_id="two") == [
        "two Sports / League One",
        "two Sports / College Basketball",
    ]

    hidden = await api.patch(f"/channels/groups/{sports}", json={"hidden": True})
    assert hidden.status_code == 200 and (hidden.json()["id"], hidden.json()["hidden"]) == (sports, True)
    assert shape(await groups(api, source_id="one")) == [("News", None, "News", 1)]
    assert len(await names(api, group="Sports")) == 4  # asked for by name
    assert sorted(await names(api)) == [
        "one News",
        "two News",
        "two Sports / College Basketball",
        "two Sports / League One",
    ]
    async with api.c.db.session() as session:
        row = await session.get(ChannelGroup, sports)
        assert row is not None and (row.name, row.sort, row.sync_gen, row.hidden) == ("Sports", 2, 4, True)
