"""Legal disclaimer (docs/LEGAL.md) — version + per-profile acceptance."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from app.infra.db import Database
from app.infra.models import DisclaimerAcceptance

LEGAL_PATH = Path(__file__).resolve().parents[1] / "resources" / "LEGAL.md"
DEFAULT_VERSION = "1.0"


@lru_cache(maxsize=1)
def load_disclaimer() -> tuple[str, str]:
    """Return (version, text). Text is the blockquoted notice from LEGAL.md, markdown kept."""
    raw = LEGAL_PATH.read_text(encoding="utf-8")
    m = re.search(r"\(version\s+([0-9.]+)\)", raw)
    version = m.group(1) if m else DEFAULT_VERSION
    paras: list[str] = []
    cur: list[str] = []
    for line in raw.splitlines():
        if line.startswith(">"):
            body = line[1:].strip()
            if body:
                cur.append(body)
            elif cur:
                paras.append(" ".join(cur))
                cur = []
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    return version, "\n\n".join(paras)


class DisclaimerService:
    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def version(self) -> str:
        return load_disclaimer()[0]

    @property
    def text(self) -> str:
        return load_disclaimer()[1]

    async def accepted_version(self, profile_id: str) -> str | None:
        async with self._db.session() as s:
            row = (
                await s.execute(
                    select(DisclaimerAcceptance)
                    .where(DisclaimerAcceptance.profile_id == profile_id)
                    .order_by(DisclaimerAcceptance.accepted_at.desc(), DisclaimerAcceptance.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return row.version if row else None

    async def has_accepted_current(self, profile_id: str) -> bool:
        return await self.accepted_version(profile_id) == self.version

    async def accept(self, profile_id: str, version: str | None) -> None:
        if version and version != self.version:
            from app.services.errors import Conflict

            raise Conflict(f"current disclaimer version is {self.version}", code="version_mismatch")
        async with self._db.session() as s:
            s.add(DisclaimerAcceptance(profile_id=profile_id, version=self.version))
            await s.commit()
