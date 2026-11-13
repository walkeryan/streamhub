"""A user's rating ceiling runs the content-policy engine for that user, flag or no flag."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.domain.entities import MetaTitle
from app.infra.models import AgeClassification, Channel, ChannelGroup, Profile, Programme, Source, Title
from app.services.classification import MOVIE_CODES, TV_CODES, classification_record, summarize
from app.services.content_policy import active_principal
from app.services.errors import Forbidden
from app.services.ids import normalise_title
from app.services.image_grants import image_payload, validate_image_grant
from app.services.permissions import Limits
from tests.conftest import Api
from tests.people import Member, member
from tests.test_adult_surfaces import shown

NOW = datetime.now(UTC)
ALL_LANGUAGES = {"language": "all"}

G, PG, PG13, R = "Meadow Song", "Harbour Lights", "Storm Front", "Night Shift"
UNRATED, NO_ID, STALE, ADULT_PG = "Loose Reel", "Home Video", "Old Print", "Velvet Room"
SHOW_PG, SHOW_MA, SHOW_UNKNOWN = "Tide Pool", "Blood Harbor", "Quiet Street"
MOVIES = {G, PG, PG13, R, UNRATED, NO_ID, STALE, ADULT_PG}
SHOWS = {SHOW_PG, SHOW_MA, SHOW_UNKNOWN}


def evidence(key: str, code: str) -> dict[str, Any]:
    movie = key.startswith("tmdb:movie:")
    record = classification_record("US", "US-MPA" if movie else "US-TVPG", code)
    return {**summarize("tmdb", "movie" if movie else "series", [record]), "subject_key": key}


def rated(key: str, code: str, *, age_days: int = 0) -> AgeClassification:
    return AgeClassification(
        subject_key=key, provider="tmdb", evidence=evidence(key, code), observed_at=NOW - timedelta(days=age_days)
    )


def title(ext: str, name: str, *, kind: str = "vod", **kw: Any) -> Title:
    return Title(
        source_id="catalog",
        ext_id=ext,
        kind=kind,
        title=name,
        norm_title=normalise_title(name),
        genres=["Drama"],
        added_at=NOW - timedelta(hours=3),
        **kw,
    )


def programme(channel: str, name: str, code: str | None) -> Programme:
    rating = summarize("xmltv", "programme", [classification_record("US", "US-TVPG", code)]) if code else {}
    return Programme(
        feed="catalog",
        epg_channel=channel,
        start=NOW - timedelta(minutes=20),
        stop=NOW + timedelta(minutes=40),
        title=name,
        age_evidence=rating,
    )


@pytest.fixture
async def seeded(api: Api) -> Api:
    async with api.c.db.session() as s:
        s.add(Source(id="catalog", name="Catalog", type="xtream", config_enc="unused", counts={"vod": 8}))
        await s.flush()
        s.add_all(
            [
                title("g", G, tmdb_id=101),
                title("pg", PG, tmdb_id=102),
                title("pg13", PG13, tmdb_id=103),
                title("r", R, tmdb_id=104),
                title("unrated", UNRATED, tmdb_id=105),
                title("noid", NO_ID),
                title("stale", STALE, tmdb_id=106),
                title("adultpg", ADULT_PG, tmdb_id=107, adult=True),
                title("show-pg", SHOW_PG, kind="series", tmdb_id=201),
                title("show-pg-s1", "Season 1", kind="season", parent_ext_id="show-pg", season_number=1),
                title("show-pg-e1", "Low Water", kind="episode", parent_ext_id="show-pg-s1", episode_number=1),
                title("show-ma", SHOW_MA, kind="series", tmdb_id=202),
                title("show-ma-s1", "Season 1", kind="season", parent_ext_id="show-ma", season_number=1),
                title("show-ma-e1", "Red Dock", kind="episode", parent_ext_id="show-ma-s1", episode_number=1),
                # Some sources hang an episode straight off its show.
                title("show-ma-x", "Red Dock Special", kind="episode", parent_ext_id="show-ma"),
                title("show-unk", SHOW_UNKNOWN, kind="series", tmdb_id=203),
                title("show-unk-e1", "First Door", kind="episode", parent_ext_id="show-unk"),
                title("orphan", "Loose Episode", kind="episode", parent_ext_id="nowhere"),
                rated("tmdb:movie:101", "G"),
                rated("tmdb:movie:102", "PG"),
                rated("tmdb:movie:103", "PG-13"),
                rated("tmdb:movie:104", "R"),
                rated("tmdb:movie:106", "PG", age_days=8),
                rated("tmdb:movie:107", "PG"),
                rated("tmdb:tv:201", "TV-PG"),
                rated("tmdb:tv:202", "TV-MA"),
                ChannelGroup(id="g", source_id="catalog", name="G"),
                Channel(source_id="catalog", ext_id="kids", name="Kids", epg_id="kids", group_id="g", group_name="G"),
                Channel(source_id="catalog", ext_id="news", name="News", epg_id="news", group_id="g", group_name="G"),
                Channel(source_id="catalog", ext_id="late", name="Late", epg_id="late", group_id="g", group_name="G"),
                programme("kids", "Morning Songs", "TV-G"),
                programme("news", "Headlines", None),
                programme("late", "After Dark", "TV-MA"),
            ]
        )
        await s.commit()
    return api


async def titles_on(who: Member | Api, path: str, **params: Any) -> set[str]:
    r = await who.get(path, params={**ALL_LANGUAGES, **params})
    assert r.status_code == 200, r.text
    return shown(r.json())[1]


async def movies(who: Member | Api) -> set[str]:
    return await titles_on(who, "/browse/movies") & MOVIES


async def shows(who: Member | Api) -> set[str]:
    return await titles_on(who, "/browse/shows") & SHOWS


async def allowed(api: Api, who: Member, *refs: str) -> set[str]:
    return set(await api.c.content_policy.allowed_refs(who.profile_id, list(refs)))


def lib(*exts: str) -> list[str]:
    return [f"lib:catalog:{ext}" for ext in exts]


# ---------------------------------------------------------------- the rule set
async def test_values_derive_from_effective_limits(seeded: Api) -> None:
    api = seeded
    policy = api.c.content_policy
    kid = await member(api, "kid", "Kid")
    values = policy.values(await policy.profile(kid.profile_id))
    assert values == {
        "restricted": True,
        "allowed_movie_codes": ["G", "PG"],
        "allowed_tv_codes": ["TV-Y", "TV-Y7", "TV-G", "TV-PG"],
        "allow_unknown": False,
    }
    parent = await member(api, "family", "Parent")
    assert policy.values(await policy.profile(parent.profile_id)) == {
        "restricted": False,
        "allowed_movie_codes": ["G", "PG", "PG-13", "R", "NC-17"],
        "allowed_tv_codes": ["TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14", "TV-MA"],
        "allow_unknown": True,
    }
    # One ceiling is enough to restrict; the other system stays open.
    teen = await member(api, "family", "Teen", limits={"rating_ceiling_movie": "PG-13"})
    values = policy.values(await policy.profile(teen.profile_id))
    assert values["restricted"] is True and values["allow_unknown"] is True
    assert values["allowed_movie_codes"] == ["G", "PG", "PG-13"] and set(values["allowed_tv_codes"]) == TV_CODES
    # Hiding unknowns alone restricts too.
    careful = await member(api, "family", "Careful", limits={"allow_unrated": False})
    values = policy.values(await policy.profile(careful.profile_id))
    assert values["restricted"] is True and values["allow_unknown"] is False
    assert set(values["allowed_movie_codes"]) == MOVIE_CODES


async def test_applies_to_follows_the_principal_not_the_flag(seeded: Api) -> None:
    api = seeded
    policy = api.c.content_policy
    assert policy.enabled is False
    kid = await member(api, "kid", "Kid")
    parent = await member(api, "family", "Parent")
    kid_principal = await api.c.auth.resolve_principal(kid.tokens["access_token"])
    parent_principal = await api.c.auth.resolve_principal(parent.tokens["access_token"])
    assert policy.applies_to(kid_principal) is True
    assert policy.applies_to(parent_principal) is False and policy.applies_to(None) is False
    api.c.settings.content_policy_enabled = True
    try:
        # The legacy regime runs the engine for everyone; each stored policy decides what it filters.
        assert policy.applies_to(parent_principal) is True and policy.applies_to(None) is True
    finally:
        api.c.settings.content_policy_enabled = False


async def test_legacy_flag_keeps_the_stored_policy_as_the_rule_set(seeded: Api) -> None:
    api = seeded
    policy = api.c.content_policy
    kid = await member(api, "kid", "Kid")
    async with api.c.db.session() as s:
        row = await s.get(Profile, kid.profile_id)
        assert row is not None
        row.content_policy = {"restricted": True, "allowed_movie_codes": ["G", "PG", "PG-13"], "allowed_tv_codes": []}
        await s.commit()
    api.c.settings.content_policy_enabled = True
    try:
        values = policy.values(await policy.profile(kid.profile_id))
        assert values["allowed_movie_codes"] == ["G", "PG", "PG-13"] and values["allow_unknown"] is False
        assert await allowed(api, kid, *lib("pg13", "r", "show-pg-e1")) == {"lib:catalog:pg13"}
    finally:
        api.c.settings.content_policy_enabled = False
    assert policy.values(await policy.profile(kid.profile_id))["allowed_movie_codes"] == ["G", "PG"]


# ---------------------------------------------------------------- a Kid, every surface
async def test_kid_browse_shows_only_what_the_ceiling_admits(seeded: Api) -> None:
    api = seeded
    kid = await member(api, "kid", "Kid")
    assert await movies(kid) == {G, PG}
    assert await shows(kid) == {SHOW_PG}
    genres = (await kid.get("/browse/movies/genres", params=ALL_LANGUAGES)).json()
    assert genres == [{"name": "drama", "count": 2}]
    # The owner, and anyone without a ceiling, sees the catalog as before (adult rows aside).
    assert await movies(api) == MOVIES - {ADULT_PG}
    assert await shows(api) == SHOWS
    parent = await member(api, "family", "Parent")
    assert await movies(parent) == MOVIES - {ADULT_PG} and await shows(parent) == SHOWS


async def test_kid_search_and_home(seeded: Api) -> None:
    api = seeded
    kid = await member(api, "kid", "Kid")
    for name in (PG13, R, UNRATED, NO_ID, STALE, ADULT_PG, SHOW_MA, SHOW_UNKNOWN, "Red Dock", "First Door"):
        found = await titles_on(kid, "/search", q=name)
        assert name not in found and not (found & (MOVIES | SHOWS) - {G, PG, SHOW_PG}), (name, found)
    assert PG in await titles_on(kid, "/search", q=PG)
    assert SHOW_PG in await titles_on(kid, "/search", q=SHOW_PG)
    assert PG13 in await titles_on(api, "/search", q=PG13)

    home = await kid.get("/home")
    assert home.status_code == 200, home.text
    ids, names = shown(home.json())
    assert names & (MOVIES | SHOWS) <= {G, PG, SHOW_PG} and names & {G, PG}
    assert not ids & {f"tmdb:movie:{n}" for n in (103, 104, 105, 106, 107)}
    owner_home = shown((await api.get("/home")).json())[1]
    assert {PG13, R, UNRATED} <= owner_home


async def test_kid_detail_and_playback_entry(seeded: Api) -> None:
    api = seeded
    kid = await member(api, "kid", "Kid")
    for ref in ("lib:catalog:pg", "tmdb:movie:102", "lib:catalog:show-pg", "lib:catalog:show-pg-e1"):
        r = await kid.get(f"/titles/{ref}")
        assert r.status_code == 200, (ref, r.text)
    detail = (await kid.get("/titles/lib:catalog:show-pg")).json()
    assert "Low Water" in shown(detail)[1]
    blocked = (
        "lib:catalog:pg13",
        "tmdb:movie:103",
        "lib:catalog:r",
        "lib:catalog:unrated",
        "tmdb:movie:105",
        "lib:catalog:noid",
        "lib:catalog:stale",
        "lib:catalog:adultpg",
        "tmdb:movie:107",
        "lib:catalog:show-ma",
        "tmdb:tv:202",
        "lib:catalog:show-ma-e1",
        "lib:catalog:show-ma-x",
        "lib:catalog:show-unk-e1",
        "lib:catalog:orphan",
    )
    for ref in blocked:
        r = await kid.get(f"/titles/{ref}")
        assert r.status_code in (403, 404), (ref, r.status_code, r.text)
        assert PG13 not in r.text and R not in r.text and SHOW_MA not in r.text
        for call in (
            kid.get("/playback/options", params={"id": ref}),
            kid.post("/playback/resolve", json={"id": ref, "recovery_generation": api.c.proxy.recovery_epoch}),
            kid.get("/playback/options", params={"id": f"trailer:{ref}"}),
        ):
            r = await call
            assert r.status_code == 403 and r.json()["code"] == "content_restricted", (ref, r.text)
    # What the ceiling admits is not refused by the policy (the fixture source cannot actually play).
    r = await kid.get("/playback/options", params={"id": "lib:catalog:pg"})
    assert r.status_code != 403, r.text
    # The owner is not in the engine's way at all.
    assert (await api.get("/titles/lib:catalog:r")).status_code == 200
    r = await api.get("/playback/options", params={"id": "lib:catalog:r"})
    assert r.status_code != 403, r.text


async def test_kid_requests_and_rooms_obey(seeded: Api) -> None:
    api = seeded
    kid = await member(api, "kid", "Kid", grants=["rooms.create"])
    await kid.refresh()
    r = await kid.post("/rooms", json={"id": "lib:catalog:r"})
    assert r.status_code == 403 and r.json()["code"] == "content_restricted"
    api.c.requests._details = AsyncMock(return_value=None)  # metadata is not what refuses this
    r = await kid.post("/requests", json={"media_type": "movie", "tmdb_id": 104})
    assert r.status_code == 403 and r.json()["code"] == "content_restricted", r.text


async def test_image_grants_bind_a_restricted_user_and_leave_others_alone(seeded: Api) -> None:
    api = seeded
    policy, auth = api.c.content_policy, api.c.auth
    kid = await member(api, "kid", "Kid")
    principal = await auth.resolve_principal(kid.tokens["access_token"])
    token = active_principal.set(principal)
    try:
        fine = image_payload("catalog", "https://art.example.test/pg.jpg", "lib:catalog:pg", policy)
        hidden = image_payload("catalog", "https://art.example.test/r.jpg", "lib:catalog:r", policy)
        # No exact reference, nothing to check the artwork against: it is left out.
        assert image_payload("catalog", "https://art.example.test/x.jpg", None, policy) is None
    finally:
        active_principal.reset(token)
    assert fine is not None and hidden is not None
    assert (fine["profile"], fine["session"], fine["ref"]) == (kid.profile_id, principal.session_id, "lib:catalog:pg")
    await validate_image_grant(fine, policy, auth)
    with pytest.raises(Forbidden):
        await validate_image_grant(hidden, policy, auth)
    # A grant minted for someone else's session is no use to the kid, and a revoked session ends it.
    with pytest.raises(Forbidden):
        await validate_image_grant({**fine, "ref": "lib:other:pg"}, policy, auth)
    await auth.revoke_session(principal.session_id)
    with pytest.raises(Exception, match="revoked"):
        await validate_image_grant(fine, policy, auth)

    owner = await auth.resolve_principal(api.headers["Authorization"].split()[1])
    token = active_principal.set(owner)
    try:
        plain = image_payload("catalog", "https://art.example.test/r.jpg", "lib:catalog:r", policy)
    finally:
        active_principal.reset(token)
    # Bound to nobody, so no profile or session in it: only where the image is, and until when it is good.
    assert plain == {"s": "catalog", "u": "https://art.example.test/r.jpg", "x": plain["x"]}
    await validate_image_grant(plain, policy, auth)


async def test_stream_grants_are_rechecked_for_a_restricted_user(seeded: Api) -> None:
    from types import SimpleNamespace

    api = seeded
    policy, auth = api.c.content_policy, api.c.auth
    kid = await member(api, "kid", "Kid")
    principal = await auth.resolve_principal(kid.tokens["access_token"])

    def grant(ref: str | None, **kw: Any) -> Any:
        return SimpleNamespace(
            content_ref=ref,
            profile_id=kid.profile_id,
            profile_version=principal.token_version,
            session_id=principal.session_id,
            source_id="catalog",
            policy_at=None,
            policy_until=None,
            live=False,
            **kw,
        )

    await policy.require_grant(grant("lib:catalog:pg"), auth)
    await policy.require_grant(grant("lib:catalog:show-pg-e1"), auth)
    for ref in ("lib:catalog:r", "lib:catalog:show-ma-e1", None):
        with pytest.raises(Forbidden):
            await policy.require_grant(grant(ref), auth)
    # Raising the ceiling reaches a stream already running: the same grant passes on its next check.
    assert (await api.patch(f"/users/{kid.id}", json={"limits": {"rating_ceiling_movie": "R"}})).status_code == 200
    await policy.require_grant(grant("lib:catalog:r"), auth)
    # Someone without a ceiling is never looked at, whatever their grant carries.
    parent = await member(api, "family", "Parent")
    await policy.require_grant(SimpleNamespace(profile_id=parent.profile_id, content_ref=None, session_id=None), auth)


# ---------------------------------------------------------------- changes apply on the next request
async def test_raising_one_users_ceiling_changes_their_next_request_only(seeded: Api) -> None:
    api = seeded
    kid = await member(api, "kid", "Kid")
    sibling = await member(api, "kid", "Sibling")
    assert await movies(kid) == {G, PG} == await movies(sibling)
    raised = await api.patch(f"/users/{kid.id}", json={"limits": {"rating_ceiling_movie": "PG-13"}})
    assert raised.status_code == 200, raised.text
    assert (await kid.get("/browse/movies")).status_code == 401  # the old token is stale
    assert (await kid.refresh()).status_code == 200
    assert await movies(kid) == {G, PG, PG13}
    assert (await kid.get("/titles/lib:catalog:pg13")).status_code == 200
    assert (await kid.get("/titles/lib:catalog:r")).status_code in (403, 404)
    assert await movies(sibling) == {G, PG}
    # Letting unknown ratings through for that one user.
    opened = await api.patch(
        f"/users/{kid.id}", json={"limits": {"rating_ceiling_movie": "PG-13", "allow_unrated": True}}
    )
    assert opened.status_code == 200
    await kid.refresh()
    assert await movies(kid) == {G, PG, PG13, UNRATED, NO_ID, STALE}
    # Lifting every limit takes the engine out of the way entirely.
    lifted = await api.patch(
        f"/users/{kid.id}",
        json={"limits": {"rating_ceiling_movie": None, "rating_ceiling_tv": None, "allow_unrated": True}},
    )
    assert lifted.status_code == 200
    await kid.refresh()
    assert await movies(kid) == MOVIES - {ADULT_PG} and await shows(kid) == SHOWS


async def test_a_role_edit_reaches_every_member(seeded: Api) -> None:
    api = seeded
    kid = await member(api, "kid", "Kid")
    patched = await api.patch(
        "/roles/kid", json={"limits": {"max_streams": 1, "rating_ceiling_movie": "PG-13", "allow_unrated": False}}
    )
    assert patched.status_code == 200, patched.text
    await kid.refresh()
    assert await movies(kid) == {G, PG, PG13}
    assert await shows(kid) == {SHOW_PG, SHOW_MA}  # no TV ceiling now, but unknown ratings are still hidden
    assert await allowed(api, kid, *lib("show-ma", "show-ma-e1", "show-unk")) == set(lib("show-ma", "show-ma-e1"))


# ---------------------------------------------------------------- unknown ratings allowed
async def test_ceiling_with_unknown_ratings_allowed(seeded: Api) -> None:
    api = seeded
    teen = await member(api, "family", "Teen", limits={"rating_ceiling_movie": "PG-13", "rating_ceiling_tv": "TV-14"})
    assert await movies(teen) == {G, PG, PG13, UNRATED, NO_ID, STALE}
    assert await shows(teen) == {SHOW_PG, SHOW_UNKNOWN}
    refs = lib("show-pg-e1", "show-ma-e1", "show-ma-x", "show-ma-s1", "show-unk-e1", "orphan", "adultpg", "r")
    assert await allowed(api, teen, *refs) == set(lib("show-pg-e1", "show-unk-e1", "orphan"))
    canonical = [f"tmdb:movie:{n}" for n in (101, 103, 104, 105, 107, 999)] + ["tmdb:tv:201", "tmdb:tv:202"]
    assert await allowed(api, teen, *canonical) == {
        "tmdb:movie:101",
        "tmdb:movie:103",
        "tmdb:movie:105",
        "tmdb:movie:999",
        "tmdb:tv:201",
    }
    assert (await teen.get("/titles/lib:catalog:show-ma-e1")).status_code in (403, 404)
    assert (await teen.get("/titles/lib:catalog:unrated")).status_code == 200
    # Metadata discovery is the exception: a title nobody has looked up yet is not "unrated".
    assert set(await api.c.content_policy.allowed_refs(teen.profile_id, canonical, proven=True)) == {
        "tmdb:movie:101",
        "tmdb:movie:103",
        "tmdb:tv:201",
    }
    meta = AsyncMock()
    meta.enabled = True
    found = [MetaTitle(n, "movie", f"Film {n}", 2020) for n in (101, 103, 104, 105, 999)]
    meta.discover.return_value = (found, 5)
    api.c.catalog.meta = meta
    page = await teen.get("/discover/movies", params=ALL_LANGUAGES)
    assert page.status_code == 200, page.text
    assert [t["id"] for t in page.json()["tiles"]] == ["tmdb:movie:101", "tmdb:movie:103"]
    assert page.json()["total"] is None


async def test_adult_rows_are_never_admitted_under_a_ceiling(seeded: Api) -> None:
    api = seeded
    # The widest ceiling there is, and the adult permission on top.
    viewer = await member(
        api, "family", "Viewer", grants=["watch.adult"], limits={"rating_ceiling_movie": "NC-17"}, pin="1234"
    )
    await viewer.refresh()
    assert (await viewer.post("/auth/adult-unlock", json={"pin": "1234"})).status_code == 200
    assert ADULT_PG not in await titles_on(viewer, "/browse/movies", include_adult="true")
    assert (await viewer.get("/titles/lib:catalog:adultpg")).status_code in (403, 404)
    assert await allowed(api, viewer, "lib:catalog:adultpg", "tmdb:movie:107", "lib:catalog:r") == {"lib:catalog:r"}
    # Without a ceiling the same user reaches it the usual, PIN-unlocked way.
    assert (await api.patch(f"/users/{viewer.id}", json={"limits": {}})).status_code == 200
    await viewer.refresh()
    assert (await viewer.post("/auth/adult-unlock", json={"pin": "1234"})).status_code == 200
    assert ADULT_PG in await titles_on(viewer, "/browse/movies", include_adult="true")


# ---------------------------------------------------------------- live
async def test_channels_follow_the_programme_on_air(seeded: Api) -> None:
    api = seeded
    policy = api.c.content_policy
    channels = ["ch:catalog:kids", "ch:catalog:news", "ch:catalog:late"]
    kid = await member(api, "kid", "Kid")
    assert await allowed(api, kid, *channels) == {"ch:catalog:kids"}
    listed = await kid.get("/channels", params=ALL_LANGUAGES)
    assert listed.status_code == 200, listed.text
    assert {c["name"] for c in listed.json()["channels"]} == {"Kids"}
    teen = await member(api, "family", "Teen", limits={"rating_ceiling_tv": "TV-14"})
    assert await allowed(api, teen, *channels) == {"ch:catalog:kids", "ch:catalog:news"}
    assert {c["name"] for c in (await teen.get("/channels", params=ALL_LANGUAGES)).json()["channels"]} == {
        "Kids",
        "News",
    }
    assert {c["name"] for c in (await api.get("/channels", params=ALL_LANGUAGES)).json()["channels"]} == {
        "Kids",
        "News",
        "Late",
    }
    # A stretch of a channel: unknown guide data passes only where unknown ratings do.
    start, stop = NOW - timedelta(minutes=5), NOW + timedelta(minutes=5)
    await policy.require_channel_interval(teen.profile_id, "ch:catalog:news", start, stop)
    await policy.require_channel_interval(kid.profile_id, "ch:catalog:kids", start, stop)
    for who, ref in ((kid, "ch:catalog:news"), (kid, "ch:catalog:late"), (teen, "ch:catalog:late")):
        with pytest.raises(Forbidden):
            await policy.require_channel_interval(who.profile_id, ref, start, stop)
    r = await kid.get("/playback/options", params={"id": "ch:catalog:late"})
    assert r.status_code == 403 and r.json()["code"] == "content_restricted"


async def test_a_catch_up_programme_is_no_way_past_the_gate(seeded: Api) -> None:
    from sqlalchemy import select

    api = seeded
    kid = await member(api, "kid", "Kid")
    async with api.c.db.session() as s:
        rows = {p.title: f"prog:{p.id}" for p in (await s.execute(select(Programme))).scalars()}
    generation = api.c.proxy.recovery_epoch

    async def resolve(*panes: dict[str, Any]) -> Any:
        body: dict[str, Any] = {"recovery_generation": generation}
        body.update(panes[0] if len(panes) == 1 else {"panes": list(panes)})
        return await kid.post("/playback/resolve" if len(panes) == 1 else "/playback/multiview", json=body)

    def refused(r: Any) -> bool:
        return bool(r.status_code == 403 and r.json()["code"] == "content_restricted")

    allowed_programme, refused_programme = rows["Morning Songs"], rows["After Dark"]
    # Naming an admitted programme next to a title does not excuse the title.
    assert refused(await resolve({"id": "lib:catalog:r", "catchup_programme_id": allowed_programme}))
    assert refused(await resolve({"id": "ch:catalog:kids", "catchup_programme_id": refused_programme}))
    # Nor does a second pane asking for the same channel as a past programme's carrier.
    live = {"id": "ch:catalog:late"}
    carrier = {"id": "ch:catalog:late", "catchup_programme_id": allowed_programme}
    assert refused(await resolve(live, carrier)) and refused(await resolve(carrier, live))
    # A rewind is judged by what aired at that moment: nothing rated did, an hour ago.
    earlier = (NOW - timedelta(hours=1)).isoformat()
    assert refused(await resolve({"id": "ch:catalog:kids", "rewind_at": earlier}))
    # What the ceiling admits gets past the policy (the fixture source then has nothing to play).
    for fine in ({"id": "ch:catalog:kids"}, {"id": "ch:catalog:kids", "catchup_programme_id": allowed_programme}):
        r = await resolve(fine)
        assert not refused(r), r.text
    # A past programme is a way onto the channel that carried it and no other: the resolver
    # judges the channel by what it aired then, which on "late" was over the ceiling.
    assert refused(await resolve({"id": "ch:catalog:late", "catchup_programme_id": allowed_programme}))


def test_limits_restrict_helper() -> None:
    from app.services.content_policy import rating_key, restricts

    assert not restricts(Limits()) and not restricts(Limits(max_streams=1))
    assert restricts(Limits(rating_ceiling_movie="R")) and restricts(Limits(rating_ceiling_tv="TV-MA"))
    assert restricts(Limits(allow_unrated=False))
    assert rating_key(None) is None
    assert rating_key(type("P", (), {"limits": Limits(rating_ceiling_movie="PG")})()) == ("PG", None, True)
