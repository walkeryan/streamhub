from datetime import UTC, datetime

from sqlalchemy import update

from app.infra.models import Profile, Title
from tests.conftest import Api
from tests.test_large_catalog_filters import seed_catalog


async def test_halloween_collection_metadata_language_date_and_kids(api: Api) -> None:
    await seed_catalog(api, title_count=6)
    async with api.c.db.session() as session:
        await session.execute(
            update(Title).where(Title.ext_id.in_(["0", "1", "2", "hidden"])).values(title="Halloween Movie")
        )
        await session.commit()
    profile_id = api.profile_id
    ctx = await api.c.catalog.tile_context(profile_id)
    october = datetime(2026, 10, 31, 23, tzinfo=UTC)
    tiles = await api.c.catalog.seasonal_movies(profile_id, ctx, now=october)
    assert [tile["id"] for tile in tiles] == ["lib:catalog:0"]
    # Midnight UTC is still Halloween locally; November in New York ends the row.
    assert await api.c.catalog.seasonal_movies(profile_id, ctx, now=datetime(2026, 11, 1, 1, tzinfo=UTC))
    assert not await api.c.catalog.seasonal_movies(profile_id, ctx, now=datetime(2026, 11, 1, 6, tzinfo=UTC))
    async with api.c.db.session() as session:
        await session.execute(update(Profile).where(Profile.id == profile_id).values(kids=True))
        await session.commit()
    assert not await api.c.catalog.seasonal_movies(profile_id, ctx, now=october)
