from datetime import UTC, datetime, timedelta

import pytest

from app.infra.models import Programme
from app.services.sports import classify_sport, sports_events
from tests.test_epg_integrity import add_source

NOW = datetime(2026, 11, 1, 14, tzinfo=UTC)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("TATA Indian Premier League 2026: M28: Kolkata Knight Riders vs Rajasthan Royals", "Cricket"),
        ("Barcelona vs Deportivo Alaves - Spanish LaLiga 2026/2027 - Week 11", "Soccer"),
        ("INAC Kobe Leonessa (JPN) vs Bam Khatoon FC (IRN) - AFC Women’s Champions League", "Soccer"),
        ("Premier League review", None),
        ("Pro20 Cup 26: Super8/A3 v B2", None),
    ],
)
def test_actual_shaped_sports_are_classified_conservatively(title, expected):
    assert classify_sport(title, None) == expected


async def test_event_grouping_status_and_source_isolation(api):
    sources = [await add_source(api, "Feed one"), await add_source(api, "Feed two")]
    async with api.c.db.session() as session:
        for src in sources:
            for title, begin, end in [
                ("NBA: Home vs Away", NOW - timedelta(hours=1), NOW + timedelta(hours=1)),
                ("NBA studio", NOW - timedelta(hours=1), NOW + timedelta(hours=1)),
                ("Tennis Replay", NOW + timedelta(hours=1), NOW + timedelta(hours=2)),
            ]:
                session.add(Programme(feed=src["id"], epg_channel="bbc1.uk", title=title, start=begin, stop=end))
        session.add(
            Programme(
                feed="unrelated",
                epg_channel="bbc1.uk",
                title="NFL Wrong feed",
                start=NOW,
                stop=NOW + timedelta(hours=1),
            )
        )
        # A long overnight slot is on now, not evidence that an actual match is live.
        session.add(
            Programme(
                feed=sources[0]["id"],
                epg_channel="bbc1.uk",
                title="Barcelona vs Deportivo Alaves - Spanish LaLiga",
                start=NOW - timedelta(hours=15),
                stop=NOW + timedelta(hours=2),
            )
        )
        session.add(
            Programme(
                feed=sources[0]["id"],
                epg_channel="bbc1.uk",
                title="Live: Golf",
                start=NOW,
                stop=NOW + timedelta(hours=1),
            )
        )
        await session.commit()
    data = await sports_events(api.c.channels, api.profile_id, date="2026-11-01", now=NOW, language="all")
    matchup = next(e for e in data["events"] if e["title"] == "NBA: Home vs Away")
    assert len(matchup["feeds"]) == 2
    assert len({f["source_id"] for f in matchup["feeds"]}) == 2
    assert matchup["status"] == "current" and not matchup["live_evidence"]
    assert all(not f["language_verified"] for f in matchup["feeds"])
    assert len([e for e in data["events"] if e["title"] == "NBA studio"]) == 2
    overnight = next(e for e in data["events"] if e["sport"] == "Soccer")
    assert overnight["status"] == "current" and not overnight["live_evidence"]
    assert next(e for e in data["events"] if e["title"] == "Live: Golf")["live_evidence"]
    assert not any("Wrong" in e["title"] for e in data["events"])
    replays = await sports_events(api.c.channels, api.profile_id, now=NOW, status="replay", language="all")
    assert replays["total"] == 2 and all(e["replay"] and e["status"] == "upcoming" for e in replays["events"])
    current = await sports_events(
        api.c.channels, api.profile_id, now=NOW, status="current", language="all", limit=1, offset=1
    )
    assert len(current["events"]) == 1 and current["total"] == 5
    filtered = await sports_events(api.c.channels, api.profile_id, now=NOW, sport="Soccer", language="all")
    assert filtered["total"] == 1


async def test_endpoint_validation_empty_and_no_auth(api):
    result = await api.get("/sports/events", params={"date": "2026-11-01"})
    assert result.status_code == 200 and result.json()["events"] == []
    assert result.json()["timezone"] == "America/New_York"
    assert not result.json()["truncated"]
    assert (await api.get("/sports/events", params={"date": "bad"})).status_code == 400
    assert (await api.get("/sports/events", params={"timezone": "invalid"})).status_code == 400
    assert (await api.get("/sports/events", params={"status": "live"})).status_code == 422
    assert (await api.client.get("/api/v1/sports/events")).status_code == 401


async def test_candidate_cap_is_explicit_and_filters_run_before_cap(api):
    from sqlalchemy import insert

    source = await add_source(api)
    async with api.c.db.session() as session:
        await session.execute(
            insert(Programme),
            [
                dict(
                    feed=source["id"],
                    epg_channel="bbc1.uk",
                    title=f"NBA studio {i:05}",
                    start=NOW,
                    stop=NOW + timedelta(hours=1),
                )
                for i in range(5001)
            ],
        )
        session.add(
            Programme(
                feed=source["id"],
                epg_channel="bbc1.uk",
                title="Tennis Replay",
                start=NOW + timedelta(hours=1),
                stop=NOW + timedelta(hours=2),
            )
        )
        await session.commit()
    bounded = await sports_events(api.c.channels, api.profile_id, now=NOW, language="all", limit=3)
    assert bounded["truncated"] and bounded["total"] == 5000 and len(bounded["events"]) == 3
    filtered = await sports_events(api.c.channels, api.profile_id, now=NOW, language="all", sport="Tennis")
    assert not filtered["truncated"] and filtered["total"] == 1
    replay = await sports_events(api.c.channels, api.profile_id, now=NOW, language="all", status="replay")
    assert not replay["truncated"] and replay["total"] == 1
