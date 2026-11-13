"""/recordings and /recording-rules, and ``recording_state`` on guide payloads."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.domain.entities import EpgSourceSpec, ProgrammeInfo
from app.infra.models import Channel, Programme, Recording
from app.infra.settings import AppSettings
from app.services import ids, recording_policy
from app.services.classification import classification_record, summarize
from tests.conftest import Api
from tests.people import member
from tests.test_epg_integrity import add_source


@pytest.fixture
def settings(settings: AppSettings, tmp_path: Path) -> AppSettings:
    root = tmp_path / "recordings"
    root.mkdir()
    return settings.model_copy(update={"recordings_dir": str(root)})


def soon(hours: float = 1) -> datetime:
    return datetime.now(UTC).replace(microsecond=0) + timedelta(hours=hours)


async def channel(api: Api, source: dict[str, str], epg_id: str = "bbc1.uk") -> Channel:
    async with api.c.db.session() as s:
        return (
            await s.execute(select(Channel).where(Channel.source_id == source["id"], Channel.epg_id == epg_id))
        ).scalar_one()


def hub(ch: Channel) -> str:
    return ids.channel_id(ch.source_id, ch.ext_id)


async def programme(api: Api, ch: Channel, title: str, start: datetime, minutes: int = 60) -> str:
    async with api.c.db.session() as s:
        row = Programme(
            feed=ch.source_id,
            epg_channel=ch.epg_id or "",
            title=title,
            start=start,
            stop=start + timedelta(minutes=minutes),
        )
        s.add(row)
        await s.commit()
        return ids.prog_id(row.id)


async def login(api: Api, name: str, *, kids: bool = False) -> dict[str, str]:
    profile = (await api.post("/profiles", json={"name": name, "pin": "1234", "kids": kids})).json()
    r = await api.client.post("/api/v1/auth/login", json={"profile_id": profile["id"], "pin": "1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


async def guide(api: Api, ch: Channel, **kw: Any) -> list[dict[str, Any]]:
    r = await api.get("/epg", params={"channel_ids": hub(ch), "to": soon(20).isoformat()}, **kw)
    assert r.status_code == 200, r.text
    rows: list[dict[str, Any]] = r.json()[hub(ch)]
    return rows


async def test_off_until_a_recordings_directory_is_configured(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    pid = await programme(api, bbc, "Harbour Lights", soon())
    on = (await api.post("/recordings", json={"programme_id": pid})).json()
    rule = (await api.post("/recording-rules", json={"kind": "keyword", "match": "quiz"})).json()
    api.c.recorder.recordings_dir = None

    assert (await api.get("/recordings")).json() == {"enabled": False, "can_schedule": True, "recordings": []}
    assert (await api.get("/recording-rules")).json() == {"enabled": False, "can_schedule": True, "rules": []}
    for r in (
        await api.post("/recordings", json={"programme_id": pid}),
        await api.delete(f"/recordings/{on['id']}"),
        await api.post("/recording-rules", json={"kind": "series", "match": "Harbour Lights"}),
        await api.patch(f"/recording-rules/{rule['id']}", json={"enabled": False}),
        await api.delete(f"/recording-rules/{rule['id']}"),
    ):
        assert r.status_code == 409 and r.json()["code"] == "recording_unavailable", r.text
    assert [p["recording_state"] for p in await guide(api, bbc)] == [None]


async def test_schedule_a_programme_and_see_it_on_the_guide(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    start = soon()
    pid = await programme(api, bbc, "Harbour Lights S02E05 The Storm", start)
    assert (await api.get("/recordings")).json() == {"enabled": True, "can_schedule": True, "recordings": []}
    assert [p["recording_state"] for p in await guide(api, bbc)] == [None]

    r = await api.post("/recordings", json={"programme_id": pid, "channel_id": hub(bbc)})
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["state"] == "scheduled" and made["profile_id"] == api.profile_id and made["rule_id"] is None
    assert (made["title"], made["subtitle"], made["season"], made["episode"]) == ("Harbour Lights", "The Storm", 2, 5)
    assert (made["channel_id"], made["channel_name"], made["is_sport"]) == (hub(bbc), bbc.name, False)
    assert datetime.fromisoformat(made["starts_at"]) == start
    assert datetime.fromisoformat(made["ends_at"]) == start + timedelta(hours=1)
    assert (made["pad_before_s"], made["pad_after_s"], made["error"], made["size_bytes"]) == (120, 300, None, None)
    assert made["has_file"] is False
    assert "path" not in made and "programme_key" not in made

    again = await api.post("/recordings", json={"programme_id": pid})
    assert again.status_code == 200 and again.json()["id"] == made["id"]
    listed = (await api.get("/recordings")).json()["recordings"]
    assert [x["id"] for x in listed] == [made["id"]] and listed[0]["profile_name"]
    assert (await api.get("/recordings", params={"state": "done"})).json()["recordings"] == []
    assert [p["recording_state"] for p in await guide(api, bbc)] == ["scheduled"]
    channels = (await api.get("/channels", params={"language": "all"})).json()["channels"]
    assert next(c for c in channels if c["id"] == hub(bbc))["next"]["recording_state"] == "scheduled"

    assert (await api.delete(f"/recordings/{made['id']}")).status_code == 204
    assert (await api.get("/recordings")).json()["recordings"] == []
    cancelled = (await api.get("/recordings", params={"state": "cancelled"})).json()["recordings"]
    assert [x["id"] for x in cancelled] == [made["id"]]
    assert [p["recording_state"] for p in await guide(api, bbc)] == [None]
    assert (await api.delete(f"/recordings/{made['id']}")).status_code == 204


async def test_recording_request_validation(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    ended = await programme(api, bbc, "Earlier", soon(-3))
    assert (await api.post("/recordings", json={})).status_code == 422
    assert (await api.post("/recordings", json={"programme_id": "prog:999999"})).status_code == 404
    r = await api.post("/recordings", json={"programme_id": ended})
    assert r.status_code == 409 and r.json()["code"] == "programme_ended"
    r = await api.get("/recordings", params={"state": "bogus"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_request"
    assert (await api.delete("/recordings/nope")).status_code == 404


async def test_recording_rules_api(api: Api) -> None:
    source = await add_source(api)
    bbc, tf1 = await channel(api, source), await channel(api, source, "tf1.fr")
    await programme(api, bbc, "Harbour Lights", soon(1))
    await programme(api, tf1, "Harbour Lights", soon(5))

    r = await api.post("/recording-rules", json={"kind": "series", "match": "Harbour Lights"})
    assert r.status_code == 201, r.text
    rule = r.json()
    assert (rule["kind"], rule["match"], rule["channel_id"], rule["channel_name"]) == (
        "series",
        "Harbour Lights",
        None,
        None,
    )
    assert (rule["new_only"], rule["keep"], rule["enabled"], rule["profile_id"]) == (False, None, True, api.profile_id)
    again = await api.post("/recording-rules", json={"kind": "series", "match": "the harbour lights"})
    assert again.status_code == 200 and again.json()["id"] == rule["id"]
    listed = (await api.get("/recording-rules")).json()
    assert listed["enabled"] and listed["can_schedule"] and [x["id"] for x in listed["rules"]] == [rule["id"]]
    recordings = (await api.get("/recordings")).json()["recordings"]
    assert [(x["rule_id"], x["channel_id"]) for x in recordings] == [(rule["id"], hub(bbc)), (rule["id"], hub(tf1))]

    r = await api.patch(f"/recording-rules/{rule['id']}", json={"channel_id": hub(tf1), "new_only": True, "keep": 5})
    assert r.status_code == 200, r.text
    assert (r.json()["channel_id"], r.json()["channel_name"], r.json()["new_only"], r.json()["keep"]) == (
        hub(tf1),
        tf1.name,
        True,
        5,
    )
    assert [x["channel_id"] for x in (await api.get("/recordings")).json()["recordings"]] == [hub(tf1)]
    r = await api.patch(f"/recording-rules/{rule['id']}", json={"enabled": False, "keep": None})
    assert r.status_code == 200 and not r.json()["enabled"] and r.json()["keep"] is None
    assert r.json()["channel_id"] == hub(tf1)  # fields left out of a patch are kept
    assert (await api.get("/recordings")).json()["recordings"] == []

    assert (await api.post("/recording-rules", json={"kind": "film", "match": "x y z"})).status_code == 422
    r = await api.post("/recording-rules", json={"kind": "keyword", "match": "ab"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_request"
    r = await api.post("/recording-rules", json={"kind": "series", "match": "Harbour", "keep": 0})
    assert r.status_code == 400 and r.json()["code"] == "invalid_request"
    r = await api.post("/recording-rules", json={"kind": "series", "match": "Harbour", "channel_id": "ch:no:no"})
    assert r.status_code == 404
    assert (await api.patch("/recording-rules/nope", json={"enabled": True})).status_code == 404
    assert (await api.patch(f"/recording-rules/{rule['id']}", json={"match": " "})).status_code == 400

    assert (await api.delete(f"/recording-rules/{rule['id']}")).status_code == 204
    assert (await api.get("/recording-rules")).json()["rules"] == []
    assert (await api.delete(f"/recording-rules/{rule['id']}")).status_code == 404


async def test_recordings_are_private_to_their_profile_except_for_admins(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    one, two = await login(api, "One"), await login(api, "Two")
    theirs = await programme(api, bbc, "Harbour Lights", soon(1))
    r = await api.post("/recordings", json={"programme_id": theirs}, headers=one)
    assert r.status_code == 201, r.text
    recording = r.json()
    rule = (await api.post("/recording-rules", json={"kind": "keyword", "match": "nothing"}, headers=one)).json()
    mine = (await api.post("/recordings", json={"programme_id": await programme(api, bbc, "Quiz", soon(3))})).json()

    assert [x["id"] for x in (await api.get("/recordings", headers=one)).json()["recordings"]] == [recording["id"]]
    assert (await api.get("/recordings", headers=two)).json()["recordings"] == []
    assert (await api.get("/recording-rules", headers=two)).json()["rules"] == []
    everything = (await api.get("/recordings")).json()["recordings"]
    assert [(x["id"], x["profile_name"]) for x in everything] == [(recording["id"], "One"), (mine["id"], "Admin")]
    assert [x["id"] for x in (await api.get("/recording-rules")).json()["rules"]] == [rule["id"]]

    assert (await api.delete(f"/recordings/{recording['id']}", headers=two)).status_code == 404
    assert (await api.delete(f"/recordings/{mine['id']}", headers=one)).status_code == 404
    assert (await api.patch(f"/recording-rules/{rule['id']}", json={"keep": 1}, headers=two)).status_code == 404
    assert (await api.delete(f"/recording-rules/{rule['id']}", headers=two)).status_code == 404
    # One file per programme for the household: the guide shows it to everyone, and it cannot be taken twice.
    assert [p["recording_state"] for p in await guide(api, bbc, headers=two)] == ["scheduled", "scheduled"]
    r = await api.post("/recordings", json={"programme_id": theirs}, headers=two)
    assert r.status_code == 409 and r.json()["code"] == "already_scheduled"
    # An admin manages anyone's.
    assert (await api.patch(f"/recording-rules/{rule['id']}", json={"keep": 1})).json()["keep"] == 1
    assert (await api.delete(f"/recordings/{recording['id']}")).status_code == 204


async def test_kids_profiles_can_look_but_not_schedule(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    pid = await programme(api, bbc, "Harbour Lights", soon(1))
    kid = await login(api, "Kid", kids=True)
    assert (await api.get("/recordings", headers=kid)).json() == {
        "enabled": True,
        "can_schedule": False,
        "recordings": [],
    }
    assert (await api.get("/recording-rules", headers=kid)).json()["can_schedule"] is False
    for r in (
        await api.post("/recordings", json={"programme_id": pid}, headers=kid),
        await api.post("/recording-rules", json={"kind": "series", "match": "Harbour Lights"}, headers=kid),
        await api.delete("/recordings/any", headers=kid),
        await api.patch("/recording-rules/any", json={"enabled": False}, headers=kid),
        await api.delete("/recording-rules/any", headers=kid),
    ):
        assert r.status_code == 403 and r.json()["code"] == "recording_forbidden", r.text
    async with api.c.db.session() as s:
        assert list((await s.execute(select(Recording))).scalars()) == []


async def test_a_friend_has_no_recordings_at_all(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    pid = await programme(api, bbc, "Harbour Lights", soon(1))
    mine = (await api.post("/recordings", json={"programme_id": pid})).json()
    rule = (await api.post("/recording-rules", json={"kind": "series", "match": "Harbour Lights"})).json()
    friend = await member(api, "friend", "Friend")
    assert not {"recordings.view", "recordings.schedule"} & set((await friend.get("/auth/me")).json()["permissions"])
    for method, url, body in [
        ("GET", "/recordings", None),
        ("POST", "/recordings", {"programme_id": pid}),
        ("DELETE", f"/recordings/{mine['id']}", None),
        ("GET", "/recording-rules", None),
        ("POST", "/recording-rules", {"kind": "series", "match": "Harbour Lights"}),
        ("PATCH", f"/recording-rules/{rule['id']}", {"enabled": False}),
        ("DELETE", f"/recording-rules/{rule['id']}", None),
    ]:
        r = await friend.req(method, url, json=body)
        assert r.status_code == 403 and r.json()["code"] == "recording_forbidden", (method, url, r.text)
    # Nothing changed, and the guide tells them nothing about what the household records.
    assert [r["state"] for r in (await api.get("/recordings")).json()["recordings"]] == ["scheduled"]
    assert (await api.get("/recording-rules")).json()["rules"][0]["enabled"] is True
    assert [p["recording_state"] for p in await guide(api, bbc, headers=friend.headers)] == [None]
    assert [p["recording_state"] for p in await guide(api, bbc)] == ["scheduled"]
    # View without schedule (the Kid role's set) looks but does not touch; one grant opens scheduling.
    viewer = await member(api, "friend", "Viewer", grants=["recordings.view"])
    await viewer.refresh()
    listed = await viewer.get("/recordings")
    assert listed.status_code == 200 and listed.json()["can_schedule"] is False
    assert (await viewer.post("/recordings", json={"programme_id": pid})).status_code == 403
    scheduler = await member(api, "friend", "Scheduler", grants=["recordings.view", "recordings.schedule"])
    await scheduler.refresh()
    assert (await scheduler.get("/recording-rules")).json()["can_schedule"] is True
    made = await scheduler.post("/recording-rules", json={"kind": "series", "match": "Tide Pool"})
    assert made.status_code == 201, made.text


def test_recording_policy_answers_from_permissions() -> None:
    from app.services.auth import Principal
    from app.services.permissions import BUILTIN_ROLES

    def who(role: str, **kw: Any) -> Principal:
        return Principal("p", permissions=BUILTIN_ROLES[role].permissions, **kw)

    # ``kids`` and the old admin flag decide nothing any more.
    assert recording_policy.can_view(who("kid", kids=True)) and not recording_policy.can_schedule(who("kid"))
    assert recording_policy.can_schedule(who("family", kids=True))
    assert not recording_policy.can_view(who("friend")) and not recording_policy.can_schedule(who("friend"))
    assert recording_policy.sees_all(who("owner")) and not recording_policy.sees_all(who("family"))
    operator = Principal("p", permissions=BUILTIN_ROLES["family"].permissions | {"system.manage"})
    assert operator.is_admin and not recording_policy.sees_all(operator)
    assert recording_policy.sees_all(Principal("p", permissions={"users.manage"}))


async def test_recordings_need_a_session_and_the_view_permission(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    bbc = await channel(api, await add_source(api))
    pid = await programme(api, bbc, "Harbour Lights", soon(1))
    assert (await api.post("/recordings", json={"programme_id": pid})).status_code == 201
    for method, url in [
        ("GET", "/recordings"),
        ("POST", "/recordings"),
        ("DELETE", "/recordings/x"),
        ("GET", "/recording-rules"),
        ("POST", "/recording-rules"),
        ("PATCH", "/recording-rules/x"),
        ("DELETE", "/recording-rules/x"),
    ]:
        assert (await api.client.request(method, "/api/v1" + url, json={})).status_code == 401
    monkeypatch.setattr(recording_policy, "can_view", lambda p: False)
    for url in ("/recordings", "/recording-rules"):
        r = await api.get(url)
        assert r.status_code == 403 and r.json()["code"] == "recording_forbidden"
    assert [p["recording_state"] for p in await guide(api, bbc)] == [None]


async def test_recordings_and_rules_survive_a_real_guide_refresh(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    source = await add_source(api)
    bbc = await channel(api, source)
    start = soon(2)
    listings = [("Harbour Lights S02E05 The Storm", start), ("Evening Quiz", start + timedelta(hours=1))]

    class Provider:
        async def epg_sources(self) -> list[EpgSourceSpec]:
            return [EpgSourceSpec(url="https://epg.invalid/test.xml")]

    class Epg:
        async def iter_programmes(self, spec: EpgSourceSpec, wanted: set[str]) -> AsyncIterator[list[ProgrammeInfo]]:
            yield [
                ProgrammeInfo(channel="bbc1.uk", start=begins, stop=begins + timedelta(hours=1), title=title)
                for title, begins in listings
            ]

    monkeypatch.setattr(api.c.sources, "provider", lambda row: Provider())
    monkeypatch.setattr(api.c.sync, "_epg", Epg())
    assert await api.c.sync.refresh_epg(source["id"]) == 2
    before = {p["title"]: p for p in await guide(api, bbc)}
    quiz = (await api.post("/recordings", json={"programme_id": before["Evening Quiz"]["id"]})).json()
    rule = (await api.post("/recording-rules", json={"kind": "series", "match": "Harbour Lights"})).json()
    first = (await api.get("/recordings")).json()["recordings"]
    assert [(x["title"], x["rule_id"]) for x in first] == [("Harbour Lights", rule["id"]), ("Evening Quiz", None)]

    listings.insert(0, ("Harbour Lights S02E06 Calm", start - timedelta(hours=1)))
    assert await api.c.sync.refresh_epg(source["id"]) == 3
    after = {p["title"]: p for p in await guide(api, bbc)}
    assert after["Evening Quiz"]["id"] != before["Evening Quiz"]["id"]  # the refresh replaced every row
    assert after["Evening Quiz"]["recording_state"] == "scheduled"
    assert after["Harbour Lights S02E05 The Storm"]["recording_state"] == "scheduled"
    second = (await api.get("/recordings")).json()["recordings"]
    assert [x["id"] for x in second[1:]] == [x["id"] for x in first] and second[2]["id"] == quiz["id"]
    assert [(x["title"], x["episode"], x["rule_id"]) for x in second[:1]] == [("Harbour Lights", 6, rule["id"])]
    r = await api.post("/recordings", json={"programme_id": after["Evening Quiz"]["id"]})
    assert r.status_code == 200 and r.json()["id"] == quiz["id"]
    # A client still holding the old guide: its programme id now names another row, and the
    # channel and start it also sends are what the server goes by.
    stale = before["Evening Quiz"]
    r = await api.post(
        "/recordings", json={"programme_id": stale["id"], "channel_id": stale["channel_id"], "start": stale["start"]}
    )
    assert r.status_code == 200 and r.json()["id"] == quiz["id"]
    r = await api.post("/recordings", json={"programme_id": after["Evening Quiz"]["id"], "start": soon(9).isoformat()})
    assert r.status_code == 409 and r.json()["code"] == "guide_changed"


# ---------------------------------------------------------------- who sees whose, and under which ceiling
def tv_rating(code: str) -> dict[str, Any]:
    return summarize("xmltv", "programme", [classification_record("US", "US-TVPG", code)])


async def rated(api: Api, ch: Channel, title: str, start: datetime, code: str) -> str:
    pid = await programme(api, ch, title, start)
    async with api.c.db.session() as s:
        row = await s.get(Programme, int(pid.removeprefix("prog:")))
        assert row is not None
        row.age_evidence = tv_rating(code)
        await s.commit()
    return pid


async def test_whoever_manages_users_sees_everyones_recordings(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    family = await member(api, "family", "Parent")
    manager = await member(api, "family", "Manager", grants=["users.manage"])
    # ``system.manage`` alone runs the server; it is not a view into what people record.
    operator = await member(api, "family", "Operator", grants=["system.manage"])
    quiz = await programme(api, bbc, "Quiz", soon(1))
    theirs = await family.post("/recordings", json={"programme_id": quiz})
    assert theirs.status_code == 201, theirs.text
    rule = (await family.post("/recording-rules", json={"kind": "keyword", "match": "nothing"})).json()
    mine = (await api.post("/recordings", json={"programme_id": await programme(api, bbc, "News", soon(3))})).json()

    async def listed(who: Any) -> tuple[list[str], list[str]]:
        recordings = (await who.get("/recordings")).json()["recordings"]
        rules = (await who.get("/recording-rules")).json()["rules"]
        return [x["id"] for x in recordings], [x["id"] for x in rules]

    assert await listed(manager) == ([theirs.json()["id"], mine["id"]], [rule["id"]])
    assert await listed(family) == ([theirs.json()["id"]], [rule["id"]])
    assert await listed(operator) == ([], [])
    # Seeing all is acting on all: the manager's is the same recording, the operator is turned away.
    again = await manager.post("/recordings", json={"programme_id": quiz})
    assert again.status_code == 200 and again.json()["id"] == theirs.json()["id"]
    r = await operator.post("/recordings", json={"programme_id": quiz})
    assert r.status_code == 409 and r.json()["code"] == "already_scheduled"
    assert (await operator.req("DELETE", f"/recordings/{mine['id']}")).status_code == 404
    assert (await operator.req("PATCH", f"/recording-rules/{rule['id']}", json={"keep": 1})).status_code == 404
    r = await manager.req("PATCH", f"/recording-rules/{rule['id']}", json={"keep": 1})
    assert r.status_code == 200 and r.json()["keep"] == 1
    assert (await manager.req("DELETE", f"/recordings/{mine['id']}")).status_code == 204


async def test_a_rating_ceiling_covers_recordings_too(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    await rated(api, bbc, "On Now", soon(-0.5), "TV-G")  # a channel is judged by what it carries now
    songs = await rated(api, bbc, "Harbour Songs", soon(1), "TV-G")
    dark = await rated(api, bbc, "Harbour After Dark", soon(3), "TV-MA")
    kid = await member(api, "kid", "Kid", grants=["recordings.schedule"])

    r = await kid.post("/recordings", json={"programme_id": dark})
    assert r.status_code in (403, 404) and "After Dark" not in r.text, r.text
    made = await kid.post("/recordings", json={"programme_id": songs})
    assert made.status_code == 201, made.text
    assert (await api.post("/recordings", json={"programme_id": dark})).status_code == 201
    # The guide never names the programme to them, recorded or not.
    rows = await guide(api, bbc, headers=kid.headers)
    assert [(p["title"], p["recording_state"]) for p in rows] == [("On Now", None), ("Harbour Songs", "scheduled")]
    assert [p["recording_state"] for p in await guide(api, bbc)] == [None, "scheduled", "scheduled"]

    # A rule of theirs only ever picks what the ceiling admits.
    await rated(api, bbc, "Harbour Tales", soon(5), "TV-G")
    await rated(api, bbc, "Harbour Nights", soon(7), "TV-MA")
    rule = await kid.post("/recording-rules", json={"kind": "keyword", "match": "harbour"})
    assert rule.status_code == 201, rule.text
    assert [x["title"] for x in (await kid.get("/recordings")).json()["recordings"]] == [
        "Harbour Songs",
        "Harbour Tales",
    ]

    # A recording keeps the rating its programme had: one of theirs from before the ceiling is
    # not shown, cannot be cancelled and does not play. Raising the ceiling brings it back.
    async with api.c.db.session() as s:
        s.add(
            Recording(
                id="older",
                profile_id=kid.profile_id,
                channel_id=hub(bbc),
                channel_name=bbc.name,
                programme_key="older",
                title="Blood Harbor",
                starts_at=soon(-30),
                ends_at=soon(-29),
                state="done",
                age_evidence=tv_rating("TV-MA"),
            )
        )
        await s.commit()
    assert "Blood Harbor" not in (await kid.get("/recordings")).text
    assert "Blood Harbor" not in (await kid.get("/recordings", params={"state": "done"})).text
    assert (await kid.req("DELETE", "/recordings/older")).status_code == 404
    r = await kid.post("/playback/resolve", json={"id": "rec:older", "client": "android_tv", "recovery_generation": 0})
    assert r.status_code == 403 and r.json()["code"] == "content_restricted", r.text
    assert "Blood Harbor" in (await api.get("/recordings", params={"state": "done"})).text
    assert (await api.patch(f"/users/{kid.id}", json={"limits": {"rating_ceiling_tv": "TV-MA"}})).status_code == 200
    await kid.refresh()
    assert "Blood Harbor" in (await kid.get("/recordings")).text


async def test_a_recording_snapshots_its_programmes_rating(api: Api) -> None:
    bbc = await channel(api, await add_source(api))
    pid = await rated(api, bbc, "Harbour Songs", soon(1), "TV-PG")
    made = (await api.post("/recordings", json={"programme_id": pid})).json()
    async with api.c.db.session() as s:
        await s.execute(delete(Programme))  # the next guide refresh no longer lists it
        await s.commit()
        row = await s.get(Recording, made["id"])
        assert row is not None and row.age_evidence == tv_rating("TV-PG")
    # Unknown is not "within the ceiling": a recording from before ratings were kept has none.
    teen = await member(api, "family", "Teen", limits={"rating_ceiling_tv": "TV-14"}, grants=["users.manage"])
    strict = await member(
        api, "family", "Strict", limits={"rating_ceiling_tv": "TV-14", "allow_unrated": False}, grants=["users.manage"]
    )
    async with api.c.db.session() as s:
        s.add(
            Recording(
                id="unrated",
                profile_id=api.profile_id,
                channel_id=hub(bbc),
                programme_key="unrated",
                title="Loose Reel",
                starts_at=soon(4),
                ends_at=soon(5),
            )
        )
        await s.commit()
    assert [x["title"] for x in (await teen.get("/recordings")).json()["recordings"]] == ["Harbour Songs", "Loose Reel"]
    assert [x["title"] for x in (await strict.get("/recordings")).json()["recordings"]] == ["Harbour Songs"]
