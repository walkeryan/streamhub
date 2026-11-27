"""Keep the small canonical date used before browse pagination, apart from the full metadata cache."""

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert

from app.infra.db import Database
from app.infra.models import CatalogRelease


class ReleaseDates:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, kind: str, tmdb_id: int, day: str | None) -> None:
        async with self._db.session() as s:
            if day is None:
                await s.execute(
                    delete(CatalogRelease).where(CatalogRelease.media_type == kind, CatalogRelease.tmdb_id == tmdb_id)
                )
            else:
                stmt = insert(CatalogRelease).values(media_type=kind, tmdb_id=tmdb_id, release_date=day)
                await s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["media_type", "tmdb_id"],
                        set_={"release_date": day},
                        where=CatalogRelease.release_date != day,
                    )
                )
            await s.commit()
