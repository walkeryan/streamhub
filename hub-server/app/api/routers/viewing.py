"""Profile-private scene marks and time-limited personal-library offline copies."""

from __future__ import annotations

import base64
import hashlib
import io
import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Query, Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import C, P
from app.api.streaming import OwnedStreamingResponse
from app.domain.ports import DownloadProvider
from app.infra.models import EditionProgress, OfflineCopy, Progress, SceneBookmark, Title, ViewingCursor, utcnow
from app.services import ids
from app.services.adult import require_adult
from app.services.editions import edition_info, same_edition
from app.services.errors import BadGateway, Conflict, Forbidden, NotFound, ServiceError
from app.services.visibility import require_watch

router = APIRouter(prefix="/viewing", tags=["playback"])
MAX_BYTES = 2 * 1024**3


async def authorized(c: C, p: P, ref: str) -> None:
    await require_watch(c.db, p.permissions, ref)
    await c.content_policy.require(p.profile_id, ref)
    await require_adult(c.db, p, ref)


class SaveScene(BaseModel):
    session: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100)
    position_ms: int = Field(ge=0, le=604_800_000)
    preview: str | None = Field(default=None, max_length=90_000)


def preview_image(value: str | None) -> str | None:
    if not value:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
        with Image.open(io.BytesIO(raw), formats=["JPEG"]) as frame:
            if frame.width > 640 or frame.height > 640:
                raise ValueError
            frame.thumbnail((320, 180))
            out = io.BytesIO()
            frame.convert("RGB").save(out, format="JPEG", quality=65)
            return base64.b64encode(out.getvalue()).decode()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ServiceError("Invalid scene preview.") from exc


def scene(s: SceneBookmark) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "position_ms": s.position_ms,
        "preview": s.preview,
        "variant_id": s.variant_id,
        "edition_id": s.edition_id,
        "ref_id": s.ref_id,
    }


@router.post("/bookmarks", status_code=201)
async def save_scene(body: SaveScene, c: C, p: P) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise ServiceError("Give the scene a name.")
    async with c.db.session() as s:
        cursor = await s.scalar(
            select(ViewingCursor).where(ViewingCursor.profile_id == p.profile_id, ViewingCursor.token == body.session)
        )
        if not cursor:
            raise Conflict("Playback changed. Reopen the scene before saving.")
        await authorized(c, p, cursor.variant_id)
        await authorized(c, p, cursor.ref_id)
        row = SceneBookmark(
            id=secrets.token_hex(16),
            profile_id=p.profile_id,
            name=name,
            ref_id=cursor.ref_id,
            variant_id=cursor.variant_id,
            edition_id=cursor.edition_id,
            position_ms=body.position_ms,
            preview=preview_image(body.preview),
        )
        s.add(row)
        await s.commit()
        return scene(row)


@router.get("/bookmarks")
async def list_scenes(id: str, c: C, p: P, response: Response) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "private, no-store"
    await authorized(c, p, id)
    if id.startswith("lib:"):
        h = ids.parse(id)
        item = await c.catalog.title_row(h.a, h.b)
        if item:
            id = c.catalog.item_hub_id(item)
    async with c.db.session() as s:
        rows = list(
            await s.scalars(
                select(SceneBookmark)
                .where(SceneBookmark.profile_id == p.profile_id, SceneBookmark.ref_id == id)
                .order_by(SceneBookmark.position_ms)
            )
        )
    shown = []
    for row in rows:
        try:
            await authorized(c, p, row.variant_id)
        except (Forbidden, NotFound):
            continue
        shown.append(scene(row))
    return shown


@router.get("/bookmarks/{bookmark_id}/restore")
async def restore_scene(bookmark_id: str, c: C, p: P) -> dict[str, Any]:
    async with c.db.session() as s:
        row = await s.get(SceneBookmark, bookmark_id)
    if not row or row.profile_id != p.profile_id:
        raise NotFound("Scene not found.")
    await authorized(c, p, row.ref_id)
    options = await c.playback.options(p.profile_id, row.ref_id, probe=False)
    copies = [o for o in options["options"] if same_edition(row.edition_id, o.get("edition_id") or "")]
    chosen = next((o for o in copies if o["variant_id"] == row.variant_id), copies[0] if copies else None)
    if not chosen or (chosen["runtime_ms"] and row.position_ms >= chosen["runtime_ms"]):
        raise Conflict("This edition is unavailable or its timeline changed.")
    await authorized(c, p, chosen["variant_id"])
    return {"id": row.ref_id, "variant_id": chosen["variant_id"], "position_ms": row.position_ms}


@router.delete("/bookmarks/{bookmark_id}", status_code=204)
async def delete_scene(bookmark_id: str, c: C, p: P) -> Response:
    async with c.db.session() as s:
        await s.execute(
            delete(SceneBookmark).where(SceneBookmark.id == bookmark_id, SceneBookmark.profile_id == p.profile_id)
        )
        await s.commit()
    return Response(status_code=204)


async def download_source(c: C, p: P, ref: str) -> tuple[Title, DownloadProvider, str, int, str]:
    if p.shared_device:
        raise Forbidden("Offline viewing requires a personal device.")
    await authorized(c, p, ref)
    if not ref.startswith("lib:"):
        raise NotFound("This episode does not offer offline viewing.")
    h = ids.parse(ref)
    row = await c.catalog.title_row(h.a, h.b)
    if not row or row.kind != "episode" or row.adult:
        raise NotFound("This episode does not offer offline viewing.")
    src, provider = await c.sources.provider_for(h.a)
    if not src.enabled or not isinstance(provider, DownloadProvider):
        raise Forbidden("This library does not grant episode downloads.")
    try:
        info = await provider.download_info(h.b)
    except PermissionError as exc:
        raise Forbidden("The library did not authorize this download.") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 404, 410):
            raise Forbidden("The library did not authorize this download.") from exc
        raise BadGateway("The library could not check download permission.") from exc
    except httpx.HTTPError as exc:
        raise BadGateway("The library could not be reached.") from exc
    if not info:
        raise Forbidden("The library has not granted a playable download of this episode.")
    mime, size, version = info
    return row, provider, mime, size, hashlib.sha256(version.encode()).hexdigest()


