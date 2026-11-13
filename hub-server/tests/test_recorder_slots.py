"""Recording the game of an event slot, and rules that must not be fed by placeholder programmes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.infra.settings import AppSettings
from tests.conftest import Api
from tests.test_channel_presentation import add_lineup, add_placeholders, add_programme, listed, stamp

EVENTS = "XX| VANTIX+ EVENTS"


@pytest.fixture
def settings(settings: AppSettings, tmp_path: Path) -> AppSettings:
    root = tmp_path / "recordings"
    root.mkdir()
    return settings.model_copy(update={"recordings_dir": str(root)})


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def lineup(at: datetime) -> list[tuple[str, str, str]]:
    return [
        ("vantix1", EVENTS, f"(XX) (VANTIX+ 001) | Rugby: Oakdale v Riverton {stamp(at + timedelta(hours=2))}"),
        (
            "vantix2",
            EVENTS,
            f"(XX) (VANTIX+ 002) | Soccer (M): Oakdale at Port Linden {stamp(at - timedelta(days=42))}",
        ),
        ("vantix3", EVENTS, f"XX (VANTIX+ 003) | Lacrosse: Millbrook at Lakeside {stamp(at + timedelta(hours=3))}"),
        ("combat", "XX| SPORTS", "COMBAT HD"),
    ]


async def test_the_game_of_a_coming_slot_is_recorded_under_its_clean_names(api: Api) -> None:
    at = now()
    source = await add_lineup(api, lineup(at))
    await add_placeholders(api, source, at)
    channel = (await listed(api))["vantix1"]
    game = channel["next"]
    assert game["recording_state"] is None

    made = await api.post(
        "/recordings", json={"programme_id": game["id"], "channel_id": channel["id"], "start": game["start"]}
    )
    assert made.status_code == 201, made.text
    recording = made.json()
    assert (recording["title"], recording["channel_name"]) == ("Oakdale v Riverton", "VANTIX+ 001")
    assert (recording["is_sport"], recording["teams"], recording["state"]) == (
        True,
        ["Oakdale", "Riverton"],
        "scheduled",
    )
    assert datetime.fromisoformat(recording["starts_at"]) == at + timedelta(hours=2)
    assert datetime.fromisoformat(recording["ends_at"]) == at + timedelta(hours=2, minutes=210)
    assert recording["channel_id"] == channel["id"]

    # The guide payload finds it again by channel and start, like any other programme.
    assert (await listed(api))["vantix1"]["next"]["recording_state"] == "scheduled"
    again = await api.post("/recordings", json={"programme_id": game["id"]})
    assert again.status_code in (200, 201) and again.json()["id"] == recording["id"]

    # A sport the classifier has no word for is still a game.
    lacrosse = (await listed(api))["vantix3"]
    other = await api.post("/recordings", json={"programme_id": lacrosse["next"]["id"]})
    assert other.status_code == 201, other.text
    assert (other.json()["title"], other.json()["is_sport"]) == ("Millbrook at Lakeside", True)


async def test_a_slot_id_is_good_only_for_the_game_its_channel_still_announces(api: Api) -> None:
    at = now()
    await add_lineup(api, lineup(at))
    channels = await listed(api)
    game = channels["vantix1"]["next"]
    moved = (datetime.fromisoformat(game["start"]) + timedelta(hours=1)).isoformat()
    stale = await api.post(
        "/recordings", json={"programme_id": game["id"], "channel_id": channels["vantix1"]["id"], "start": moved}
    )
    assert stale.status_code == 409 and stale.json()["code"] == "guide_changed"
    renamed = game["id"].rsplit("-", 1)[0] + "-1700000000"
    assert (await api.post("/recordings", json={"programme_id": renamed})).status_code == 409
    # A name left over from an old game announces nothing, and neither does a plain network.
    for epg in ("vantix2", "combat"):
        row = channels[epg]["id"]
        pk = game["id"].removeprefix("prog:slot-").split("-")[0]
        gone = await api.post("/recordings", json={"programme_id": f"prog:slot-{pk}-0", "channel_id": row})
        assert gone.status_code == 404, epg
    assert (await api.post("/recordings", json={"programme_id": "prog:slot-x-y"})).status_code == 404
    assert (await api.get("/recordings")).json()["recordings"] == []


async def test_a_team_rule_records_the_coming_game_and_never_a_placeholder_block(api: Api) -> None:
    at = now()
    source = await add_lineup(api, lineup(at))
    # Each channel "shows" its own name for hours ahead: the old Oakdale name included.
    await add_placeholders(api, source, at, skip=("combat",))
    await add_programme(api, source, "combat", "Boxing: Oakdale Open", at + timedelta(hours=9), 2)
    rule = await api.post("/recording-rules", json={"kind": "team", "match": "Oakdale"})
    assert rule.status_code == 201, rule.text
    await api.c.recorder.apply_rules()
    recordings = (await api.get("/recordings")).json()["recordings"]
    assert sorted((r["title"], r["channel_name"]) for r in recordings) == [
        ("Boxing: Oakdale Open", "COMBAT HD"),
        ("Oakdale v Riverton", "VANTIX+ 001"),
    ]
    slot = next(r for r in recordings if r["channel_name"] == "VANTIX+ 001")
    assert datetime.fromisoformat(slot["starts_at"]) == at + timedelta(hours=2)
    # Applying again schedules nothing new.
    assert await api.c.recorder.apply_rules() == 0
