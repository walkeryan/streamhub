"""One API-level proof per permission: without it 403 (or nothing to see), with it the call goes through."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from app.api.deps import require
from app.container import Container
from app.infra.models import AuthSession, Source, Title
from app.infra.settings import AppSettings
from app.main import create_app
from app.services.auth import Principal
from app.services.ids import normalise_title
from app.services.permissions import PERMISSIONS
from tests.conftest import FIXTURES, Api, add_m3u
from tests.people import Member, member, owner_id
from tests.test_adult_surfaces import shown
from tests.test_api_requests import TMDB, configure, upstream  # noqa: F401

LIBRARY, VOD = "lib:shelf:film", "lib:panel:film"


def denied(response: httpx.Response, *missing: str) -> bool:
    return response.status_code == 403 and response.json()["missing"] == sorted(missing)


async def seed_catalog(api: Api) -> None:
    """One owned film on a library server and one provider film, both findable as 'Harbour'."""
    now = datetime.now(UTC)
    async with api.c.db.session() as s:
        s.add(Source(id="shelf", name="Shelf", type="plex", config_enc=api.c.crypto.encrypt_json({})))
        s.add(Source(id="panel", name="Panel", type="xtream", config_enc=api.c.crypto.encrypt_json({})))
        await s.flush()
        for source, name, tmdb in (("shelf", "Harbour Owned", 7001), ("panel", "Harbour Provider", 7002)):
            s.add(
                Title(
                    source_id=source,
                    ext_id="film",
                    kind="movie" if source == "shelf" else "vod",
                    title=name,
                    norm_title=normalise_title(name),
                    tmdb_id=tmdb,
                    genres=["Drama"],
                    added_at=now - timedelta(hours=1),
                )
            )
        # The same film on both kinds of source: a canonical id that either permission can reach.
        for source in ("shelf", "panel"):
            s.add(
                Title(
                    source_id=source,
                    ext_id="both",
                    kind="movie" if source == "shelf" else "vod",
                    title="Harbour Both",
                    norm_title=normalise_title("Harbour Both"),
                    tmdb_id=7003,
                    added_at=now - timedelta(hours=2),
                )
            )
        await s.commit()


async def listed(user: Member | Api) -> set[str]:
    """Every title name the user can reach through browse, search and home."""
    names: set[str] = set()
    for path, params in (
        ("/browse/movies", {"language": "all"}),
        ("/search", {"q": "Harbour", "language": "all"}),
        ("/home", {}),
    ):
        r = await user.get(path, params=params)
        assert r.status_code == 200, (path, r.text)
        names |= shown(r.json())[1]
    return names


async def resolve(user: Member | Api, ref: str, **extra: Any) -> httpx.Response:
    body = {"id": ref, "recovery_generation": user_api(user).c.proxy.recovery_epoch, **extra}
    return await user.post("/playback/resolve", json=body)


def user_api(user: Member | Api) -> Api:
    return user.api if isinstance(user, Member) else user


# ---------------------------------------------------------------- watch.library / watch.vod
@pytest.mark.parametrize(
    ("permission", "blocked_ref", "blocked_name", "open_ref", "open_name"),
    [
        ("watch.library", LIBRARY, "Harbour Owned", VOD, "Harbour Provider"),
        ("watch.vod", VOD, "Harbour Provider", LIBRARY, "Harbour Owned"),
    ],
)
async def test_watch_library_and_vod_gate_listings_detail_and_resolve(
    api: Api, permission: str, blocked_ref: str, blocked_name: str, open_ref: str, open_name: str
) -> None:
    await seed_catalog(api)
    without = await member(api, "family", name="Without", denials=[permission])
    with_it = await member(api, "family", name="With")

    assert {"Harbour Owned", "Harbour Provider"} <= await listed(with_it)
    names = await listed(without)
    assert open_name in names and blocked_name not in names

    assert denied(await without.get(f"/titles/{blocked_ref}"), permission)
    assert denied(await resolve(without, blocked_ref), permission)
    assert denied(await without.get("/playback/options", params={"id": blocked_ref}), permission)
    assert (await without.get(f"/titles/{open_ref}")).status_code == 200
    assert (await with_it.get(f"/titles/{blocked_ref}")).status_code == 200
    # With the permission the resolver runs; whatever it then says, it is not a permission refusal.
    assert "missing" not in (await resolve(with_it, blocked_ref)).json()

    # A canonical id carried by a source the user may watch stays reachable, on that source only.
    detail = await without.get("/titles/tmdb:movie:7003")
    assert detail.status_code == 200, detail.text
    blocked_source = blocked_ref.split(":")[1]
    assert blocked_source not in {s["source_id"] for s in detail.json()["sources"]}
    assert denied(await resolve(without, "tmdb:movie:7003", source_id=blocked_source), permission)
    # A canonical id only a blocked source carries is refused outright.
    only = "tmdb:movie:7001" if permission == "watch.library" else "tmdb:movie:7002"
    assert denied(await without.get(f"/titles/{only}"), permission)
    assert denied(await resolve(without, only), permission)


async def test_watch_scope_is_never_replayed_from_the_listing_cache(api: Api) -> None:
    await seed_catalog(api)
    user = await member(api, "family")
    assert "Harbour Owned" in await listed(user)
    assert (await api.patch(f"/users/{user.id}", json={"denials": ["watch.library"]})).status_code == 200
    await user.refresh()
    assert "Harbour Owned" not in await listed(user)


# ---------------------------------------------------------------- watch.live
async def test_watch_live_gates_channels_guide_and_live_resolve(api: Api) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u")
    with_it = await member(api, "kid", name="With")
    without = await member(api, "kid", name="Without", denials=["watch.live"])

    channels = (await with_it.get("/channels")).json()["channels"]
    assert len(channels) == 7
    channel = channels[0]["id"]
    assert (await with_it.get("/channels/groups")).json()
    assert "missing" not in (await resolve(with_it, channel)).json()

    assert (await without.get("/channels")).json()["channels"] == []
    assert (await without.get("/channels/groups")).json() == []
    assert (await without.get("/epg", params={"channel_ids": channel})).json() in ({}, {channel: []})
    assert (await without.get("/sports")).json()["items"] == []
    assert denied(await resolve(without, channel), "watch.live")
    assert denied(await without.get("/playback/rewind", params={"id": channel}), "watch.live")
    assert denied(await without.get("/playback/options", params={"id": channel}), "watch.live")
    pair = {"panes": [{"id": channel}, {"id": channels[1]["id"]}], "recovery_generation": api.c.proxy.recovery_epoch}
    assert denied(await without.post("/playback/multiview", json=pair), "watch.live")
    # Live is the only thing withheld: the catalog still answers.
    assert (await without.get("/browse/movies")).status_code == 200


async def test_background_sync_started_by_a_restricted_manager_sees_every_row(api: Api) -> None:
    """A sync inherits the request's context; the caller's watch scope must not leak into it."""
    source = await add_m3u(api, FIXTURES / "sample.m3u")
    manager = await member(api, "family", grants=["sources.manage"], denials=["watch.live"])
    assert (await manager.post(f"/sources/{source['id']}/sync")).status_code == 202
    await api.c.sync.wait_idle()
    after = (await api.get(f"/sources/{source['id']}")).json()
    assert after["status"] == "ok" and after["counts"]["channels"] == 7
    assert len((await api.get("/channels")).json()["channels"]) == 7


# ---------------------------------------------------------------- watch.adult
async def seed_adult(api: Api) -> None:
    async with api.c.db.session() as s:
        s.add(Source(id="catalog", name="Catalog", type="xtream", config_enc=api.c.crypto.encrypt_json({})))
        await s.flush()
        for ext, name, adult in (("day", "Harbour Day", False), ("night", "Harbour Night", True)):
            s.add(
                Title(
                    source_id="catalog",
                    ext_id=ext,
                    kind="vod",
                    title=name,
                    norm_title=normalise_title(name),
                    adult=adult,
                    added_at=datetime.now(UTC),
                )
            )
        await s.commit()


async def adult_names(user: Member | Api) -> set[str]:
    r = await user.get("/browse/movies", params={"include_adult": "true", "language": "all"})
    assert r.status_code == 200, r.text
    return shown(r.json())[1]


async def test_watch_adult_needs_the_permission_a_pin_and_an_unlock(api: Api) -> None:
    await seed_adult(api)
    me = await owner_id(api)
    family = await member(api, "family", pin="2468")
    # Without the permission: asking changes nothing and there is nothing to unlock.
    assert await adult_names(family) == {"Harbour Day"}
    assert denied(await family.post("/auth/adult-unlock", json={"pin": "2468"}), "watch.adult")

    # With the permission but no unlock: still hidden. No PIN set: nothing to unlock with.
    assert await adult_names(api) == {"Harbour Day"}
    r = await api.post("/auth/adult-unlock", json={"pin": "1357"})
    assert r.status_code == 409 and r.json()["code"] == "pin_setup_required"
    assert (await api.put(f"/users/{me}/pin", json={"pin": "1357"})).status_code == 204
    wrong = await api.post("/auth/adult-unlock", json={"pin": "0000"})
    assert wrong.status_code == 403 and wrong.json()["code"] == "pin_invalid"
    assert await adult_names(api) == {"Harbour Day"}

    unlocked = await api.post("/auth/adult-unlock", json={"pin": "1357"})
    assert unlocked.status_code == 200, unlocked.text
    until = datetime.fromisoformat(unlocked.json()["adult_unlocked_until"])
    assert timedelta(hours=3, minutes=59) < until - datetime.now(UTC) <= timedelta(hours=4)
    assert (await api.get("/auth/me")).json()["adult_unlocked"] is True
    assert await adult_names(api) == {"Harbour Day", "Harbour Night"}
    # Unlocked, but only when asked for.
    assert shown((await api.get("/browse/movies", params={"language": "all"})).json())[1] == {"Harbour Day"}

    # The unlock belongs to the session, not the user: another device starts locked.
    other = await api.client.post("/api/v1/auth/login", json={"profile_id": api.profile_id, "pin": "1357"})
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    listing = await api.client.get(
        "/api/v1/browse/movies", params={"include_adult": "true", "language": "all"}, headers=other_headers
    )
    assert shown(listing.json())[1] == {"Harbour Day"}

    # Changing the PIN ends the unlock early, and it can be closed by hand.
    assert (await api.put(f"/users/{me}/pin", json={"pin": "9753"})).status_code == 204
    assert await adult_names(api) == {"Harbour Day"}
    assert (await api.post("/auth/adult-unlock", json={"pin": "9753"})).status_code == 200
    assert (await api.post("/auth/adult-lock")).status_code == 204
    assert await adult_names(api) == {"Harbour Day"}


async def test_adult_unlock_expires_and_wrong_pins_are_rate_limited(api: Api) -> None:
    from sqlalchemy import update

    from app.infra.models import utcnow

    await seed_adult(api)
    me = await owner_id(api)
    assert (await api.put(f"/users/{me}/pin", json={"pin": "1357"})).status_code == 204
    assert (await api.post("/auth/adult-unlock", json={"pin": "1357"})).status_code == 200
    async with api.c.db.session() as s:
        await s.execute(update(AuthSession).values(adult_unlocked_until=utcnow() - timedelta(seconds=1)))
        await s.commit()
    assert await adult_names(api) == {"Harbour Day"}
    for _ in range(5):
        assert (await api.post("/auth/adult-unlock", json={"pin": "0000"})).status_code == 403
    locked = await api.post("/auth/adult-unlock", json={"pin": "1357"})
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0


async def test_pin_rules_and_who_may_set_whose(api: Api) -> None:
    me = await owner_id(api)
    kid = await member(api, "kid", name="Small")
    other = await member(api, "kid", name="Other")
    for bad in ("123", "123456789", "12ab", ""):
        assert (await api.put(f"/users/{me}/pin", json={"pin": bad})).status_code == 422, bad
    # One's own PIN needs no permission; someone else's needs users.manage.
    assert (await kid.req("PUT", f"/users/{kid.id}/pin", json={"pin": "12345678"})).status_code == 204
    assert (await kid.req("PUT", f"/users/{other.id}/pin", json={"pin": "1234"})).status_code == 404
    assert (await api.put(f"/users/{other.id}/pin", json={"pin": "4321"})).status_code == 204
    users = {u["id"]: u for u in (await api.get("/users")).json()}
    assert users[kid.id]["has_pin"] and users[other.id]["has_pin"]
    assert (await api.put(f"/users/{other.id}/pin", json={"pin": None})).status_code == 204
    assert not (await api.get(f"/users/{other.id}")).json()["has_pin"]
    # A manager who is not an owner cannot take over an owner by resetting their PIN.
    manager = await member(api, "family", name="Manager", grants=["users.manage"])
    r = await manager.req("PUT", f"/users/{me}/pin", json={"pin": "1111"})
    assert r.status_code == 403 and r.json()["code"] == "owner_required"
    # The PIN is the sign-in PIN where local sign-in is allowed.
    login = await api.client.post("/api/v1/auth/login", json={"profile_id": kid.profile_id, "pin": "12345678"})
    assert login.status_code == 200
    assert (
        await api.client.post("/api/v1/auth/login", json={"profile_id": kid.profile_id, "pin": "1234"})
    ).status_code == 401


# ---------------------------------------------------------------- requests.*
async def test_requests_permissions(api: Api, upstream: respx.MockRouter) -> None:  # noqa: F811
    await configure(api)
    body = {"media_type": "movie", "tmdb_id": 603}
    without = await member(api, "friend", name="Without", denials=["requests.create"])
    friend = await member(api, "friend", name="Friend")
    family = await member(api, "family", name="Family")
    approver = await member(api, "friend", name="Approver", grants=["requests.approve"])

    # requests.create
    r = await without.post("/requests", json=body)
    assert r.status_code == 403 and r.json()["code"] == "request_forbidden"
    assert (await without.get("/titles/tmdb:movie:603")).json()["requestable"] is False
    assert (await friend.get("/titles/tmdb:movie:603")).json()["requestable"] is True
    created = await friend.post("/requests", json=body)
    assert created.status_code == 201 and created.json()["state"] == "pending_approval"

    # requests.approve
    assert (await friend.get("/requests", params={"scope": "all"})).status_code == 403
    assert (await friend.get("/requests")).json()["can_approve"] is False
    assert (await friend.post(f"/requests/{created.json()['id']}/approve", json={})).status_code == 403
    assert (await approver.get("/requests", params={"scope": "all"})).json()["can_approve"] is True
    decided = await approver.post(f"/requests/{created.json()['id']}/approve", json={})
    assert decided.status_code == 200 and decided.json()["state"] == "sent"

    # requests.auto_approve: Family has it, so the request goes straight to the manager.
    upstream.get(f"{TMDB}/tv/1399").mock(
        return_value=httpx.Response(200, json={"id": 1399, "name": "Show", "seasons": [{"season_number": 1}]})
    )
    auto = await family.post("/requests", json={"media_type": "tv", "tmdb_id": 1399})
    assert auto.status_code == 201 and auto.json()["state"] == "sent", auto.text


# ---------------------------------------------------------------- users.* / sources.manage / system.manage
async def test_users_invite(api: Api) -> None:
    kid = await member(api, "kid")
    family = await member(api, "family", name="Family")
    assert denied(await kid.post("/invites", json={"role_id": "friend"}), "users.invite")
    assert denied(await kid.get("/invites"), "users.invite")
    created = await family.post("/invites", json={"role_id": "friend"})
    assert created.status_code == 201 and created.json()["url"].startswith("/invite/")
    assert [i["id"] for i in (await family.get("/invites")).json()] == [created.json()["id"]]


async def test_users_manage(api: Api) -> None:
    family = await member(api, "family")
    manager = await member(api, "family", name="Manager", grants=["users.manage"])
    calls: list[tuple[str, str, Any]] = [
        ("GET", "/users", None),
        ("POST", "/users", {"name": "New"}),
        ("GET", f"/users/{family.id}", None),
        ("PATCH", f"/users/{family.id}", {"name": "Renamed"}),
        ("POST", f"/users/{family.id}/approve", {"role_id": "kid"}),
        ("POST", f"/users/{family.id}/deny", None),
        ("POST", f"/users/{family.id}/suspend", None),
        ("POST", f"/users/{family.id}/reinstate", None),
        ("DELETE", f"/users/{family.id}", None),
        ("GET", "/roles", None),
        ("POST", "/roles", {"name": "Guests"}),
        ("PATCH", "/roles/friend", {"name": "Friends"}),
        ("DELETE", "/roles/friend", None),
        ("GET", "/audit", None),
        ("POST", "/profiles", {"name": "Legacy"}),
        ("DELETE", f"/profiles/{manager.profile_id}", None),
        ("GET", f"/profiles/{manager.profile_id}/content-policy", None),
        ("GET", f"/auth/sessions?user_id={manager.id}", None),
    ]
    for method, path, body in calls:
        assert denied(await family.req(method, path, json=body), "users.manage"), (method, path)
    # Someone else's viewer state is not found rather than forbidden.
    assert (await family.get(f"/profiles/{manager.profile_id}/prefs")).status_code == 404

    assert (await manager.get("/users")).status_code == 200
    assert (await manager.post("/users", json={"name": "New"})).status_code == 201
    assert (await manager.get("/roles")).status_code == 200
    assert (await manager.get("/audit")).status_code == 200
    assert (await manager.get(f"/auth/sessions?user_id={family.id}")).status_code == 200
    assert (await manager.get(f"/profiles/{family.profile_id}/prefs")).status_code == 200
    assert (await manager.post("/profiles", json={"name": "Legacy"})).status_code == 201
    assert (await manager.req("PATCH", f"/users/{family.id}", json={"name": "Renamed"})).status_code == 200


async def test_sources_manage(api: Api) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u")
    source = (await api.get("/sources")).json()[0]["id"]
    group = (await api.get("/channels/groups")).json()[0]["id"]
    channel = (await api.get("/channels")).json()["channels"][0]["id"]
    family = await member(api, "family")
    manager = await member(api, "family", name="Manager", grants=["sources.manage"])
    new = {"type": "streaming_service", "name": "Service", "service_key": "netflix", "config": {}}
    calls: list[tuple[str, str, Any]] = [
        ("POST", "/sources", new),
        ("PATCH", f"/sources/{source}", {"name": "Renamed"}),
        ("DELETE", f"/sources/{source}", None),
        ("POST", f"/sources/{source}/sync", None),
        ("POST", f"/sources/{source}/test", None),
        ("POST", "/sources/plex/pin", None),
        ("GET", "/sources/plex/pin/1", None),
        ("PATCH", f"/channels/groups/{group}", {"hidden": True}),
        ("PATCH", f"/channels/{channel}", {"number": 9}),
    ]
    for method, path, body in calls:
        assert denied(await family.req(method, path, json=body), "sources.manage"), (method, path)
    assert (await family.get("/sources")).status_code == 200  # reading stays open to every user
    assert (await manager.post("/sources", json=new)).status_code == 201
    assert (await manager.req("PATCH", f"/sources/{source}", json={"name": "Renamed"})).status_code == 200
    assert (await manager.req("PATCH", f"/channels/groups/{group}", json={"hidden": True})).status_code == 200
    # sources.manage is not system.manage.
    assert denied(await manager.req("PATCH", "/settings", json={"region": "GB"}), "system.manage")


async def test_system_manage(api: Api) -> None:
    family = await member(api, "family")
    admin = await member(api, "family", name="Admin", grants=["system.manage"])
    theme = {"name": "Mine", "tokens": {"extends": "oled", "accent": "#FF00AA"}}
    calls: list[tuple[str, str, Any]] = [
        ("PATCH", "/settings", {"region": "GB"}),
        ("PUT", "/settings/tmdb_key", {"api_key": ""}),
        ("POST", "/themes", theme),
        ("GET", "/settings/request_manager", None),
        ("PUT", "/settings/request_manager", {"url": None}),
        ("POST", "/settings/request_manager/test", None),
        ("GET", "/playback/recovery", None),
        ("POST", "/playback/recovery/stop", {"confirm": True}),
        ("POST", "/playback/recovery/resume", {"confirm": True}),
    ]
    for method, path, body in calls:
        assert denied(await family.req(method, path, json=body), "system.manage"), (method, path)
    assert (await family.get("/settings")).status_code == 200
    assert (await admin.req("PATCH", "/settings", json={"region": "GB"})).json()["region"] == "GB"
    assert (await admin.post("/themes", json=theme)).status_code == 201
    assert (await admin.get("/settings/request_manager")).status_code == 200
    assert (await admin.get("/playback/recovery")).status_code == 200
    # system.manage is not users.manage or sources.manage.
    assert denied(await admin.get("/users"), "users.manage")
    assert denied(await admin.post("/sources/plex/pin"), "sources.manage")


# ---------------------------------------------------------------- the guard itself, for every permission
@pytest.fixture
async def probe(settings: AppSettings, container: Container) -> Any:
    """The real app plus one throwaway route per permission, each behind ``require``."""
    app = create_app(settings, container=container, start_background=False)
    router = APIRouter()
    for name in sorted(PERMISSIONS):

        def route(permission: str) -> None:
            @router.get(f"/probe/{permission}")
            async def handler(p: Principal = require(permission)) -> dict[str, str]:
                return {"user": p.user_id}

        route(name)

    @router.get("/probe-both")
    async def both(p: Principal = require("users.manage", "system.manage")) -> dict[str, str]:
        return {"user": p.user_id}

    app.router.routes[0:0] = router.routes
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://hub.test") as client,
    ):
        a = Api(client, container)
        await a.login_admin()
        yield a


@pytest.mark.parametrize("permission", sorted(PERMISSIONS))
async def test_require_enforces_every_permission_in_the_catalogue(probe: Api, permission: str) -> None:
    holder = await member(probe, "family", name="Holder", grants=[permission])
    lacking = await member(probe, "family", name="Lacking", denials=[permission])
    assert (await probe.client.get(f"/probe/{permission}", headers=holder.headers)).json() == {"user": holder.id}
    refused = await probe.client.get(f"/probe/{permission}", headers=lacking.headers)
    assert refused.status_code == 403
    assert refused.json() == {
        "code": "forbidden",
        "message": "You do not have permission to do this",
        "missing": [permission],
    }
    assert (await probe.client.get(f"/probe/{permission}")).status_code == 401


async def test_require_names_every_missing_permission(probe: Api) -> None:
    family = await member(probe, "family")
    r = await probe.client.get("/probe-both", headers=family.headers)
    assert r.status_code == 403 and r.json()["missing"] == ["system.manage", "users.manage"]
    half = await member(probe, "family", name="Half", grants=["users.manage"])
    assert (await probe.client.get("/probe-both", headers=half.headers)).json()["missing"] == ["system.manage"]
