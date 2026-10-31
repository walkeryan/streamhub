from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.infra.models import Channel, ChannelGroup, Profile, Programme, Source
from app.services.sports import classify_sport, sports_on_now
from tests.conftest import Api
from tests.test_epg_integrity import add_source

NOW = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("title", "category", "expected"),
    [
        ("NBA: Home vs Away", None, "Basketball"),
        ("Evening programme", "Sports", "Sports"),
        ("Highlights", "Tennis", "Tennis"),
        ("Football review", None, "Football"),
        ("Daily news", None, None),
        ("Transport report", None, None),
        ("College Football: Replay", None, "American football"),
    ],
)
def test_only_explicit_metadata_classifies_sports(title, category, expected):
    assert classify_sport(title, category) == expected


async def test_current_window_source_isolation_languages_and_paging(api: Api):
    first = await add_source(api, "First feed")
    second = await add_source(api, "Second feed")
    async with api.c.db.session() as session:
        profile = await session.get(Profile, api.profile_id)
        profile.languages = ["eng"]
        for source, title in [(first, "NBA: Home vs Away"), (second, "Tennis final")]:
            session.add(
                Programme(
                    feed=source["id"], epg_channel="bbc1.uk", start=NOW, stop=NOW + timedelta(hours=1), title=title
                )
            )
        # Same EPG id from an unrelated feed must never attach to either channel.
        session.add(
            Programme(
                feed="unrelated",
                epg_channel="bbc1.uk",
                start=NOW,
                stop=NOW + timedelta(hours=1),
                title="NFL: Wrong feed",
            )
        )
        # Exactly ended and future programmes are excluded; current boundary is inclusive.
        for start, stop, title in [
            (NOW - timedelta(hours=1), NOW, "NFL: Ended"),
            (NOW + timedelta(seconds=1), NOW + timedelta(hours=1), "NFL: Future"),
        ]:
            session.add(Programme(feed=first["id"], epg_channel="bbc1.uk", start=start, stop=stop, title=title))
        session.add(
            Programme(
                feed=first["id"], epg_channel="tf1.fr", start=NOW, stop=NOW + timedelta(hours=1), title="Cricket live"
            )
        )
        await session.commit()
    data = await sports_on_now(api.c.channels, api.profile_id, now=NOW, limit=1)
    assert data["total"] == 2 and len(data["items"]) == 1
    assert data["coverage"] == {"eligible_channels": 2, "channels_with_current_schedule": 2}
    other = await sports_on_now(api.c.channels, api.profile_id, now=NOW, limit=1, offset=1)
    items = data["items"] + other["items"]
    assert {(x["source_name"], x["title"]) for x in items} == {
        ("First feed", "NBA: Home vs Away"),
        ("Second feed", "Tennis final"),
    }
    all_languages = await sports_on_now(api.c.channels, api.profile_id, now=NOW, language="all")
    assert all_languages["total"] == 3
    filtered = await sports_on_now(api.c.channels, api.profile_id, now=NOW, sport="Basketball", q="HOME")
    assert filtered["total"] == 1 and filtered["items"][0]["source_name"] == "First feed"


async def test_no_epg_does_not_invent_sports_and_hidden_disabled_sources_excluded(api: Api):
    first = await add_source(api, "Sports named source")
    empty = await sports_on_now(api.c.channels, api.profile_id, now=NOW, language="all")
    assert empty["total"] == 0 and empty["coverage"]["channels_with_current_schedule"] == 0
    async with api.c.db.session() as session:
        channel = (
            await session.execute(select(Channel).where(Channel.source_id == first["id"], Channel.epg_id == "bbc1.uk"))
        ).scalar_one()
        group = await session.get(ChannelGroup, channel.group_id)
        group.hidden = True
        session.add(
            Programme(feed=first["id"], epg_channel="bbc1.uk", start=NOW, stop=NOW + timedelta(hours=1), title="Sports")
        )
        await session.commit()
    assert (await sports_on_now(api.c.channels, api.profile_id, now=NOW, language="all"))["total"] == 0
    async with api.c.db.session() as session:
        group = await session.get(ChannelGroup, channel.group_id)
        group.hidden = False
        source = await session.get(Source, first["id"])
        source.enabled = False
        await session.commit()
    data = await sports_on_now(api.c.channels, api.profile_id, now=NOW, language="all")
    assert data["coverage"]["eligible_channels"] == 0 and data["total"] == 0


async def test_api_large_current_catalog_is_paged_and_uses_frozen_clock(api: Api, monkeypatch):
    source = await add_source(api)
    async with api.c.db.session() as session:
        original = (
            await session.execute(select(Channel).where(Channel.source_id == source["id"], Channel.epg_id == "bbc1.uk"))
        ).scalar_one()
        session.add_all(
            [
                Channel(
                    source_id=source["id"],
                    ext_id=f"extra-{i}",
                    name=f"Feed {i}",
                    number=i + 100,
                    group_id=original.group_id,
                    group_name=original.group_name,
                    language="eng",
                    epg_id="bbc1.uk",
                )
                for i in range(5000)
            ]
        )
        session.add(
            Programme(
                feed=source["id"],
                epg_channel="bbc1.uk",
                start=NOW - timedelta(minutes=5),
                stop=NOW + timedelta(minutes=5),
                title="Basketball live",
            )
        )
        await session.commit()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr("app.services.sports.datetime", FrozenDateTime)
    response = await api.get("/sports", params={"language": "eng", "offset": 40, "limit": 40})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 5001 and len(data["items"]) == 40
    assert data["offset"] == 40 and data["sports"] == [{"name": "Basketball", "count": 5001}]
    assert data["coverage"]["channels_with_current_schedule"] == 5001
    assert data["items"][0]["channel_number"] == 139
    assert (await api.get("/sports", params={"limit": 1001})).status_code == 422
