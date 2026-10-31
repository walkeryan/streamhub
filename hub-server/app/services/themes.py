"""Themes: builtins from design/tokens.json plus user-created custom themes."""

from __future__ import annotations

import json
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.infra.db import Database
from app.infra.models import Theme
from app.services.errors import Conflict, ServiceError

TOKENS_PATH = Path(__file__).resolve().parents[1] / "resources" / "tokens.json"
_COLOR_RE = re.compile(r"^(#[0-9A-Fa-f]{3,8}|rgba?\([^)]{3,40}\)|dynamic:[a-z\-]+)$")


@lru_cache(maxsize=1)
def load_tokens() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    return data


def builtin_themes() -> list[dict[str, Any]]:
    tokens = load_tokens()
    themes: dict[str, Any] = tokens.get("themes", {})
    out = []
    for tid, t in themes.items():
        resolved = dict(t)
        base = resolved.pop("extends", None)
        if base and base in themes:
            resolved = {**themes[base], **resolved}
        name = resolved.pop("name", tid)
        out.append({"id": tid, "name": name, "builtin": True, "tokens": resolved})
    return out


class ThemeService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self) -> list[dict[str, Any]]:
        async with self._db.session() as s:
            custom = list((await s.execute(select(Theme).order_by(Theme.created_at))).scalars())
        return builtin_themes() + [
            {"id": t.id, "name": t.name, "builtin": False, "tokens": t.tokens} for t in custom
        ]

    async def exists(self, theme_id: str) -> bool:
        return any(t["id"] == theme_id for t in await self.list())

    async def create(self, profile_id: str, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()[:64]
        if not name:
            raise ServiceError("name required")
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            raise ServiceError("tokens must be an object")
        builtins = {t["id"]: t for t in builtin_themes()}
        base_id = str(tokens.get("extends") or "midnight")
        base = builtins.get(base_id, builtins["midnight"])["tokens"]
        merged = {**base, **{k: v for k, v in tokens.items() if k != "extends"}}
        for k, v in merged.items():
            if isinstance(v, str) and k not in ("name",) and not _COLOR_RE.match(v):
                raise ServiceError(f"invalid colour for {k}")
        tid = str(data.get("id") or "").strip() or f"custom-{secrets.token_hex(4)}"
        if not re.fullmatch(r"[a-z0-9\-_]{1,64}", tid):
            raise ServiceError("invalid theme id")
        if tid in builtins:
            raise Conflict("cannot overwrite a builtin theme")
        async with self._db.session() as s:
            row = await s.get(Theme, tid)
            if row is None:
                row = Theme(id=tid, name=name, tokens=merged, owner_profile_id=profile_id)
                s.add(row)
            else:
                row.name = name
                row.tokens = merged
            await s.commit()
        return {"id": tid, "name": name, "builtin": False, "tokens": merged}
