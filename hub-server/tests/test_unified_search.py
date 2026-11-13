"""One search across titles, channels and guide broadcasts: matching, ranking, caps and the gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy import event, insert

from app.domain.entities import MetaTitle
from app.infra.models import AgeClassification, Channel, ChannelGroup, Programme, Source, Title
from app.services.classification import classification_record, summarize
from app.services.ids import normalise_title
from app.services.search import (
    CHANNEL_CAP,
    PROGRAMME_CAP,
    PROGRAMME_HORIZON,
    TITLE_CAP,
    fold,
    split_year,
    title_rank,
)
from tests.conftest import Api
from tests.people import Member, member
from tests.test_adult_surfaces import unlock_adult

ALL = {"language": "all"}


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def channel(ext: str, name: str, number: int, *, epg: bool = True, source: str = "catalog") -> Channel:
    return Channel(
        source_id=source,
        ext_id=ext,
        name=name,
        norm_name=normalise_title(name),
        number=number,
        epg_id=ext if epg else None,
        group_id="g",
        group_name="Group",
        logo=f"https://art.example.test/{ext}.png",
    )


def programme(epg: str, name: str, start: timedelta, minutes: int = 60, **kw: Any) -> Programme:
    begins = now() + start
    return Programme(
        feed="catalog", epg_channel=epg, title=name, start=begins, stop=begins + timedelta(minutes=minutes), **kw
    )


def title(ext: str, name: str, *, kind: str = "vod", **kw: Any) -> Title:
    return Title(source_id="catalog", ext_id=ext, kind=kind, title=name, norm_title=normalise_title(name), **kw)


async def seed(api: Api, *rows: Any) -> None:
    async with api.c.db.session() as s:
        s.add(Source(id="catalog", name="Catalog", type="xtream", config_enc="unused"))
        await s.flush()
        s.add(ChannelGroup(id="g", source_id="catalog", name="Group"))
        await s.flush()
        s.add_all(rows)
        await s.commit()


async def search(who: Api | Member, q: str, **params: Any) -> dict[str, Any]:
    response = await who.get("/search", params={"q": q, **ALL, **params})
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def names(found: dict[str, Any], section: str) -> list[str]:
    return [row["title"] if section != "channels" else row["name"] for row in found[section]]


# ---------------------------------------------------------------- the pure rules
def test_fold_ignores_diacritics_punctuation_ampersand_and_a_leading_article() -> None:
    assert fold("Pokémon: The First Movie") == "pokemon the first movie"
    assert fold("  Law & Order ") == fold("law and order") == "law and order"
    assert fold("The Mill") == fold("mill") == "mill"
    assert fold("S.W.A.T.") == "s w a t"
    assert fold("The") == "the"


def test_a_trailing_year_is_read_as_a_year_only_after_other_words() -> None:
    assert split_year("the mill 2023") == ("the mill", 2023)
    assert split_year("The Mill (2023)") == ("The Mill", 2023)
    assert split_year("1917") == ("1917", None)
    assert split_year("2001 a space odyssey") == ("2001 a space odyssey", None)
    assert split_year("channel 235") == ("channel 235", None)


def test_title_rank_orders_exact_prefix_word_prefix_then_contains() -> None:
    ranks = [title_rank("mill", [], name) for name in ("mill", "millstone", "old mill road", "windmill", "harbour")]
    assert ranks[:4] == [0, 1, 2, 3] and ranks[4] is None
    # Every word present in another order still matches, after any run of the phrase itself.
    assert title_rank("road mill", ["road", "mill"], "old mill road") == 4
    assert title_rank("mill road", ["mill", "road"], "old mill road") == 2


# ---------------------------------------------------------------- nothing for a letter
async def test_empty_and_one_character_queries_return_nothing_without_reading_the_catalog(api: Api) -> None:
    await seed(api, channel("a", "A", 1), title("a", "A"))
    fake = AsyncMock()
    fake.enabled = True
    api.c.catalog.meta = fake
    selects: list[str] = []

    def before(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        if any(f" {table}" in statement for table in ("title", "channel", "programme")):
            selects.append(statement)

    event.listen(api.c.db.engine.sync_engine, "before_cursor_execute", before)
    try:
        for q in ("", " ", "a", " a "):
            response = await api.get("/search", params={"q": q})
            assert response.status_code == 200, response.text
            found = response.json()
            assert found["results"] == found["channels"] == found["programmes"] == []
            assert found["play_here"] == found["open_in_app"] == []
            assert found["sections"] == {
                name: {"count": 0, "more": False} for name in ("titles", "channels", "programmes")
            }
        assert (await api.get("/search")).status_code == 200
    finally:
        event.remove(api.c.db.engine.sync_engine, "before_cursor_execute", before)
    assert selects == [] and not fake.search.called


# ---------------------------------------------------------------- channels
async def test_channels_match_on_clean_name_and_number_with_the_exact_number_first(api: Api) -> None:
    await seed(
        api,
        channel("k1", "Korvo HD", 12),
        channel("k2", "Korvo 2 HD", 13),
        channel("kd", "XX | Korvo Deportes ᴴᴰ", 14),
        channel("box", "Movie Box 235", 40),
        channel("hal", "Halden News", 235),
        channel("oth", "Tessel Sport", 2350),
    )
    found = await search(api, "korvo")
    assert names(found, "channels") == ["Korvo HD", "Korvo 2 HD", "XX | Korvo Deportes ᴴᴰ"]
    assert found["sections"]["channels"] == {"count": 3, "more": False}
    # The exact name is the best match, so channels lead the page.
    assert found["order"][0] == "channels"

    numbered = await search(api, "235")
    assert names(numbered, "channels") == ["Halden News", "Movie Box 235"]
    assert numbered["order"][0] == "channels"
    assert names(await search(api, "KORVO  2"), "channels") == ["Korvo 2 HD"]
    assert names(await search(api, "sport"), "channels") == ["Tessel Sport"]
    # Part of a word inside a name is found too.
    assert names(await search(api, "alden"), "channels") == ["Halden News"]
    # Digits that cannot be a channel number are only text, never an error.
    assert (await search(api, "9" * 40))["channels"] == []
    assert names(await search(api, "²³"), "channels") == ["Movie Box 235"]
    assert names(await search(api, "٢٣٥"), "channels")[0] == "Halden News"


async def test_event_slot_channels_are_found_by_their_shown_name(api: Api) -> None:
    kickoff = now() + timedelta(hours=3)
    slot = f"(XX) (VANTIX+ 001) | Field Hockey: Northfield at Lakeside ({kickoff:%Y-%m-%d %H:%M:%S})"
    await seed(api, channel("v1", slot, 501, epg=False))
    found = await search(api, "vantix")
    assert names(found, "channels") == ["VANTIX+ 001"]
    game = await search(api, "northfield")
    assert names(game, "programmes") == ["Northfield at Lakeside"]
    entry = game["programmes"][0]
    assert entry["id"].startswith("prog:slot-") and entry["channel_name"] == "VANTIX+ 001"
    assert entry["on_now"] is False and entry["is_sport"] is True and entry["teams"] == ["Northfield", "Lakeside"]
    assert entry["channel_id"] == "ch:catalog:v1" and entry["channel_number"] == 501


# ---------------------------------------------------------------- programmes
async def test_programmes_airing_now_or_soon_ordered_on_now_first_then_by_start(api: Api) -> None:
    await seed(
        api,
        channel("one", "Halden One", 1),
        channel("two", "Halden Two", 2),
        channel("three", "Tessel Sport", 3),
        programme("one", "Harbour Patrol", timedelta(minutes=-10)),
        programme("two", "Harbour Patrol: Night Shift", timedelta(hours=5)),
        programme("three", "The Harbour Patrol Story", timedelta(hours=2)),
        programme("one", "Harbour Patrol", timedelta(hours=-3)),  # over
        programme("two", "Harbour Patrol", PROGRAMME_HORIZON + timedelta(hours=1)),  # too far ahead
        programme("three", "Rowing: Kestrels vs Badgers", timedelta(minutes=-30), category="Sports"),
        programme("one", "Gardening Hour", timedelta(hours=1)),
    )
    found = await search(api, "harbour patrol")
    assert names(found, "programmes") == [
        "Harbour Patrol",
        "The Harbour Patrol Story",
        "Harbour Patrol: Night Shift",
    ]
    first = found["programmes"][0]
    assert first["on_now"] is True and first["id"].startswith("prog:")
    assert (first["channel_id"], first["channel_number"], first["channel_name"]) == ("ch:catalog:one", 1, "Halden One")
    assert first["channel_logo"] == "https://art.example.test/one.png"
    assert datetime.fromisoformat(first["start"]) < now() < datetime.fromisoformat(first["stop"])
    assert [entry["on_now"] for entry in found["programmes"]] == [True, False, False]
    assert found["sections"]["programmes"] == {"count": 3, "more": False}
    # A broadcast by its exact title is the best match: its section leads (it does anyway).
    assert found["order"] == ["programmes", "channels", "titles"]

    sport = (await search(api, "kestrels"))["programmes"]
    assert [(entry["is_sport"], entry["teams"]) for entry in sport] == [(True, ["Kestrels", "Badgers"])]
    # Words in the middle of a word are not a broadcast match: "our" is not "Harbour".
    assert names(await search(api, "our"), "programmes") == []
    assert names(await search(api, "garden"), "programmes") == ["Gardening Hour"]


async def test_placeholder_programmes_are_never_results(api: Api) -> None:
    await seed(
        api,
        channel("one", "Halden One HD", 1),
        programme("one", "Halden One HD", timedelta(minutes=-10)),
        programme("one", "  halden one hd ", timedelta(hours=1)),
        programme("one", "Halden One Tonight", timedelta(hours=2)),
    )
    found = await search(api, "halden one")
    assert names(found, "programmes") == ["Halden One Tonight"]
    assert names(found, "channels") == ["Halden One HD"]


async def test_one_entry_per_title_and_start_with_every_channel_as_a_feed(api: Api) -> None:
    await seed(
        api,
        channel("b", "Tessel Sport 2", 22),
        channel("a", "Tessel Sport 1", 21),
        channel("c", "Halden One", 30),
        programme("a", "Regatta Live", timedelta(minutes=-5)),
        programme("b", "Regatta Live", timedelta(minutes=-5)),
        programme("c", "Regatta Live", timedelta(hours=4)),
        programme("c", "regatta  LIVE", timedelta(hours=8)),
        programme("c", "Regatta Live", timedelta(hours=8)),  # the guide lists it twice
    )
    found = await search(api, "regatta")
    assert [len(entry["feeds"]) for entry in found["programmes"]] == [2, 1, 1]
    simulcast = found["programmes"][0]
    # The lowest channel number speaks for the broadcast; the rest are listed as feeds.
    assert simulcast["channel_id"] == "ch:catalog:a" and simulcast["id"] == simulcast["feeds"][0]["programme_id"]
    assert [(feed["channel_id"], feed["channel_number"], feed["channel_name"]) for feed in simulcast["feeds"]] == [
        ("ch:catalog:a", 21, "Tessel Sport 1"),
        ("ch:catalog:b", 22, "Tessel Sport 2"),
    ]
    assert found["sections"]["programmes"]["count"] == 3


# ---------------------------------------------------------------- titles
async def test_title_ranking_for_a_short_query(api: Api) -> None:
    await seed(
        api,
        title("road", "Old Mill Road"),
        title("wind", "Windmill"),
        title("stone", "Millstone"),
        title("mill", "The Mill"),
        title("other", "Harbour Lights"),
    )
    found = await search(api, "mill")
    assert names(found, "results") == ["The Mill", "Millstone", "Old Mill Road", "Windmill"]
    assert found["sections"]["titles"] == {"count": 4, "more": False}
    assert names(await search(api, "the mill"), "results")[0] == "The Mill"
    assert names(await search(api, "mi"), "results") == ["The Mill", "Millstone", "Old Mill Road"]


async def test_diacritics_punctuation_and_ampersand_are_ignored(api: Api) -> None:
    await seed(api, title("cafe", "Café Lumière"), title("law", "Tide & Timber"), title("swat", "Mr. Halden's Day"))
    assert names(await search(api, "cafe lumiere"), "results") == ["Café Lumière"]
    assert names(await search(api, "tide and timber"), "results") == ["Tide & Timber"]
    assert names(await search(api, "Tide & Timber"), "results") == ["Tide & Timber"]
    assert names(await search(api, "mr haldens day"), "results") == []
    assert names(await search(api, "mr halden's day!"), "results") == ["Mr. Halden's Day"]


async def test_a_year_in_the_query_narrows(api: Api) -> None:
    await seed(
        api,
        title("new", "The Mill", year=2023),
        title("old", "The Mill", year=1999),
        title("plain", "The Mill"),
        title("far", "Mill Runner 2049", year=2017),
    )
    assert {row["id"] for row in (await search(api, "the mill"))["results"]} == {
        "lib:catalog:new",
        "lib:catalog:old",
        "lib:catalog:plain",
        "lib:catalog:far",
    }
    assert [row["id"] for row in (await search(api, "the mill 2023"))["results"]] == ["lib:catalog:new"]
    assert [row["id"] for row in (await search(api, "The Mill (1999)"))["results"]] == ["lib:catalog:old"]
    # A number that belongs to the title still finds it.
    assert [row["id"] for row in (await search(api, "mill runner 2049"))["results"]] == ["lib:catalog:far"]


async def test_owned_titles_come_before_metadata_only_ones_at_equal_rank(api: Api) -> None:
    await seed(api, title("owned", "Harbour Lights", year=2020), title("prefix", "Harbour Lights Forever", year=2021))
    fake = AsyncMock()
    fake.enabled = True
    fake.search.return_value = [
        MetaTitle(tmdb_id=71, media_type="movie", title="Harbour Lights", year=1987, popularity=99.0),
        MetaTitle(tmdb_id=72, media_type="tv", title="Harbour Lights", year=2005, popularity=50.0),
    ]
    fake.details.return_value = None
    fake.watch_providers.return_value = []
    api.c.catalog.meta = fake
    api.c.availability.handoff_badges = AsyncMock(return_value=[])
    found = await search(api, "harbour lights")
    assert [row["id"] for row in found["results"]] == [
        "lib:catalog:owned",
        "tmdb:movie:71",
        "tmdb:tv:72",
        "lib:catalog:prefix",
    ]
    assert [bool(row["sources"]) for row in found["results"]] == [True, False, False, True]
    # The provider is asked for the title, not for the year typed after it.
    await search(api, "harbour lights 1987")
    assert fake.search.call_args.args[0] == "harbour lights"
    assert [row["id"] for row in (await search(api, "harbour lights 1987"))["results"]] == ["tmdb:movie:71"]


async def test_a_series_is_one_result_by_its_name_never_a_row_per_episode(api: Api) -> None:
    await seed(
        api,
        title("show", "Tide Pool", kind="series"),
        title("s1", "Tide Pool Season 1", kind="season", parent_ext_id="show", season_number=1),
        *(
            title(f"e{n}", f"Tide Pool S01E{n:02}", kind="episode", parent_ext_id="s1", episode_number=n)
            for n in range(1, 9)
        ),
    )
    found = await search(api, "tide pool")
    assert [(row["id"], row["kind"]) for row in found["results"]] == [("lib:catalog:show", "series")]
    assert found["sections"]["titles"]["count"] == 1
    # A client that asks for episodes by kind still gets them.
    assert len((await search(api, "tide pool", kinds="episode"))["results"]) == 8


# ---------------------------------------------------------------- caps
async def test_each_section_is_capped_counted_and_can_be_expanded(api: Api) -> None:
    await seed(api)
    async with api.c.db.session() as s:
        await s.execute(
            insert(Title),
            [
                dict(
                    source_id="catalog",
                    ext_id=f"t{n}",
                    kind="vod",
                    title=f"Lantern {n:03}",
                    norm_title=f"lantern {n:03}",
                )
                for n in range(TITLE_CAP + 15)
            ],
        )
        s.add_all(channel(f"c{n}", f"Lantern TV {n:02}", 100 + n) for n in range(CHANNEL_CAP + 5))
        s.add_all(
            programme(f"c{n % 3}", f"Lantern Tales {n:02}", timedelta(minutes=30 * n - 10), minutes=30)
            for n in range(PROGRAMME_CAP + 6)
        )
        await s.commit()
    found = await search(api, "lantern")
    assert (len(found["results"]), len(found["channels"]), len(found["programmes"])) == (
        TITLE_CAP,
        CHANNEL_CAP,
        PROGRAMME_CAP,
    )
    assert found["sections"] == {
        "titles": {"count": TITLE_CAP + 15, "more": True},
        "channels": {"count": CHANNEL_CAP + 5, "more": True},
        "programmes": {"count": PROGRAMME_CAP + 6, "more": True},
    }
    assert names(found, "channels")[:2] == ["Lantern TV 00", "Lantern TV 01"]
    assert names(found, "programmes")[:2] == ["Lantern Tales 00", "Lantern Tales 01"]

    more = await search(api, "lantern", expand="channels,programmes")
    assert (len(more["results"]), len(more["channels"]), len(more["programmes"])) == (
        TITLE_CAP,
        CHANNEL_CAP + 5,
        PROGRAMME_CAP + 6,
    )
    assert more["sections"]["channels"] == {"count": CHANNEL_CAP + 5, "more": False}
    assert more["sections"]["programmes"]["more"] is False and more["sections"]["titles"]["more"] is True
    # The first rows of an expanded section are the rows it had: the client extends in place.
    assert names(more, "channels")[:CHANNEL_CAP] == names(found, "channels")
    assert names(more, "programmes")[:PROGRAMME_CAP] == names(found, "programmes")
    everything = await search(api, "lantern", expand="titles")
    assert len(everything["results"]) == TITLE_CAP + 15 and everything["sections"]["titles"]["more"] is False
    assert names(everything, "results")[:TITLE_CAP] == names(found, "results")
    assert (await api.get("/search", params={"q": "lantern", "expand": "everything"})).status_code == 422


# ---------------------------------------------------------------- the gates
def rated_programme(epg: str, name: str, code: str | None) -> Programme:
    rating = summarize("xmltv", "programme", [classification_record("US", "US-TVPG", code)]) if code else {}
    return programme(epg, name, timedelta(minutes=-20), age_evidence=rating)


def rated(key: str, code: str) -> AgeClassification:
    movie = key.startswith("tmdb:movie:")
    record = classification_record("US", "US-MPA" if movie else "US-TVPG", code)
    evidence = {**summarize("tmdb", "movie" if movie else "series", [record]), "subject_key": key}
    return AgeClassification(subject_key=key, provider="tmdb", evidence=evidence, observed_at=now())


async def test_a_kid_profile_finds_only_what_its_ceiling_admits_in_every_section(api: Api) -> None:
    await seed(
        api,
        title("pg", "Lantern Song", tmdb_id=102),
        title("r", "Lantern Shift", tmdb_id=104),
        title("unrated", "Lantern Reel", tmdb_id=105),
        rated("tmdb:movie:102", "PG"),
        rated("tmdb:movie:104", "R"),
        channel("kids", "Lantern Kids", 1),
        channel("late", "Lantern Late", 2),
        channel("plain", "Lantern Plain", 3),
        rated_programme("kids", "Lantern Mornings", "TV-G"),
        rated_programme("late", "Lantern After Dark", "TV-MA"),
        rated_programme("plain", "Lantern Headlines", None),
    )
    owner = await search(api, "lantern")
    assert len(owner["results"]) == len(owner["channels"]) == len(owner["programmes"]) == 3
    kid = await member(api, "kid", "Kid")
    found = await search(kid, "lantern")
    assert [row["id"] for row in found["results"]] == ["tmdb:movie:102"]
    assert names(found, "channels") == ["Lantern Kids"]
    assert names(found, "programmes") == ["Lantern Mornings"]
    # Counts never give away what was left out.
    assert found["sections"] == {name: {"count": 1, "more": False} for name in ("titles", "channels", "programmes")}


async def test_a_metadata_only_title_over_the_ceiling_is_left_out_and_not_counted(api: Api) -> None:
    await seed(api, title("pg", "Lantern Song", tmdb_id=102), rated("tmdb:movie:102", "PG"))
    fake = AsyncMock()
    fake.enabled = True
    fake.search.return_value = [MetaTitle(tmdb_id=900, media_type="movie", title="Lantern Shift", year=2001)]
    fake.details.return_value = None
    api.c.catalog.meta = fake
    api.c.availability.handoff_badges = AsyncMock(return_value=[])
    assert len((await search(api, "lantern"))["results"]) == 2
    kid = await member(api, "kid", "Kid")
    found = await search(kid, "lantern")
    assert [row["id"] for row in found["results"]] == ["tmdb:movie:102"]
    assert found["sections"]["titles"] == {"count": 1, "more": False}


async def test_without_watch_live_there_are_no_channels_or_broadcasts(api: Api) -> None:
    await seed(
        api,
        title("film", "Lantern Song"),
        channel("one", "Lantern One", 7),
        programme("one", "Lantern Tales", timedelta(minutes=-5)),
    )
    without = await member(api, "friend", name="Without", denials=["watch.live"])
    found = await search(without, "lantern")
    assert found["channels"] == found["programmes"] == []
    assert found["sections"]["channels"] == found["sections"]["programmes"] == {"count": 0, "more": False}
    assert names(found, "results") == ["Lantern Song"]
    assert found["order"] == ["programmes", "channels", "titles"]
    assert (await search(without, "7"))["channels"] == []
    # And without watch.vod the IPTV title goes too, while live stays.
    live_only = await member(api, "friend", name="Live", denials=["watch.vod"])
    found = await search(live_only, "lantern")
    assert found["results"] == [] and names(found, "channels") == ["Lantern One"]
    assert names(found, "programmes") == ["Lantern Tales"]


async def test_adult_titles_need_the_permission_and_the_unlock(api: Api) -> None:
    await seed(api, title("general", "Velvet Harbour"), title("adult", "Velvet Room", adult=True))
    flag = {"include_adult": "true"}
    assert names(await search(api, "velvet"), "results") == ["Velvet Harbour"]
    # Asking is not enough until this session is unlocked.
    assert names(await search(api, "velvet", **flag), "results") == ["Velvet Harbour"]
    await unlock_adult(api)
    assert set(names(await search(api, "velvet", **flag), "results")) == {"Velvet Harbour", "Velvet Room"}
    assert names(await search(api, "velvet"), "results") == ["Velvet Harbour"]
    guest = await member(api, "friend", name="Guest")
    found = await search(guest, "velvet", **flag)
    assert names(found, "results") == ["Velvet Harbour"] and found["sections"]["titles"]["count"] == 1


# ---------------------------------------------------------------- languages
async def test_the_profile_languages_come_first_and_a_section_with_nothing_in_them_looks_in_the_rest(api: Api) -> None:
    def spoken(row: Any, language: str) -> Any:
        row.language = language
        return row

    await seed(
        api,
        spoken(title("en", "Lantern Song"), "eng"),
        spoken(title("fr", "Lantern Chanson"), "fra"),
        spoken(title("only", "Velours Bleu"), "fra"),
        spoken(channel("news", "Lantern News", 1), "eng"),
        spoken(channel("info", "Lantern Info", 2), "fra"),
        spoken(channel("cine", "Cinq Sur Cinq", 30), "fra"),
        programme("news", "Lantern Tonight", timedelta(minutes=-5)),
        programme("info", "Lantern Ce Soir", timedelta(minutes=-5)),
        programme("cine", "Minuit Passe", timedelta(minutes=-5)),
    )
    assert (await api.patch(f"/profiles/{api.profile_id}", json={"languages": ["eng"]})).status_code == 200

    async def mine(q: str) -> dict[str, Any]:
        response = await api.get("/search", params={"q": q})
        assert response.status_code == 200, response.text
        found: dict[str, Any] = response.json()
        return found

    # Something in the profile's languages: only that, as the listings show it.
    found = await mine("lantern")
    assert names(found, "results") == ["Lantern Song"] and names(found, "channels") == ["Lantern News"]
    assert names(found, "programmes") == ["Lantern Tonight"]
    assert found["sections"]["channels"] == {"count": 1, "more": False}
    # Nothing in them: the title, the channel and the broadcast asked for by name are still found.
    assert names(await mine("velours bleu"), "results") == ["Velours Bleu"]
    assert names(await mine("cinq sur cinq"), "channels") == ["Cinq Sur Cinq"]
    assert names(await mine("30"), "channels") == ["Cinq Sur Cinq"]
    assert names(await mine("minuit"), "programmes") == ["Minuit Passe"]
    # Each section on its own: an English title does not hide the only channel of that name.
    mixed = await mine("lantern info")
    assert names(mixed, "channels") == ["Lantern Info"] and mixed["results"] == []
    # A language asked for outright is taken at its word.
    strict = (await api.get("/search", params={"q": "cinq sur cinq", "language": "eng"})).json()
    assert strict["channels"] == [] and strict["sections"]["channels"]["count"] == 0
    assert len((await search(api, "lantern"))["channels"]) == 2


# ---------------------------------------------------------------- cost
async def test_the_number_of_queries_does_not_grow_with_the_catalog(api: Api) -> None:
    await seed(api)
    api.c.catalog.meta = AsyncMock(enabled=False)

    async def grow(offset: int, titles: int, channels: int, programmes: int) -> None:
        async with api.c.db.session() as s:
            await s.execute(
                insert(Title),
                [
                    dict(
                        source_id="catalog",
                        ext_id=f"t{offset + n}",
                        kind=("vod", "series")[n % 2],
                        title=f"Lantern {('Song', 'Road', 'Tales')[n % 3]} {offset + n}",
                        norm_title=f"lantern {('song', 'road', 'tales')[n % 3]} {offset + n}",
                    )
                    for n in range(titles)
                ],
            )
            await s.execute(
                insert(Channel),
                [
                    dict(
                        source_id="catalog",
                        ext_id=f"c{offset + n}",
                        name=f"Lantern TV {offset + n}",
                        norm_name=f"lantern tv {offset + n}",
                        number=offset + n + 1,
                        epg_id=f"c{offset + n}",
                        group_id="g",
                        group_name="Group",
                    )
                    for n in range(channels)
                ],
            )
            begins = now()
            await s.execute(
                insert(Programme),
                [
                    dict(
                        feed="catalog",
                        epg_channel=f"c{offset + n % max(channels, 1)}",
                        title=f"Lantern Tales {n % 50}",
                        start=begins + timedelta(minutes=30 * (n // max(channels, 1)) - 10),
                        stop=begins + timedelta(minutes=30 * (n // max(channels, 1)) + 20),
                    )
                    for n in range(programmes)
                ],
            )
            await s.commit()

    async def cost(q: str) -> tuple[int, dict[str, Any]]:
        statements: list[str] = []

        def before(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(api.c.db.engine.sync_engine, "before_cursor_execute", before)
        try:
            found = await search(api, q)
        finally:
            event.remove(api.c.db.engine.sync_engine, "before_cursor_execute", before)
        return len(statements), found

    await grow(0, titles=30, channels=10, programmes=40)
    small, first = await cost("lantern")
    await grow(1000, titles=4000, channels=600, programmes=4000)
    # A different spelling of the same query: the first answer is cached, this one is computed.
    large, second = await cost("LANTERN ")
    assert first["sections"]["titles"]["count"] == 30 and second["sections"]["titles"]["more"] is True
    assert second["sections"]["channels"]["count"] == 610
    assert (len(second["results"]), len(second["channels"]), len(second["programmes"])) == (
        TITLE_CAP,
        CHANNEL_CAP,
        PROGRAMME_CAP,
    )
    assert large == small, (small, large)
    assert small <= 20
