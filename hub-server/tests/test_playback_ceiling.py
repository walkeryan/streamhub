"""A rating ceiling reaches playback itself, flag or no flag: guarded modes, intervals, private caching.

ffmpeg is stubbed (a dated playlist and one segment appear); no upstream is opened.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.infra.models import Programme
from app.proxy import stream_proxy
from app.services.classification import classification_record, summarize
from app.services.content_policy import active_principal
from app.services.errors import Forbidden
from app.services.streams import VALID_TTL_S
from tests.conftest import FIXTURES, Api, add_m3u
from tests.people import Member, member
from tests.test_content_policy import activate
from tests.test_source_busy import Process
from tests.test_stream_sessions import Clock

NOW = datetime.now(UTC).replace(second=0, microsecond=0)


def rating(code: str) -> dict[str, Any]:
    return summarize("xmltv", "programme", [classification_record("US", "US-TVPG", code)])


def airing(source: str, channel: str, title: str, start: int, stop: int, code: str) -> Programme:
    return Programme(
        feed=source,
        epg_channel=channel,
        title=title,
        start=NOW + timedelta(minutes=start),
        stop=NOW + timedelta(minutes=stop),
        age_evidence=rating(code),
    )


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[Process]:
    started: list[Process] = []

    async def spawn(*args: Any, **kwargs: Any) -> Process:
        playlist = Path(args[-1])
        dated = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        playlist.write_text(
            f"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-PROGRAM-DATE-TIME:{dated}\n#EXTINF:4,\nseg_00000.ts\n"
        )
        (playlist.parent / "seg_00000.ts").write_bytes(b"\x47" * 188)
        started.append(Process())
        return started[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(stream_proxy, "HLS_ADMISSION_WAIT_S", 0.0)
    return started


@pytest.fixture
async def live(api: Api) -> dict[str, str]:
    """Two channels with a rated guide: BBC One plays direct, TF1 has a provider archive."""
    source = await add_m3u(api, FIXTURES / "sample.m3u")
    async with api.c.db.session() as s:
        s.add_all(
            [
                airing(source["id"], "bbc1.uk", "Harbour Songs", -30, 30, "TV-PG"),
                airing(source["id"], "tf1.fr", "Morning", -120, -40, "TV-G"),
                airing(source["id"], "tf1.fr", "Middle", -40, -20, "TV-MA"),
                airing(source["id"], "tf1.fr", "Now Show", -20, 30, "TV-G"),
            ]
        )
        await s.commit()
    listed = (await api.get("/channels", params={"language": "all"})).json()["channels"]
    by_name = {c["name"]: c["id"] for c in listed}
    return {"source": source["id"], "bbc": by_name["BBC One HD"], "tf1": by_name["TF1"]}


async def play(who: Member | Api, ref: str, client: str = "web", **body: Any) -> Any:
    generation = who.c.proxy.recovery_epoch if isinstance(who, Api) else who.api.c.proxy.recovery_epoch
    return await who.post(
        "/playback/resolve", json={"id": ref, "client": client, "recovery_generation": generation, **body}
    )


def refused(r: Any) -> bool:
    return bool(r.status_code == 403 and r.json()["code"] == "content_restricted")


async def programmes(api: Api) -> dict[str, Programme]:
    async with api.c.db.session() as s:
        return {p.title: p for p in (await s.execute(select(Programme))).scalars()}


async def test_live_is_served_through_the_guard_under_a_ceiling(api: Api, live: dict[str, str]) -> None:
    kid = await member(api, "kid", "Kid")
    # Nobody else is rerouted: the owner still plays the provider's own URL.
    assert (await play(api, live["bbc"])).json()["mode"] == "direct"
    for client in ("web", "android_tv"):
        r = await play(kid, live["bbc"], client)
        assert r.status_code == 200, r.text
        d = r.json()
        assert (d["mode"], d["live"], d["headers"]) == ("hls", True, {}) and "/api/v1/stream/" in d["url"]
        assert "streams.example" not in r.text
        grant = api.c.tokens.verify(d["url"].split("/stream/", 1)[1].split("/", 1)[0])
        assert grant is not None and (grant.content_ref, grant.session_id) == (live["bbc"], grant.session_id)
    # A provider archive file is passed through the guard, never handed over as a URL.
    archive = (await programmes(api))["Morning"]
    r = await play(kid, live["tf1"], "android_tv", catchup_programme_id=f"prog:{archive.id}")
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "proxy" and "streams.example" not in r.text
    r = await play(api, live["tf1"], "android_tv", catchup_programme_id=f"prog:{archive.id}")
    assert r.json()["mode"] == "direct"


async def test_what_is_on_air_above_the_ceiling_is_refused_at_resolve(api: Api, live: dict[str, str]) -> None:
    kid = await member(api, "kid", "Kid", limits={"rating_ceiling_tv": "TV-G"})
    assert refused(await play(kid, live["bbc"]))
    assert (await play(kid, live["tf1"])).status_code == 200
    # The resolver refuses on its own, for callers that do not come through the HTTP gate.
    principal = await api.c.auth.resolve_principal(kid.tokens["access_token"])
    token = active_principal.set(principal)
    try:
        with pytest.raises(Forbidden) as caught:
            await api.c.playback.resolve(kid.profile_id, live["bbc"])
        assert caught.value.code == "content_restricted"
        with pytest.raises(Forbidden):
            await api.c.playback.rewind_capability(kid.profile_id, live["bbc"])
    finally:
        active_principal.reset(token)


async def test_rewind_and_catch_up_intervals_are_checked(api: Api, live: dict[str, str]) -> None:
    kid = await member(api, "kid", "Kid")
    rows = await programmes(api)

    def at(minutes: int) -> str:
        return (NOW + timedelta(minutes=minutes)).isoformat()

    # The archive is asked for from the chosen minute up to now: all of that is judged.
    assert (await play(kid, live["tf1"], rewind_at=at(-10))).status_code == 200
    assert refused(await play(kid, live["tf1"], rewind_at=at(-50)))  # runs on into "Middle"
    assert (await play(api, live["tf1"], rewind_at=at(-50))).status_code == 200
    # A past programme is its whole slot, including anything else the guide lists inside it.
    morning = f"prog:{rows['Morning'].id}"
    assert (await play(kid, live["tf1"], catchup_programme_id=morning)).status_code == 200
    async with api.c.db.session() as s:
        s.add(airing(live["source"], "tf1.fr", "Late Insert", -90, -80, "TV-MA"))
        await s.commit()
    assert refused(await play(kid, live["tf1"], catchup_programme_id=morning))
    assert (await play(api, live["tf1"], catchup_programme_id=morning)).status_code == 200


async def test_segments_are_rechecked_and_private(api: Api, live: dict[str, str], ffmpeg: list[Process]) -> None:
    clock = Clock()
    api.c.guard.clock = clock
    kid = await member(api, "kid", "Kid")

    async def segment(who: Member | Api, ref: str) -> tuple[str, Any]:
        d = (await play(who, ref)).json()
        assert d["mode"] == "hls", d
        playlist = await api.client.get(d["url"])
        assert playlist.status_code == 200, playlist.text
        path = next(line for line in playlist.text.splitlines() if line.endswith(".ts"))
        return path, await api.client.get(path)

    path, served = await segment(kid, live["bbc"])
    assert served.status_code == 200 and served.headers["cache-control"] == "private, no-store"
    _, theirs = await segment(api, live["tf1"])
    assert theirs.status_code == 200 and theirs.headers["cache-control"] == "max-age=60"

    # The ceiling is lowered while the stream runs: its very next segment is refused, and once
    # the viewer is validated again the grant itself is out of date.
    lowered = await api.patch(f"/users/{kid.id}", json={"limits": {"rating_ceiling_tv": "TV-G"}})
    assert lowered.status_code == 200, lowered.text
    assert refused(await api.client.get(path))
    clock.now += VALID_TTL_S + 0.1
    stale = await api.client.get(path)
    assert stale.status_code == 401 and stale.json()["code"] == "session_stale"
    await kid.refresh()
    assert refused(await play(kid, live["bbc"]))
    await api.c.proxy.shutdown()


async def test_the_legacy_flag_still_guards_everyone(api: Api, live: dict[str, str]) -> None:
    await activate(api)
    try:
        # Under the flag the engine runs for every profile, the owner's included.
        d = (await play(api, live["bbc"])).json()
        assert d["mode"] == "hls" and "/api/v1/stream/" in d["url"]
        archive = (await programmes(api))["Morning"]
        r = await play(api, live["tf1"], "android_tv", catchup_programme_id=f"prog:{archive.id}")
        assert r.json()["mode"] == "proxy"
    finally:
        api.c.settings.content_policy_enabled = False
