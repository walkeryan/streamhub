"""/sources, /sources/plex/pin, /services/registry."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Response

from app.api import schemas as S
from app.api.deps import C, Admin, P
from app.domain.entities import SourceStatus
from app.services.errors import PreconditionRequired, ServiceError

router = APIRouter()

MAX_PLAYLIST_BYTES = 200 * 1024 * 1024


def _store_playlist(c: C, content: str) -> str:
    """Persist uploaded playlist text under CONFIG_DIR/playlists; returns stored file name."""
    data = content.encode("utf-8")
    if len(data) > MAX_PLAYLIST_BYTES:
        raise ServiceError("playlist too large")
    if "#EXTINF" not in content[:1_000_000] and "://" not in content[:10_000]:
        raise ServiceError("file does not look like an M3U playlist")
    d = c.settings.playlist_dir
    d.mkdir(parents=True, exist_ok=True)
    name = f"upload-{secrets.token_hex(8)}.m3u"
    (d / name).write_bytes(data)
    return name


def _prep_config(c: C, type_: str, config: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(config)
    if type_ == "m3u" and isinstance(cfg.get("file"), str):
        f = cfg["file"]
        # Web client sends the playlist text itself in config.file.
        if "\n" in f or f.lstrip().startswith("#EXTM3U"):
            cfg["file"] = _store_playlist(c, f)
        else:
            # a bare name must refer to an already stored playlist (no path traversal)
            if "/" in f or "\\" in f or not (c.settings.playlist_dir / f).is_file():
                raise ServiceError("config.file must be playlist content")
    return cfg


@router.get("/sources", response_model=list[S.Source], tags=["sources"])
async def list_sources(c: C, p: P) -> list[dict[str, Any]]:
    return [await c.sources.to_api(r) for r in await c.sources.list()]


@router.post(
    "/sources",
    response_model=S.Source,
    status_code=201,
    responses={412: {"model": S.Error, "description": "disclaimer not accepted"}},
    tags=["sources"],
)
async def create_source(body: S.SourceCreate, c: C, p: Admin) -> dict[str, Any]:
    # check the gate before touching uploaded content
    if c.registry.requires_disclaimer(body.type) and not await c.disclaimer.has_accepted_current(p.profile_id):
        from app.services.errors import PreconditionFailed

        raise PreconditionFailed("Accept the legal disclaimer before adding IPTV/M3U sources")
    cfg = _prep_config(c, body.type, body.config)
    if body.type == "jellyfin" and not cfg.get("username") and not cfg.get("access_token"):
        cfg.setdefault("quick_connect", True)
    row = await c.sources.create(
        profile_id=p.profile_id, type_=body.type, name=body.name, config=cfg, region=body.region,
        service_key=body.service_key,
    )
    if row.type != "streaming_service":
        c.sync.spawn(row.id)
    return await c.sources.to_api(row)


@router.get("/sources/{id}", response_model=S.Source, tags=["sources"])
async def get_source(id: str, c: C, p: P) -> dict[str, Any]:
    return await c.sources.to_api(await c.sources.get(id))


@router.patch("/sources/{id}", response_model=S.Source, tags=["sources"])
async def update_source(id: str, body: S.SourcePatch, c: C, p: Admin) -> dict[str, Any]:
    row = await c.sources.get(id)
    patch = body.model_dump(exclude_unset=True)
    patch.pop("type", None)
    if isinstance(patch.get("config"), dict):
        patch["config"] = _prep_config(c, row.type, patch["config"])
    row = await c.sources.update(id, patch)
    return await c.sources.to_api(row)


@router.delete("/sources/{id}", status_code=204, tags=["sources"])
async def delete_source(id: str, c: C, p: Admin) -> Response:
    row = await c.sources.get(id)
    cfg = c.sources.decrypt_config(row)
    await c.sources.delete(id)
    f = cfg.get("file")
    if isinstance(f, str) and f.startswith("upload-") and "/" not in f:
        (c.settings.playlist_dir / f).unlink(missing_ok=True)
    return Response(status_code=204)


@router.post("/sources/{id}/sync", status_code=202, tags=["sources"])
async def sync_source(id: str, c: C, p: Admin) -> Response:
    row = await c.sources.get(id)
    if not c.sync.is_running(row.id):
        await c.sources.set_status(row.id, SourceStatus.SYNCING)
        c.sync.spawn(row.id)
    return Response(status_code=202)


@router.post("/sources/{id}/test", response_model=S.TestResult, tags=["sources"])
async def test_source(id: str, c: C, p: Admin) -> dict[str, Any]:
    row = await c.sources.get(id)
    res = await c.sources.provider(row).test()
    return {"ok": res.ok, "message": res.message}


@router.post("/sources/plex/pin", response_model=S.PlexPin, tags=["sources"])
async def plex_pin(c: C, p: Admin) -> dict[str, Any]:
    return await c.plex_link.start()


@router.get(
    "/sources/plex/pin/{pin_id}",
    response_model=list[S.Source],
    responses={428: {"model": S.Error, "description": "pending"}},
    tags=["sources"],
)
async def plex_pin_poll(pin_id: int, c: C, p: Admin) -> list[dict[str, Any]]:
    drafts = await c.plex_link.poll(pin_id)
    if drafts is None:
        raise PreconditionRequired("waiting for Plex link")
    rows = await c.sources.create_drafts(p.profile_id, drafts)
    for r in rows:
        c.sync.spawn(r.id)
    return [await c.sources.to_api(r) for r in rows]


@router.get("/services/registry", response_model=list[S.ServiceInfo], tags=["sources"])
async def services_registry(c: C, p: P) -> list[dict[str, Any]]:
    return await c.service_registry.api_list()
