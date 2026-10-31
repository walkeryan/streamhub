from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import insert, update

from app.infra.models import Channel, ChannelGroup, Programme, Source, Title
from tests.conftest import Api


async def seed_catalog(api: Api, title_count: int = 0, channel_count: int = 0) -> None:
    async with api.c.db.session() as s:
        s.add_all(
            [
                Source(id="catalog", name="Catalog", type="xtream", config_enc="unused"),
                Source(id="disabled", name="Disabled", type="xtream", enabled=False, config_enc="unused"),
            ]
        )
        await s.flush()
        s.add_all(
            [
                ChannelGroup(id=code, source_id="catalog", name=group)
                for code, group in [("eng", "UK| News"), ("fra", "FR| News"), ("und", "Uncategorized")]
            ]
        )
        await s.flush()
        if title_count:
            await s.execute(
                insert(Title),
                [
                    dict(
                        source_id="catalog",
                        ext_id=str(i),
                        kind="vod",
                        title=f"Film {i:06}",
                        norm_title=f"film {i:06}",
                        group_name=["UK| Films", "FR| Films", None][i % 3],
                    )
                    for i in range(title_count)
                ],
            )
        if channel_count:
            await s.execute(
                insert(Channel),
                [
                    dict(
                        source_id="catalog",
                        ext_id=str(i),
                        name=f"News {i:06}",
                        norm_name=f"news {i:06}",
                        number=i + 1,
                        group_id=["eng", "fra", "und"][i % 3],
                        group_name=["UK| News", "FR| News", "Uncategorized"][i % 3],
                        language=["eng", "fra", "und"][i % 3],
                        epg_id=f"epg-{i}",
                    )
                    for i in range(channel_count)
                ],
            )
        s.add(
            Title(
                source_id="disabled",
                ext_id="hidden",
                kind="vod",
                title="Film hidden",
                norm_title="film hidden",
                group_name="UK| Films",
            )
        )
        await s.commit()
    await api.patch(f"/profiles/{api.profile_id}", json={"languages": ["eng"]})


async def test_large_catalog_language_filter_and_stable_paging(api: Api) -> None:
    await seed_catalog(api, title_count=12000)
    first = (await api.get("/browse/movies", params={"sort": "title"})).json()
    second = (await api.get("/browse/movies", params={"sort": "title", "page": 2})).json()
    assert first["total"] == 4000 and len(first["tiles"]) == 60 and first["has_more"]
    assert not ({x["id"] for x in first["tiles"]} & {x["id"] for x in second["tiles"]})
    assert all(int(x["id"].split(":")[-1]) % 3 == 0 for x in first["tiles"])
    assert (await api.get("/browse/movies", params={"language": "all"})).json()["total"] == 12000
    unknown = (await api.get("/browse/movies", params={"language": "und"})).json()
    assert unknown["total"] == 4000 and all(int(x["id"].split(":")[-1]) % 3 == 2 for x in unknown["tiles"])
    final = (await api.get("/browse/movies", params={"language": "all", "page": 200})).json()
    assert len(final["tiles"]) == 60 and not final["has_more"]
    assert (await api.get("/browse/movies/categories")).json() == [{"name": "UK| Films", "count": 4000}]
    assert {v["code"]: v["count"] for v in (await api.get("/browse/movies/languages")).json()} == {
        "eng": 4000,
        "fra": 4000,
        "und": 4000,
    }
    assert (await api.get("/browse/movies", params={"q": "Film 000003"})).json()["total"] == 1
    assert (await api.get("/browse/movies", params={"category": "FR| Films", "language": "all"})).json()[
        "total"
    ] == 4000


async def test_scoped_channel_groups_search_and_paging(api: Api) -> None:
    await seed_catalog(api, title_count=12, channel_count=3000)
    response = (await api.get("/channels", params={"limit": 40, "offset": 40})).json()
    assert len(response["channels"]) == 40 and response["total"] == 1000 and response["offset"] == 40
    assert all(x["language"] == "eng" for x in response["channels"])
    assert [(x["name"], x["channel_count"]) for x in (await api.get("/channels/groups")).json()] == [("UK| News", 1000)]
    assert len((await api.get("/channels/groups", params={"language": "all"})).json()) == 3
    search = (await api.get("/search", params={"q": "News"})).json()
    assert search["channels"] and all(x["language"] == "eng" for x in search["channels"])
    assert {
        x["language"] for x in (await api.get("/search", params={"q": "News", "language": "all"})).json()["channels"]
    } == {"eng", "fra", "und"}
    assert len((await api.get("/search", params={"q": "Film"})).json()["play_here"]) == 4
    assert len((await api.get("/search", params={"q": "Film", "language": "all"})).json()["play_here"]) == 12
    await api.put("/favorites", json={"kind": "channel", "id": response["channels"][0]["id"]})
    assert (await api.get("/channels/groups", params={"favorites": True})).json()[0]["channel_count"] == 1
    assert (await api.get("/channels", params={"limit": 501})).status_code == 422
    assert (await api.get("/channels", params={"offset": -1})).status_code == 422


