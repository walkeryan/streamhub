"""Per-profile notices (for example: a requested title arrived)."""

from __future__ import annotations

import builtins
import secrets

from sqlalchemy import select

from app.infra.db import Database
from app.infra.models import Notice, utcnow
from app.services.errors import NotFound


def new_notice(profile_id: str, kind: str, title: str, body: str = "", ref_id: str | None = None) -> Notice:
    return Notice(id=secrets.token_hex(8), profile_id=profile_id, kind=kind, title=title, body=body, ref_id=ref_id)


class NoticeService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self, profile_id: str, limit: int = 50) -> builtins.list[Notice]:
        async with self._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(Notice)
                        .where(Notice.profile_id == profile_id)
                        .order_by(Notice.read_at.is_not(None), Notice.created_at.desc(), Notice.id)
                        .limit(limit)
                    )
                ).scalars()
            )

    async def mark_read(self, profile_id: str, notice_id: str) -> Notice:
        async with self._db.session() as s:
            row = await s.get(Notice, notice_id)
            # Another profile's notice is indistinguishable from a missing one.
            if row is None or row.profile_id != profile_id:
                raise NotFound("notice not found")
            if row.read_at is None:
                row.read_at = utcnow()
                await s.commit()
            return row
