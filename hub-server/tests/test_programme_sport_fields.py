"""``is_sport`` and ``teams`` on programme payloads: the guide, a channel's now/next, recordings."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.infra.models import Programme
from app.infra.settings import AppSettings
from app.services.sports import matchup_teams
from tests.conftest import Api
from tests.test_api_recordings import channel, guide, hub, soon
from tests.test_epg_integrity import add_source


@pytest.fixture
def settings(settings: AppSettings, tmp_path: Path) -> AppSettings:
    root = tmp_path / "recordings"
    root.mkdir()
    return settings.model_copy(update={"recordings_dir": str(root)})


@pytest.mark.parametrize(
    ("title", "category", "expected"),
    [
        ("NBA: Northside vs Lakeview", None, ["Northside", "Lakeview"]),
        ("Northside vs. Lakeview", "Basketball", ["Northside", "Lakeview"]),
        ("Eastfield v Westbrook", "Soccer", ["Eastfield", "Westbrook"]),
        ("Rugby - Mill Lane versus North End", None, ["Mill Lane", "North End"]),
        ("NFL Football: Rivermen at Highlanders", None, ["Rivermen", "Highlanders"]),
        ("Harbour Town @ Red Rock", "Baseball", ["Harbour Town", "Red Rock"]),
        ("MLB Baseball: Harbour Town at Red Rock (Live)", None, ["Harbour Town", "Red Rock"]),
        ("UEFA Champions League: Oldport vs Newbridge - Matchday 5", None, ["Oldport", "Newbridge"]),
        ("LIVE: Tennis | Rivers vs Stone", None, ["Rivers", "Stone"]),
        # Not a matchup, or not a sports listing at all: nothing is guessed.
        ("Golf Central", "Golf", []),
        ("PGA Tour: Live at the Links", None, []),
        ("Tennis at night", "Sports", []),
        ("Football Tonight", "Sports", []),
        ("Harbour vs. Lighthouse", "Drama", []),
        ("Live at the Harbour", "Comedy", []),
        ("Boxing: Red vs Blue vs Green", None, []),
        ("Hockey: vs Rivals", None, []),
    ],
)
def test_teams_come_only_from_an_explicit_matchup_in_a_sports_listing(
    title: str, category: str | None, expected: list[str]
) -> None:
    assert matchup_teams(title, category) == expected


async def test_guide_now_next_and_recordings_carry_the_sport_fields(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    async with api.c.db.session() as s:
        for title, category, hours in [
            ("NBA: Northside vs Lakeview", None, -0.5),
            ("Evening News", "News", 0.5),
            ("Highlights", "Tennis", 1.5),
        ]:
            start = soon(hours)
            s.add(
                Programme(
                    feed=bbc.source_id,
                    epg_channel=bbc.epg_id or "",
                    title=title,
                    category=category,
                    start=start,
                    stop=start + timedelta(hours=1),
                )
            )
        await s.commit()
        game = (await s.execute(select(Programme).where(Programme.title.startswith("NBA")))).scalar_one()

    r = await api.get("/epg", params={"channel_ids": hub(bbc), "from": soon(-1).isoformat(), "to": soon(4).isoformat()})
    assert r.status_code == 200, r.text
    assert [(p["title"], p["is_sport"], p["teams"]) for p in r.json()[hub(bbc)]] == [
        ("NBA: Northside vs Lakeview", True, ["Northside", "Lakeview"]),
        ("Evening News", False, []),
        ("Highlights", True, []),
    ]
    listed = (await api.get("/channels", params={"language": "all"})).json()["channels"]
    row = next(c for c in listed if c["id"] == hub(bbc))
    assert (row["now"]["is_sport"], row["now"]["teams"]) == (True, ["Northside", "Lakeview"])
    assert (row["next"]["is_sport"], row["next"]["teams"]) == (False, [])

    made = await api.post("/recordings", json={"programme_id": f"prog:{game.id}"})
    assert made.status_code == 201, made.text
    assert (made.json()["is_sport"], made.json()["teams"]) == (True, ["Northside", "Lakeview"])
    news = next(p for p in await guide(api, bbc) if p["title"] == "Evening News")
    plain = (await api.post("/recordings", json={"programme_id": news["id"]})).json()
    assert (plain["is_sport"], plain["teams"]) == (False, [])
    assert [x["teams"] for x in (await api.get("/recordings")).json()["recordings"]] == [["Northside", "Lakeview"], []]