async def baseline(s: AsyncSession, profile: str, ref: str) -> str:
    point = await s.scalar(select(Progress.updated_at).where(Progress.profile_id == profile, Progress.ref_id == ref))
    cursor = await s.get(ViewingCursor, (profile, ref))
    return f"{point.isoformat() if point else ''}|{cursor.token if cursor else ''}"


def license_info(copy: OfflineCopy, row: Title, size: int, mime: str) -> dict[str, Any]:
    return {
        "id": copy.id,
        "ref_id": copy.ref_id,
        "profile_id": copy.profile_id,
        "session_id": copy.session_id,
        "title": ids.episode_display_title(row.title, row.episode_number),
        "mime": mime,
        "size": size,
        "duration_ms": row.duration_ms,
        "expires_at": copy.expires_at,
    }


@router.post("/downloads", status_code=201)
async def acquire(id: str, c: C, p: P, max_bytes: int = Query(ge=1, le=MAX_BYTES)) -> dict[str, Any]:
    row, _, mime, size, fingerprint = await download_source(c, p, id)
    if size > max_bytes:
        raise Conflict("This episode exceeds the remaining download limit.")
    now = utcnow()
    async with c.db.session() as s:
        await s.execute(delete(OfflineCopy).where(OfflineCopy.expires_at <= now))
        grant = OfflineCopy(
            id=secrets.token_hex(16),
            ref_id=id,
            profile_id=p.profile_id,
            session_id=p.session_id,
            edition_id=edition_info(row, [row])["edition_id"],
            fingerprint=fingerprint,
            baseline=await baseline(s, p.profile_id, id),
            expires_at=now + timedelta(hours=24),
        )
        s.add(grant)
        await s.commit()
    return license_info(grant, row, size, mime)


async def checked(c: C, p: P, grant: str) -> tuple[OfflineCopy, Title, DownloadProvider, str, int]:
    async with c.db.session() as s:
        copy = await s.get(OfflineCopy, grant)
    if not copy or copy.profile_id != p.profile_id or copy.session_id != p.session_id or copy.expires_at <= utcnow():
        raise NotFound("This offline copy has expired or belongs to another session.")
    row, provider, mime, size, fingerprint = await download_source(c, p, copy.ref_id)
    if copy.fingerprint != fingerprint:
        raise Conflict("This episode changed. Download the current version.")
    return copy, row, provider, mime, size


@router.get("/downloads/{grant}")
async def validate_copy(grant: str, c: C, p: P, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    copy, row, _, mime, size = await checked(c, p, grant)
    return license_info(copy, row, size, mime)


@router.get("/downloads/{grant}/content")
async def download_content(grant: str, c: C, p: P) -> Response:
    _, row, provider, mime, size = await checked(c, p, grant)
    stream = provider.download_bytes(row.ext_id, size)
    try:
        first = await anext(stream)
    except PermissionError as exc:
        raise Forbidden("The library refused this download.") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403, 404, 410):
            raise Forbidden("The library refused this download.") from exc
        raise BadGateway("The library could not send the file.") from exc
    except (StopAsyncIteration, ValueError, httpx.HTTPError) as exc:
        raise BadGateway("The download could not start.") from exc

    async def body() -> AsyncIterator[bytes]:
        yield first
        async for chunk in stream:
            yield chunk

    return OwnedStreamingResponse(
        body(),
        owned_stream=stream,
        media_type=mime,
        headers={"Content-Length": str(size), "Cache-Control": "private, no-store"},
    )


@router.post("/downloads/{grant}/progress", status_code=204)
async def sync_offline(
    grant: str,
    c: C,
    p: P,
    position_ms: int = Query(ge=0, le=604_800_000),
    duration_ms: int = Query(gt=0, le=604_800_000),
) -> Response:
    copy, _, _, _, _ = await checked(c, p, grant)
    async with c.db.session() as s:
        await s.execute(text("BEGIN IMMEDIATE"))
        own = await s.get(OfflineCopy, grant)
        if not own or own.baseline != await baseline(s, p.profile_id, copy.ref_id):
            raise Conflict("Another device already saved newer progress.")
        done = position_ms >= duration_ms * 0.95
        point = await s.scalar(
            select(Progress).where(Progress.profile_id == p.profile_id, Progress.ref_id == copy.ref_id)
        )
        if not point:
            point = Progress(profile_id=p.profile_id, ref_id=copy.ref_id)
            s.add(point)
        point.position_ms, point.duration_ms, point.completed, point.updated_at = (
            0 if done else position_ms,
            duration_ms,
            done,
            utcnow(),
        )
        edition = await s.get(EditionProgress, (p.profile_id, copy.ref_id, copy.edition_id))
        if not edition:
            edition = EditionProgress(
                profile_id=p.profile_id, ref_id=copy.ref_id, edition_id=copy.edition_id, variant_id=copy.ref_id
            )
            s.add(edition)
        edition.position_ms, edition.duration_ms, edition.completed = point.position_ms, duration_ms, done
        await s.execute(
            delete(ViewingCursor).where(ViewingCursor.profile_id == p.profile_id, ViewingCursor.ref_id == copy.ref_id)
        )
        await s.flush()
        own.baseline = await baseline(s, p.profile_id, copy.ref_id)
        await s.commit()
    return Response(status_code=204)
