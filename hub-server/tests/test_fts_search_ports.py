"""FTS port regressions using disposable data; no metadata/provider network."""

import importlib.util
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import insert

from app.infra.models import Title
from app.services.ids import normalise_title
from tests.test_large_catalog_filters import seed_catalog


def test_fts_migration_rebuild_and_transactional_changes(monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/0006_title_search.py"
    spec = importlib.util.spec_from_file_location("fts_port_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0006" and module.down_revision == "0005"
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE title(id INTEGER PRIMARY KEY,title TEXT,norm_title TEXT)")
        connection.execute("INSERT INTO title VALUES(1,'Before migration','before migration')")
        monkeypatch.setattr(module.op, "execute", connection.execute)
        module.upgrade()

        def hits(term):
            return [
                row[0]
                for row in connection.execute("SELECT rowid FROM title_search WHERE title_search MATCH ?", (term,))
            ]

        assert hits("migration") == [1]
        connection.execute("INSERT INTO title VALUES(2,'Added afterward','added afterward')")
        connection.commit()
        assert hits("afterward") == [2]
        connection.execute("UPDATE title SET title='Changed label',norm_title='changed label' WHERE id=2")
        assert hits("changed") == [2] and not hits("afterward")
        connection.rollback()
        assert hits("afterward") == [2] and not hits("changed")
        connection.execute("DELETE FROM title WHERE id=2")
        assert not hits("afterward")
        module.downgrade()
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'title_search%'").fetchall() == []
        assert connection.execute("SELECT title FROM title WHERE id=1").fetchone()[0] == "Before migration"


async def test_work_budget_aliases_transposition_and_episode_identity(api):
    await seed_catalog(api)
    fake = AsyncMock()
    fake.enabled = False
    api.c.catalog.meta = fake
    items = [
        dict(
            source_id="catalog",
            ext_id=f"noise-{n}",
            kind="vod",
            title=f"Star Documentary {n:04}",
            norm_title=f"star documentary {n:04}",
        )
        for n in range(600)
    ]
    items += [
        dict(
            source_id="catalog",
            ext_id=f"edition-{n}",
            kind="vod",
            title="EN-Star Wars (1977)",
            norm_title=normalise_title("EN-Star Wars (1977)"),
            tmdb_id=11,
        )
        for n in range(550)
    ]
    items += [
        dict(
            source_id="catalog",
            ext_id="alias-first",
            kind="vod",
            title="FR-Star Wars Le Reveil",
            norm_title=normalise_title("FR-Star Wars Le Reveil"),
            tmdb_id=140607,
        ),
        dict(
            source_id="catalog",
            ext_id="alias-best",
            kind="vod",
            title="EN-Star Wars The Force Awakens",
            norm_title=normalise_title("EN-Star Wars The Force Awakens"),
            tmdb_id=140607,
        ),
    ]
    items += [
        dict(
            source_id="catalog",
            ext_id=f"episode-{n}",
            kind="episode",
            title=f"Fixture Show Episode {n}",
            norm_title=f"fixture show episode {n}",
            tmdb_id=1399,
        )
        for n in range(2)
    ]
    async with api.c.db.session() as session:
        await session.execute(insert(Title), items)
        await session.commit()

    async def search(q, kinds):
        response = await api.get("/search", params={"q": q, "language": "all", "kinds": kinds})
        assert response.status_code == 200, response.text
        return response.json()["results"]

    exact = await search("Star Wars", "movie")
    assert exact[0]["id"] == "tmdb:movie:11"
    assert len([row for row in exact if row["id"] == "tmdb:movie:11"]) == 1
    typo = await search("Star Wras", "movie")
    assert typo[0]["id"] == "tmdb:movie:11"
    best = await search("The Force Awakens", "movie")
    assert best[0]["id"] == "tmdb:movie:140607" and best[0]["title"] == "Star Wars The Force Awakens"
    episodes = await search("Fixture Show", "episode")
    assert {row["id"] for row in episodes} == {"lib:catalog:episode-0", "lib:catalog:episode-1"}
