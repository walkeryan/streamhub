from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.infra.models import AuthSession, Profile, Progress, Source, Title, utcnow
from tests.conftest import Api


async def library(api: Api) -> None:
    now = utcnow()
    async with api.c.db.session() as s:
        s.add(Source(id="s", name="Library", type="plex", enabled=True, config_enc="test"))
        await s.flush()
        for ext, kind, genre, parent, episode, added in [
            ("show", "show", "Drama", None, None, -20),
            ("season", "season", "", "show", None, -20),
            ("first", "episode", "", "season", 1, -20),
            ("new", "episode", "", "season", 2, -1),
            ("old", "episode", "", "season", 3, -15),
            ("restricted", "episode", "", "season", 4, -1),
            ("drama", "movie", "Drama", None, None, -5),
            ("funny", "movie", "Comedy", None, None, -2),
            ("another", "movie", "Comedy", None, None, -4),
            ("comedies", "show", "Comedy", None, None, -5),
        ]:
            s.add(
                Title(
                    source_id="s",
                    ext_id=ext,
                    kind=kind,
                    adult=ext == "restricted",
                    title=ext.title(),
                    genres=[genre] if genre else [],
                    parent_ext_id=parent,
                    episode_number=episode,
                    season_number=1 if parent else None,
                    added_at=now + timedelta(days=added),
                )
            )
        s.add(
            Progress(
                profile_id=api.profile_id,
                ref_id="lib:s:first",
                position_ms=100,
                duration_ms=100,
                completed=True,
                updated_at=now - timedelta(days=4),
            )
        )
        await s.commit()


async def test_new_episodes_and_personal_feedback(api: Api, monkeypatch):
    await library(api)

    async def unchanged(row):
        return row

    monkeypatch.setattr(api.c.catalog, "refresh_detail", unchanged)
    r = await api.req("GET", "/for-you")
    assert r.status_code == 200, r.text
    feed = r.json()
    assert [t["id"] for t in feed["new_episodes"]] == ["lib:s:new"]
    assert feed["history_used"] and feed["coming_up"] == []
    for vote in ("up", "up"):
        r = await api.req("PUT", "/for-you/feedback", json={"id": "lib:s:funny", "vote": vote})
        assert r.status_code == 200, r.text
    rated = (await api.req("GET", "/for-you")).json()
    assert rated["movies"][0]["id"] in {"lib:s:funny", "lib:s:another"}
    assert rated["feedback"]["lib:s:funny"] == "up"
    assert "lib:s:comedies" in [t["id"] for t in rated["series"]]
    assert (await api.req("PUT", "/for-you/feedback", json={"id": "lib:s:funny", "vote": "down"})).is_success
    assert "lib:s:funny" not in [t["id"] for t in (await api.req("GET", "/for-you")).json()["movies"]]
    assert (await api.req("PUT", "/for-you/feedback", json={"id": "lib:s:funny", "vote": None})).is_success
    assert "lib:s:funny" not in (await api.req("GET", "/for-you")).json()["feedback"]


async def test_controller_requires_tv_consent_and_acknowledges_once(api: Api, monkeypatch):
    phone = dict(api.headers)
    await api.login_admin()
    tv = dict(api.headers)
    async with api.c.db.session() as s:
        row = await s.scalar(select(AuthSession).order_by(AuthSession.created_at.desc()))
        row.kind = "tv"
        row.label = "Living room"
        await s.commit()

    async def req(headers, method, path, **kw):
        return await api.client.request(method, "/api/v1/cast" + path, headers=headers, **kw)

    assert (await req(phone, "POST", "/receiver/sync", json={"enabled": True})).status_code == 403
    assert (await req(tv, "POST", "/receiver/sync", json={"enabled": True})).is_success
    target = (await req(phone, "GET", "/targets")).json()["targets"][0]
    link = (await req(phone, "POST", "/connect", json={"target_id": target["id"]})).json()["session"]
    assert link["status"] == "pending"
    key = link["id"]
    sync = {"enabled": True, "session_id": key}
    assert (await req(tv, "POST", "/receiver/sync", json=sync)).json()["session"]["status"] == "pending"
    assert (await req(phone, "POST", "/receiver/decision", json={"session_id": key, "accept": True})).status_code == 404
    assert (await req(tv, "POST", "/receiver/decision", json={"session_id": key, "accept": True})).status_code == 204
    await req(
        tv,
        "POST",
        "/receiver/sync",
        json={**sync, "playback": {"id": "lib:s:film", "playing": True, "title": "A film"}},
    )
    command = {"session_id": key, "action": "pause", "request_id": str(uuid4()), "id": "lib:s:film"}
    for _ in range(2):
        assert (await req(phone, "POST", "/commands", json=command)).json() == {"seq": 1}
    data = (await req(tv, "POST", "/receiver/sync", json=sync)).json()
    assert data["command"]["action"] == "pause"
    assert (await req(tv, "POST", "/receiver/sync", json={**sync, "ack": 1})).json()["command"] is None
    assert (await req(phone, "GET", "/session")).json()["session"]["last_ack"] == 1
    assert (await req(phone, "DELETE", f"/session/{key}")).status_code == 204
    assert (await req(tv, "POST", "/receiver/sync", json=sync)).json()["session"] is None


async def test_upcoming_calendar_uses_seven_days_and_deduplicates_a_batch(api: Api, monkeypatch):
    from datetime import date

    # Include today and the next six calendar days. A later season date is outside the window.
    first = {"season_number": 2, "episode_number": 4, "air_date": "2026-11-27"}

    async def details(path, ttl, **kw):
        return {"next_episode_to_air": first}

    async def season(series_id, number):
        return {
            "episodes": [
                first,
                {"season_number": 2, "episode_number": 5, "air_date": "2026-12-02"},
                {"season_number": 2, "episode_number": 6, "air_date": "2026-12-03"},
            ]
        }

    monkeypatch.setattr(api.c.tmdb, "_get", details)
    monkeypatch.setattr(api.c.tmdb, "season", season)
    rows = await api.c.tmdb.upcoming_episodes(1, date(2026, 11, 26))
    assert [e["episode_number"] for e in rows] == [4, 5]


async def test_together_requires_opt_in_and_never_exposes_individual_votes(api: Api):
    await library(api)
    response = await api.req("POST", "/for-you/together", json={})
    assert response.status_code == 200, response.text
    result = response.json()
    path = "/for-you/together/" + result["code"]
    assert result["member_count"] == 1 and "feedback" not in result
    async with api.c.db.session() as session:
        session.add(Profile(id="other-viewer", name="Other"))
        await session.commit()
    login = await api.client.post("/api/v1/auth/login", json={"profile_id": "other-viewer"})
    assert login.status_code == 200, login.text
    guest = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert (await api.req("GET", path, headers=guest)).status_code == 404
    joined = await api.req("POST", path + "/join", headers=guest, json={})
    assert joined.status_code == 200, joined.text
    assert joined.json()["member_count"] == 2
    assert all(any(s["playable_in_app"] for s in t["sources"]) for t in joined.json()["tiles"])
    assert (await api.req("DELETE", path, headers=guest)).status_code == 204
    assert (await api.req("GET", path, headers=guest)).status_code == 404
    assert (await api.req("GET", path)).json()["member_count"] == 1