async def test_guide_filter_requires_matching_source_and_future_programme(api: Api) -> None:
    await seed_catalog(api, channel_count=6)
    now = datetime.now(UTC)
    async with api.c.db.session() as s:
        s.add_all(
            [
                Programme(
                    feed="catalog",
                    epg_channel="epg-0",
                    title="On now",
                    start=now - timedelta(minutes=10),
                    stop=now + timedelta(minutes=20),
                ),
                Programme(
                    feed="disabled",
                    epg_channel="epg-3",
                    title="Wrong feed",
                    start=now - timedelta(minutes=10),
                    stop=now + timedelta(minutes=20),
                ),
            ]
        )
        await s.commit()
    response = (await api.get("/channels", params={"has_epg": True, "limit": 40})).json()
    assert response["total"] == 1 and response["channels"][0]["ext_id"] == "0"


async def test_home_recent_respects_language_but_saved_items_remain(api: Api) -> None:
    await seed_catalog(api, title_count=12)
    async with api.c.db.session() as s:
        await s.execute(update(Title).values(added_at=datetime.now(UTC)))
        await s.commit()
    await api.put("/favorites", json={"kind": "vod", "id": "lib:catalog:1"})
    await api.put("/watchlist", json={"id": "lib:catalog:2"})
    home = (await api.get("/home")).json()
    rows = {row["id"]: row["tiles"] for row in home["rows"]}
    assert len(rows["recently_added"]) == 4
    assert all(int(t["id"].split(":")[-1]) % 3 == 0 for t in rows["recently_added"])
    assert rows["favorites"][0]["id"] == "lib:catalog:1"
    assert rows["watchlist"][0]["id"] == "lib:catalog:2"


async def test_default_playback_context_matches_list_without_serialization(api: Api) -> None:
    await seed_catalog(api, channel_count=3000)
    listed = (await api.get("/channels")).json()
    channel = await api.c.channels.channel_by_hub_id(listed["channels"][40]["id"])
    with patch.object(api.c.channels, "to_api", new=AsyncMock(side_effect=AssertionError("No serialization"))):
        context = await api.c.channels.context_for_channel(api.profile_id, channel.id)
    assert context == {"list_id": listed["list_id"], "index": 40, "count": 1000}


async def test_hidden_groups_remain_manageable_in_settings(api: Api) -> None:
    await seed_catalog(api, channel_count=6)
    await api.patch("/channels/groups/eng", json={"hidden": True})
    assert (await api.get("/channels/groups")).json() == []
    settings = (await api.get("/channels/groups", params={"language": "all", "include_hidden": True})).json()
    english = next(g for g in settings if g["id"] == "eng")
    assert english["hidden"] and english["channel_count"] == 2
    await api.patch("/channels/groups/eng", json={"hidden": False})
    assert (await api.get("/channels/groups")).json()[0]["id"] == "eng"


async def test_on_now_uses_only_profile_history_and_current_eligible_channels(api: Api) -> None:
    from app.infra.models import Profile, Progress

    await seed_catalog(api, channel_count=12)
    now = datetime.now(UTC)
    async with api.c.db.session() as session:
        session.add_all(
            [Profile(id="second", name="Second", languages=["eng"]), Profile(id="cold", name="Cold", languages=["eng"])]
        )
        await session.flush()
        session.add_all(
            [
                Programme(
                    feed="catalog",
                    epg_channel=f"epg-{i}",
                    title=f"Show {i}",
                    start=now - timedelta(minutes=10),
                    stop=now + timedelta(minutes=20),
                )
                for i in range(9)
            ]
        )
        session.add_all(
            [
                Progress(
                    profile_id=api.profile_id,
                    ref_id="ch:catalog:6",
                    kind="channel",
                    updated_at=now - timedelta(minutes=2),
                ),
                Progress(
                    profile_id=api.profile_id, ref_id="ch:catalog:1", kind="channel", updated_at=now
                ),  # foreign language
                Progress(
                    profile_id=api.profile_id, ref_id="ch:catalog:9", kind="channel", updated_at=now
                ),  # no schedule
                Progress(profile_id="second", ref_id="ch:catalog:3", kind="channel", updated_at=now),
            ]
        )
        await session.commit()
    pairs, personalized = await api.c.channels.on_now(api.profile_id)
    assert personalized and [ch.ext_id for ch, _ in pairs] == ["6", "0", "3"]
    pairs, personalized = await api.c.channels.on_now("second")
    assert personalized and [ch.ext_id for ch, _ in pairs] == ["3", "0", "6"]
    pairs, personalized = await api.c.channels.on_now("cold")
    assert not personalized and [ch.ext_id for ch, _ in pairs] == ["0", "3", "6"]
    home = await api.c.home.home(api.profile_id)
    assert next(row for row in home["rows"] if row["id"] == "on_now")["title"] == "On now · Recent channels first"
    cold = await api.c.home.home("cold")
    assert next(row for row in cold["rows"] if row["id"] == "on_now")["title"] == "On now · Explore live TV"
    await api.patch("/channels/groups/eng", json={"hidden": True})
    assert await api.c.channels.on_now(api.profile_id) == ([], False)


async def test_no_epg_cold_start_home_explores_eligible_channels(api: Api) -> None:
    await seed_catalog(api, channel_count=90)
    home = await api.c.home.home(api.profile_id)
    row = next(row for row in home["rows"] if row["id"] == "on_now")
    assert row["title"] == "Explore live TV" and len(row["tiles"]) == 20
    assert all(int(t["id"].split(":")[-1]) % 3 == 0 for t in row["tiles"])
