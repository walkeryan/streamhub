from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.infra.models import Channel, Programme, Title
from tests.conftest import Api
from tests.test_large_catalog_filters import seed_catalog


async def test_actual_genres_combine_category_and_scope_full_catalog(api: Api) -> None:
    await seed_catalog(api, title_count=12000)
    async with api.c.db.session() as session:
        await session.execute(update(Title).values(genres=["Drama", "Drama"]))
        await session.execute(update(Title).where(Title.ext_id == "11997").values(genres=["Horror", "Drama"]))
        await session.execute(update(Title).where(Title.source_id == "disabled").values(genres=["Disabled"]))
        await session.commit()
    facets = (await api.get("/browse/movies/genres")).json()
    assert facets == [{"name": "drama", "count": 4000}, {"name": "horror", "count": 1}]
    assert (await api.get("/browse/movies/genres", params={"language": "all"})).json()[0]["count"] == 12000
    assert (await api.get("/browse/movies/genres", params={"source_id": "disabled"})).json() == []
    selected = (await api.get("/browse/movies", params={"genre": "Horror", "category": "UK| Films"})).json()
    assert selected["total"] == 1 and selected["tiles"][0]["id"] == "lib:catalog:11997"
    assert (
        await api.get("/browse/movies", params={"genre": "Horror", "category": "FR| Films", "language": "all"})
    ).json()["total"] == 0
    assert (await api.get("/browse/movies", params={"genre": "UK| Films"})).json()["total"] == 0
    assert (await api.get("/browse/movies/genres", params={"q": "Film 011997"})).json() == [
        {"name": "drama", "count": 1},
        {"name": "horror", "count": 1},
    ]


async def test_country_independent_language_unknown_and_guide(api: Api) -> None:
    await seed_catalog(api, channel_count=6)
    now = datetime.now(UTC)
    async with api.c.db.session() as session:
        for ext, country in [("0", "US"), ("1", "US"), ("3", "GB")]:
            await session.execute(update(Channel).where(Channel.ext_id == ext).values(country=country))
        session.add_all(
            [
                Programme(
                    feed="catalog",
                    epg_channel=f"epg-{i}",
                    title=f"Show {i}",
                    start=now - timedelta(minutes=10),
                    stop=now + timedelta(minutes=20),
                )
                for i in range(6)
            ]
        )
        await session.commit()
    us = (await api.get("/channels", params={"country": "us"})).json()
    assert [ch["ext_id"] for ch in us["channels"]] == ["0"]
    assert (await api.get("/channels", params={"country": "US", "language": "all"})).json()["total"] == 2
    unknown = (await api.get("/channels", params={"country": "und", "language": "all"})).json()
    assert {ch["ext_id"] for ch in unknown["channels"]} == {"2", "5"}
    groups = (await api.get("/channels/groups", params={"country": "US", "language": "all"})).json()
    assert {g["name"]: g["channel_count"] for g in groups} == {"UK| News": 1, "FR| News": 1}
    grid = (
        await api.get("/epg", params={"country": "US", "channel_ids": "ch:catalog:0,ch:catalog:1,ch:catalog:3"})
    ).json()
    assert set(grid) == {"ch:catalog:0", "ch:catalog:1"}
    assert set((await api.get("/epg", params={"country": "US"})).json()) == {"ch:catalog:0"}
    normal = (await api.get("/channels")).json()
    assert normal["list_id"] != us["list_id"] and normal["total"] == 2


async def test_explicit_provider_country_hints_and_provenance(api: Api) -> None:
    from app.adapters.language.country import group_country

    await seed_catalog(api, channel_count=9)
    labels = [
        "US| ABC ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ",
        "US| French",
        "UK| English",
        "English",
        "AFR| AFRICA VIP",
        "4K| RELAX",
        "🇺🇸 News",
        "US| UK| Mixed",
        "USA| News",
    ]
    async with api.c.db.session() as session:
        for i, label in enumerate(labels):
            await session.execute(update(Channel).where(Channel.ext_id == str(i)).values(group_name=label))
        await session.commit()
    result = (await api.get("/channels", params={"language": "all", "country": "US"})).json()
    assert result["total"] == 4
    assert {ch["ext_id"] for ch in result["channels"]} == {"0", "1", "6", "8"}
    assert next(ch for ch in result["channels"] if ch["ext_id"] == "1")["language"] == "fra"
    assert next(ch for ch in result["channels"] if ch["ext_id"] == "0")["country_source"] == "group"
    assert next(ch for ch in result["channels"] if ch["ext_id"] == "6")["country_source"] == "flag"
    unknown = (await api.get("/channels", params={"language": "all", "country": "und"})).json()
    assert {ch["ext_id"] for ch in unknown["channels"]} == {"3", "4", "5", "7"}
    assert group_country("United States | News") == ("US", "group")
    assert group_country("English | News") == (None, None)
    async with api.c.db.session() as session:
        from sqlalchemy import select

        assert all(country is None for country in (await session.execute(select(Channel.country))).scalars())
        await session.execute(update(Channel).where(Channel.ext_id == "0").values(country="CA"))
        await session.commit()
    explicit = (await api.get("/channels", params={"country": "CA", "language": "all"})).json()["channels"][0]
    assert explicit["country"] == "CA" and explicit["country_source"] == "tag"
