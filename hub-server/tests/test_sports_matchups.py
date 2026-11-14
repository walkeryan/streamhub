"""/sports/events carries football matchups, and /sports/art serves their images behind signed URLs.

Fictional teams; the scoreboard is a fixture behind an ``httpx.MockTransport``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.infra.models import Programme
from app.services.matchup_art import matchup_key
from app.services.sports_facts import SportsFacts
from tests.conftest import Api
from tests.test_epg_integrity import add_source
from tests.test_sports_facts import Espn, fixture

PLACEHOLDER = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"


async def listed(api: Api, title: str, start: datetime, category: str = "College Football") -> None:
    source = await add_source(api, "Feed")
    async with api.c.db.session() as session:
        session.add(
            Programme(
                feed=source["id"],
                epg_channel="bbc1.uk",
                title=title,
                category=category,
                start=start,
                stop=start + timedelta(hours=3),
            )
        )
        await session.commit()


def espn_at(kickoff: datetime) -> Espn:
    board = fixture("scoreboard_college.json")
    stamp = kickoff.strftime("%Y-%m-%dT%H:%MZ")
    board["events"][0]["date"] = board["events"][0]["competitions"][0]["date"] = stamp
    return Espn(board=board)


async def events(api: Api, **params: Any) -> list[dict[str, Any]]:
    response = await api.get("/sports/events", params={"language": "all", "status": "active", **params})
    assert response.status_code == 200, response.text
    return response.json()["events"]


async def test_football_events_carry_matchups_and_others_do_not(api: Api) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    await listed(api, "Lakeview Kestrels at No. 12 Brookfield Badgers", now - timedelta(minutes=30))
    await listed(api, "NBA: Herons vs Otters", now - timedelta(minutes=30), category="Basketball")
    espn = espn_at(now - timedelta(minutes=30))
    api.c.sports_facts = SportsFacts(espn.factory, art=api.c.matchup_art)
    found = await events(api)
    football = next(e for e in found if e["sport"] == "American football")
    basketball = next(e for e in found if e["sport"] == "Basketball")
    assert basketball["matchup"] is None
    matchup = football["matchup"]
    assert matchup["home"]["short_name"] == "Brookfield" and matchup["home"]["rank"] == 12
    assert matchup["away"]["color"] == "#2b4c9a" and matchup["away"]["conference_record"] == "4-1 Lakes League"
    assert matchup["status_detail"] == "Q2 7:14"
    assert matchup["win_probability"] == {"away": 14.6, "home": 85.4, "live": True}
    # No key, no art: nothing is queued and no URL is made up.
    assert matchup["art_url"] is None and api.c.matchup_art.pending == 0


async def test_facts_failing_never_fail_the_listing(api: Api) -> None:
    now = datetime.now(UTC)
    await listed(api, "Lakeview Kestrels at Brookfield Badgers", now - timedelta(minutes=30))

    async def broken(page: list[dict[str, Any]]) -> None:
        raise RuntimeError("fixture failure")

    api.c.sports_facts.annotate = broken  # type: ignore[method-assign]
    found = await events(api)
    assert len(found) == 1 and found[0]["matchup"] is None


async def test_art_is_served_through_a_signed_url(api: Api) -> None:
    now = datetime.now(UTC)
    await listed(api, "Lakeview Kestrels at Brookfield Badgers", now - timedelta(minutes=30))
    # An image made earlier for this pairing (facts are off in tests: the guide's names are the key).
    art = api.c.matchup_art
    art.root.mkdir(parents=True)
    art.path(matchup_key("Lakeview Kestrels", "Brookfield Badgers", "football")).write_bytes(PLACEHOLDER)
    [event] = await events(api)
    url = event["matchup"]["art_url"]
    assert url and "/api/v1/sports/art/" in url
    path = url[url.index("/api/v1/") :]
    # An <img> sends no bearer: the signature is the authority.
    served = await api.client.get(path)
    assert served.status_code == 200 and served.content == PLACEHOLDER
    assert served.headers["content-type"] == "image/jpeg" and served.headers["x-content-type-options"] == "nosniff"
    token = path.rsplit("/", 1)[1]
    assert (await api.client.get(path.replace(token, token[:-2] + "xx"))).status_code == 404
    # A well-signed grant for an image that is not there.
    other = api.c.crypto.sign_json({"m": "f" * 32, "x": api.c.crypto.verify_json(token)["x"]})
    assert (await api.client.get(f"/api/v1/sports/art/{other}")).status_code == 404
    # Nothing but a 32-hex key names a file.
    sneaky = api.c.crypto.sign_json({"m": "../secret.key", "x": api.c.crypto.verify_json(token)["x"]})
    assert (await api.client.get(f"/api/v1/sports/art/{sneaky}")).status_code == 403
