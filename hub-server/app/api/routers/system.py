"""/themes, /settings, /settings/tmdb_key, /health, /about."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.adapters.tmdb.client import JUSTWATCH_ATTRIBUTION, TMDB_ATTRIBUTION
from app.api import schemas as S
from app.api.deps import C, Admin, P
from app.infra.settings import APP_VERSION
from app.services.errors import ServiceError

router = APIRouter()


@router.get("/themes", response_model=list[S.Theme], tags=["system"])
async def themes(c: C) -> list[dict[str, Any]]:
    return await c.themes.list()


@router.post("/themes", response_model=S.Theme, status_code=201, tags=["system"])
async def create_theme(body: S.Theme, c: C, p: P) -> dict[str, Any]:
    return await c.themes.create(p.profile_id, body.model_dump())


@router.get("/settings", response_model=S.Settings, tags=["system"])
async def get_settings(c: C, p: P) -> dict[str, Any]:
    return c.app_settings.public()


@router.patch("/settings", response_model=S.Settings, tags=["system"])
async def patch_settings(body: S.SettingsPatch, c: C, p: Admin) -> dict[str, Any]:
    try:
        return await c.app_settings.update(body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc


@router.put("/settings/tmdb_key", status_code=204, tags=["system"])
async def put_tmdb_key(body: S.TmdbKey, c: C, p: Admin) -> Response:
    await c.app_settings.set_tmdb_key(body.api_key)
    return Response(status_code=204)


@router.get("/health", response_model=S.Health, tags=["system"])
async def health(c: C) -> dict[str, Any]:
    ok = True
    try:
        async with c.db.session() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        ok = False
    return {"status": "ok" if ok else "degraded", "version": APP_VERSION, "db": ok}


@router.get("/about", tags=["system"])
async def about() -> dict[str, Any]:
    return {"version": APP_VERSION, "tmdb_attribution": TMDB_ATTRIBUTION, "justwatch_attribution": JUSTWATCH_ATTRIBUTION}
