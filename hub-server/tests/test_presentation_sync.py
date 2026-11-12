"""Presentation fields end to end: panel → sync → rows → browse. Names are invented; shapes are real."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import respx
from sqlalchemy import select

from app.infra.models import Channel, Title
from tests.conftest import Api

MANAGER = "http://manager.example.test"
LIBRARY = "http://library.example.test:32400"
BRAND = "NETFLIX MOVIES ⁴ᴷ ³⁸⁴⁰ᴾ ᴰᵒˡᵇʸ ⱽᶦˢᶦᵒⁿ"


def panel(**actions: Any) -> Callable[[httpx.Request], httpx.Response]:
    """A player_api.php handler; keyword arguments replace the default (empty) action responses."""
    data: dict[str | None, Any] = {
        None: {"user_info": {"auth": 1, "status": "Active"}, "server_info": {}},
        "get_live_categories": [],
        "get_live_streams": [],
        "get_vod_categories": [],
        "get_vod_streams": [],
        "get_series_categories": [],
        "get_series": [],
        "get_vod_info": {"info": {}},
        "get_series_info": {},
        **actions,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=data[request.url.params.get("action")])

    return handler


async def add_xtream(api: Api, **config: Any) -> str:
    await api.accept_disclaimer()
    created = await api.post(
        "/sources",
        json={
            "type": "xtream",
            "name": "Manager",
            "config": {"server_url": MANAGER, "username": "alice", "password": "s3cret", **config},
        },
    )
    assert created.status_code == 201, created.text
    await api.c.sync.wait_idle()
    return str(created.json()["id"])


async def titles(api: Api, source_id: str) -> dict[str, Title]:
    async with api.c.db.session() as session:
        rows = (await session.execute(select(Title).where(Title.source_id == source_id))).scalars()
        return {row.ext_id: row for row in rows}


@respx.mock
async def test_sync_stores_presentation_fields(api: Api) -> None:
    respx.get(f"{MANAGER}/player_api.php").mock(
        side_effect=panel(
            get_vod_categories=[
                {"category_id": "1", "category_name": BRAND},
                {"category_id": "2", "category_name": "EN - NEW RELEASE"},
            ],
            get_vod_streams=[
                {"stream_id": 1, "name": "NF - Millionaire", "category_id": "1"},
                {"stream_id": 2, "name": "EN - The Mill  (2023)", "category_id": "2", "is_adult": "1"},
            ],
            get_series_categories=[{"category_id": "7", "category_name": "APPLE+ KIDS"}],
            get_series=[{"series_id": 3, "name": "4K-A+ - Berlin ER (2025) (DE)", "category_id": "7"}],
            get_series_info={"episodes": {"1": [{"id": "30", "episode_num": 1, "title": "Arrival"}]}},
            get_live_categories=[
                {"category_id": "20", "category_name": "US| SPORT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ"},
                {"category_id": "21", "category_name": "US| NFL PPV"},
            ],
            get_live_streams=[
                {"stream_id": 100, "name": "Sport One", "category_id": "20"},
                {"stream_id": 101, "name": "Game One", "category_id": "21"},
            ],
        )
    )
    sid = await add_xtream(api, hidden_groups=["US| NFL PPV"])
    rows = await titles(api, sid)
    brand, release, series = rows["1"], rows["2"], rows["s:3"]
    assert (brand.title, brand.group_name, brand.quality, brand.language, brand.adult) == (
        "Millionaire",
        "Netflix",
        "4K",
        "und",
        False,
    )
    assert brand.norm_title == "millionaire" and brand.extra["raw_category"] == BRAND
    assert (release.title, release.year, release.group_name, release.language, release.adult) == (
        "The Mill",
        2023,
        "New Releases",
        "eng",
        True,
    )
    assert (series.title, series.year, series.group_name, series.quality) == ("Berlin ER", 2025, "Apple TV+", "4K")
    assert series.extra["kids"] is True

    async with api.c.db.session() as session:
        channels = list((await session.execute(select(Channel).where(Channel.source_id == sid))).scalars())
    assert [(c.name, c.group_name) for c in channels] == [("Sport One", "Sport")]  # the hidden group is not stored
    # The tag that was stripped from the group still drives language and country.
    assert (channels[0].language, channels[0].language_source, channels[0].country) == ("eng", "group", "US")
    listed = (await api.get("/channels", params={"language": "eng", "country": "US"})).json()["channels"]
    assert [(c["name"], c["group"], c["country"]) for c in listed] == [("Sport One", "Sport", "US")]

    # Lazily fetched children inherit what only the series row knows.
    await api.patch(f"/sources/{sid}", json={"config": {"default_language": "de"}})
    await api.c.sync.sync_source(sid)
    detail = await api.get(f"/titles/lib:{sid}:s:3")
    assert detail.status_code == 200 and [e["title"] for e in detail.json()["episodes"]] == ["Arrival"]
    rows = await titles(api, sid)
    assert rows["1"].language == "deu" and rows["2"].language == "eng"
    assert (rows["e:30"].language, rows["s:3:s1"].language) == ("deu", "deu")

    movies = (await api.get("/browse/movies", params={"language": "all", "sort": "title"})).json()["tiles"]
    assert [(t["title"], t["sources"][0]["quality"]) for t in movies] == [("The Mill", None), ("Millionaire", "4K")]
    categories = (await api.get("/browse/movies/categories", params={"language": "all"})).json()
    assert categories == [{"name": "Netflix", "count": 1}, {"name": "New Releases", "count": 1}]
    assert (await api.get("/search", params={"q": "The Mill", "language": "all"})).json()["results"][0][
        "title"
    ] == "The Mill"


@respx.mock
async def test_brand_and_library_titles_listed_for_a_language_profile(api: Api) -> None:
    respx.get(f"{MANAGER}/player_api.php").mock(
        side_effect=panel(
            get_vod_categories=[{"category_id": "1", "category_name": BRAND}],
            get_vod_streams=[{"stream_id": 1, "name": "NF - Millionaire", "category_id": "1"}],
        )
    )
    respx.get(f"{LIBRARY}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "movie", "title": "Films"}]}}
        )
    )
    movie = {"ratingKey": "11", "type": "movie", "title": "Lighthouse Road", "year": 2021}
    respx.get(f"{LIBRARY}/library/sections/1/all").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"totalSize": 1, "Metadata": [movie]}})
    )
    await api.patch(f"/profiles/{api.profile_id}", json={"languages": ["en"]})
    panel_id = await add_xtream(api)
    created = await api.post(
        "/sources", json={"type": "plex", "name": "Library", "config": {"server_url": LIBRARY, "token": "tok"}}
    )
    assert created.status_code == 201, created.text
    library_id = created.json()["id"]
    await api.c.sync.wait_idle()

    # Neither a brand category nor a library section says anything about language.
    assert (await api.get("/browse/movies")).json()["total"] == 0
    languages = (await api.get("/browse/movies/languages")).json()
    assert languages == [{"code": "und", "name": "Unknown", "count": 2}]

    for source_id in (panel_id, library_id):
        patched = await api.patch(f"/sources/{source_id}", json={"config": {"default_language": "en"}})
        assert patched.status_code == 200, patched.text
        assert patched.json()["config_summary"]["default_language"] == "eng"
        assert await api.c.sync.sync_source(source_id)
    listed = (await api.get("/browse/movies", params={"sort": "title"})).json()
    assert [t["title"] for t in listed["tiles"]] == ["Lighthouse Road", "Millionaire"]
    languages = (await api.get("/browse/movies/languages")).json()
    assert languages == [{"code": "eng", "name": "English", "count": 2}]
    assert (await api.get("/search", params={"q": "Millionaire"})).json()["results"][0]["title"] == "Millionaire"


def test_migration_adds_presentation_columns_to_an_existing_catalog(tmp_path: Path) -> None:
    import sqlite3

    from alembic import command
    from alembic.config import Config

    from app.infra.db import HUB_ROOT

    path = tmp_path / "existing.db"
    cfg = Config(str(HUB_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(HUB_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "0006")
    insert = (
        "INSERT INTO title(source_id,ext_id,kind,title,norm_title,genres,group_name,extra,sync_gen) "
        "VALUES ('panel',?,'vod',?,?,'[]',?,'{}',1)"
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO source(id,type,name,enabled,priority,region,status,config_enc,hints,counts,created_at) "
            "VALUES ('panel','xtream','Panel',1,10,'US','ok','unused','{}','{}','2026-11-01')"
        )
        conn.executemany(
            insert,
            [
                ("1", "The Mill", "mill", "EN - NEW RELEASE"),
                ("2", "Le Moulin", "le moulin", "FR| Films"),
                ("3", "Millionaire", "millionaire", "NETFLIX  SERIES"),
                ("4", "Untagged", "untagged", None),
            ],
        )
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT ext_id,language,quality,adult,group_name FROM title ORDER BY ext_id").fetchall()
        # Languages are carried over from the category so filters keep working until the next sync.
        assert rows == [
            ("1", "eng", None, 0, "EN - NEW RELEASE"),
            ("2", "fra", None, 0, "FR| Films"),
            ("3", "und", None, 0, "NETFLIX  SERIES"),
            ("4", "und", None, 0, None),
        ]
        indexes = {row[1] for row in conn.execute("PRAGMA index_list('title')")}
        assert {"ix_title_language", "ix_title_adult"} <= indexes
        conn.execute(insert, ("5", "After", "after", None))
        assert conn.execute("SELECT language,adult FROM title WHERE ext_id='5'").fetchone() == ("und", 0)
    command.downgrade(cfg, "0006")
    with sqlite3.connect(path) as conn:
        assert "language" not in {row[1] for row in conn.execute("PRAGMA table_info('title')")}
        assert conn.execute("SELECT count(*) FROM title").fetchone() == (5,)
        triggers = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='title'")
        assert triggers.fetchone() == (3,)  # the search index is still maintained
