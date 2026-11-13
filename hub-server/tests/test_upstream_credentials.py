"""An upstream URL carries the source's credentials (a Plex token, an Xtream username and
password, a provider's signed query). Only someone who manages sources is ever handed one."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import respx

from app.domain.entities import PlaybackMode, StreamTarget
from app.infra.models import Source, Title
from tests.conftest import FIXTURES, Api, add_m3u
from tests.people import Member, member

PLEX = "http://plex.example.test:32400"
FILM = "lib:plex:55"
XTREAM_URL = "http://tv.example.test:8080/movie/xt-user/xt-secret-pass/901.mkv"


@pytest.fixture(autouse=True)
def no_network() -> Iterator[None]:
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        router.route(host="hub.test").pass_through()
        router.route(host="plex.example.test").respond(404)
        yield


async def library(api: Api) -> None:
    await api.accept_disclaimer()
    config = api.c.crypto.encrypt_json({"server_url": PLEX, "token": "plex-secret-token"})
    async with api.c.db.session() as s:
        s.add(Source(id="plex", type="plex", name="Library", config_enc=config))
        part = {"part_key": "/library/parts/9/file.mp4", "container": "mp4"}
        s.add(Title(source_id="plex", ext_id="55", kind="movie", title="The Mill", duration_ms=7_200_000, extra=part))
        await s.commit()


async def resolve(who: Api | Member, ref: str, client: str) -> dict[str, Any]:
    epoch = (who.api if isinstance(who, Member) else who).c.proxy.recovery_epoch
    r = await who.post("/playback/resolve", json={"id": ref, "client": client, "recovery_generation": epoch})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def served_by_the_hub(d: dict[str, Any]) -> bool:
    return bool(
        d["mode"] in ("proxy", "hls") and d["url"].startswith("http://hub.test/api/v1/stream/") and d["headers"] == {}
    )


@pytest.mark.parametrize("client", ["web", "android_tv"])
async def test_a_friend_never_receives_the_plex_token(api: Api, client: str) -> None:
    await library(api)
    friend = await member(api, "friend", "Friend")
    theirs = await resolve(friend, FILM, client)
    assert served_by_the_hub(theirs), theirs
    assert "plex-secret-token" not in repr(theirs) and "plex.example.test" not in repr(theirs)
    # Whoever manages the sources already holds the token, and still plays it directly.
    mine = await resolve(api, FILM, client)
    assert mine["mode"] == "direct" and "X-Plex-Token=plex-secret-token" in mine["url"]
    # Being a parent, or an inviter, is not managing sources.
    parent = await member(api, "family", "Parent")
    assert served_by_the_hub(await resolve(parent, FILM, client))
    trusted = await member(api, "friend", "Helper", grants=["sources.manage"])
    assert (await resolve(trusted, FILM, client))["mode"] == "direct"


@pytest.mark.parametrize("client", ["web", "android_tv"])
async def test_a_friend_never_receives_a_provider_url(api: Api, client: str) -> None:
    await add_m3u(api, FIXTURES / "sample.m3u")
    listed = (await api.get("/channels", params={"language": "all"})).json()["channels"]
    channel = next(c["id"] for c in listed if c["name"] == "BBC One HD")
    assert (await resolve(api, channel, client))["mode"] == "direct"
    friend = await member(api, "friend", "Friend")
    theirs = await resolve(friend, channel, client)
    assert served_by_the_hub(theirs) and "streams.example" not in repr(theirs), theirs
    # Not even where the owner asked for streams never to be proxied: that is the owner's own choice
    # of route, not leave to hand a provider's address to everyone who may watch.
    assert (await api.patch("/settings", json={"proxy_mode": "never"})).status_code == 200
    assert (await resolve(api, channel, client))["mode"] == "direct"
    theirs = await resolve(friend, channel, client)
    assert served_by_the_hub(theirs) and "streams.example" not in repr(theirs), theirs


async def test_xtream_style_credentials_stay_on_the_server(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same rule at its one chokepoint, for a URL with the username and password in its path."""
    await library(api)
    friend = await member(api, "friend", "Friend")

    async def target(*_a: Any, **_kw: Any) -> StreamTarget:
        return StreamTarget(url=XTREAM_URL, mime="video/x-matroska", live=False)

    _, provider = await api.c.sources.provider_for("plex")
    monkeypatch.setattr(provider, "stream_for_item", target)
    for client in ("web", "android_tv"):
        theirs = await resolve(friend, FILM, client)
        assert theirs["mode"] != PlaybackMode.DIRECT.value and "xt-secret-pass" not in repr(theirs), theirs
