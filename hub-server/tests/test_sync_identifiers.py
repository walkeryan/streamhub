"""A sync refreshes what the panel lists; it must not erase identifiers learned elsewhere."""

from __future__ import annotations

import respx
from sqlalchemy import select

from app.domain.entities import CatalogItem, MediaKind
from app.infra.models import Source, Title
from app.services.sources import LAZY_GEN, item_row, upsert_titles
from tests.conftest import Api
from tests.test_presentation_sync import MANAGER, add_xtream, panel, titles


@respx.mock
async def test_resync_keeps_lazily_discovered_identifiers(api: Api) -> None:
    # The list endpoint knows no ids for this film; only its detail does.
    respx.get(f"{MANAGER}/player_api.php").mock(
        side_effect=panel(
            get_vod_categories=[{"category_id": "1", "category_name": "EN - NEW RELEASE"}],
            get_vod_streams=[{"stream_id": 7, "name": "EN - The Mill  (2023)", "category_id": "1"}],
            get_vod_info={"info": {"tmdb_id": 4242, "imdb_id": "tt0000042"}},
        )
    )
    sid = await add_xtream(api)
    assert (await titles(api, sid))["7"].tmdb_id is None

    detail = await api.get(f"/titles/lib:{sid}:7")
    assert detail.status_code == 200 and detail.json()["id"] == "tmdb:movie:4242"
    saved = await api.put("/favorites", json={"kind": "movie", "id": "tmdb:movie:4242"})
    assert saved.status_code == 204, saved.text
    before = (await titles(api, sid))["7"]
    assert (before.tmdb_id, before.imdb_id) == (4242, "tt0000042")

    assert await api.c.sync.sync_source(sid)
    after = (await titles(api, sid))["7"]
    assert after.sync_gen == before.sync_gen + 1
    assert (after.tmdb_id, after.imdb_id) == (4242, "tt0000042")
    favorites = (await api.get("/favorites")).json()
    assert [(t["id"], t["title"]) for t in favorites] == [("tmdb:movie:4242", "The Mill")]


async def test_upsert_replaces_identifiers_only_with_known_values(api: Api) -> None:
    async with api.c.db.session() as session:
        session.add(Source(id="panel", name="Panel", type="xtream", config_enc="unused"))
        await session.commit()

    async def store(gen: int, tmdb_id: int | None = None, imdb_id: str | None = None) -> Title:
        item = CatalogItem(ext_id="7", kind=MediaKind.VOD, title="The Mill", tmdb_id=tmdb_id, imdb_id=imdb_id)
        await upsert_titles(api.c.db, [item_row("panel", item, gen)])
        async with api.c.db.session() as session:
            return (await session.execute(select(Title).where(Title.source_id == "panel"))).scalar_one()

    row = await store(1, tmdb_id=1, imdb_id="tt0000001")
    assert (row.tmdb_id, row.imdb_id) == (1, "tt0000001")
    row = await store(2)
    assert (row.tmdb_id, row.imdb_id, row.sync_gen) == (1, "tt0000001", 2)
    row = await store(LAZY_GEN, imdb_id="tt0000002")
    assert (row.tmdb_id, row.imdb_id, row.sync_gen) == (1, "tt0000002", 2)
    row = await store(3, tmdb_id=2)
    assert (row.tmdb_id, row.imdb_id) == (2, "tt0000002")
