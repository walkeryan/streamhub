"""What a source tells someone who only watches from it: its name, kind and state, not where it lives."""

from __future__ import annotations

from app.infra.models import Source
from tests.conftest import Api
from tests.people import member


async def seed(api: Api) -> None:
    crypto = api.c.crypto
    async with api.c.db.session() as s:
        plex = {"server_url": "http://10.20.30.40:32400", "token": "plex-secret", "machine_id": "machine-1"}
        jellyfin = {"server_url": "http://jellyfin.internal.test:8096", "username": "householder", "access_token": "jf"}
        xtream = {"server_url": "http://tv.example.test:8080", "username": "xt-user", "password": "xt-pass"}
        s.add(Source(id="plex", type="plex", name="Library", config_enc=crypto.encrypt_json(plex)))
        s.add(Source(id="jf", type="jellyfin", name="Films", config_enc=crypto.encrypt_json(jellyfin)))
        s.add(Source(id="xt", type="xtream", name="TV", config_enc=crypto.encrypt_json(xtream)))
        await s.commit()


async def test_only_a_source_manager_sees_where_a_source_lives(api: Api) -> None:
    await seed(api)
    mine = (await api.get("/sources")).json()
    assert {s["id"]: s["config_summary"].get("server_url") for s in mine}["plex"] == "http://10.20.30.40:32400"
    assert "householder" in repr(mine)

    for role in ("friend", "family", "kid"):
        viewer = await member(api, role, role.title())
        listed = await viewer.get("/sources")
        assert listed.status_code == 200
        for hidden in (
            "10.20.30.40",
            "jellyfin.internal.test",
            "householder",
            "tv.example.test",
            "xt-user",
            "machine-1",
        ):
            assert hidden not in listed.text, (role, hidden)
        rows = {s["id"]: s for s in listed.json()}
        # Still enough to label a title's source and to filter by it.
        assert (rows["plex"]["name"], rows["plex"]["type"], rows["plex"]["status"]) == ("Library", "plex", "ok")
        assert all(s["config_summary"] == {} for s in rows.values())
        one = await viewer.get("/sources/jf")
        assert one.status_code == 200 and one.json()["config_summary"] == {} and "householder" not in one.text
    helper = await member(api, "friend", "Helper", grants=["sources.manage"])
    assert "10.20.30.40" in (await helper.get("/sources")).text
