"""Personal recommendations and short-lived, opt-in group picks."""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from app.api.deps import C, P, acting_as
from app.api.policy_route import PolicyCatalogRoute
from app.api.routers.rooms import _require_rated
from app.api.schemas import Tile
from app.container import Container
from app.infra.models import RecommendationVote, Watchlist, utcnow
from app.services import ids
from app.services.auth import Principal
from app.services.errors import Conflict, NotFound, ServiceError
from app.services.for_you import feed
from app.services.recommendations import recommendations
from app.services.tiles import hub_id_for_title
from app.services.visibility import require_watch

router = APIRouter(prefix="/for-you", tags=["catalog"], route_class=PolicyCatalogRoute)


class UpcomingEpisode(BaseModel):
    show: Tile
    season: int | None
    episode: int | None
    title: str
    air_date: str


class PersonalFeed(BaseModel):
    new_episodes: list[Tile]
    coming_up: list[UpcomingEpisode]
    movies: list[Tile]
    series: list[Tile]
    feedback: dict[str, Literal["up", "down"]]
    reason: str
    history_used: bool


class TogetherPicks(BaseModel):
    code: str
    member_count: int
    expires_at: datetime
    tiles: list[Tile]


@router.get("", response_model=PersonalFeed)
async def personal(c: C, p: P, response: Response, timezone: str = Query("UTC", max_length=80)) -> dict[str, Any]:
    try:
        today = datetime.now(ZoneInfo(timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        today = utcnow().date()
    response.headers["Cache-Control"] = "private, no-store"
    return await feed(c, p, today)


class Rating(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    vote: Literal["up", "down"] | None


async def rating_subject(c: Container, p: Principal, ref: str) -> tuple[str, list[str]]:
    await require_watch(c.db, p.permissions, ref)
    await _require_rated(c, p, ref)
    try:
        media = ids.parse(ref)
    except ValueError as exc:
        raise NotFound("Unknown media") from exc
    if media.scheme == "rec":
        c.recorder._require_view(p)
        recording = await c.recorder._owned(p, media.a)
        return ref, ["Sports"] if recording.is_sport else []
    if media.scheme == "prog":
        programme = await c.channels.programme(ref)
        identity = repr((programme.feed, programme.epg_channel, programme.start.isoformat(), programme.title))
        return "airing:" + hashlib.sha256(identity.encode()).hexdigest(), [
            programme.category
        ] if programme.category else []
    if media.scheme == "tmdb" and media.a in {"movie", "tv"} and media.b.isdigit():
        rows = await c.catalog.rows_for_tmdb(media.a, int(media.b))
        if rows:
            return ref, [tag for row in rows for tag in (row.genres or [])]
    if media.scheme == "lib":
        row = await c.catalog.title_row(media.a, media.b)
        if row is None or media.a not in (await c.catalog.tile_context(p.profile_id)).sources:
            raise NotFound("Title not found")
        genres = list(row.genres or [])
        ancestor = row
        for _ in range(2):
            if genres or not ancestor.parent_ext_id:
                break
            parent = await c.catalog.title_row(media.a, ancestor.parent_ext_id)
            if parent is None:
                break
            genres = list(parent.genres or [])
            ancestor = parent
        return hub_id_for_title(row), genres
    if media.scheme == "ch":
        channel = await c.channels.channel_by_hub_id(ref)
        return ref, [channel.group_name] if channel.group_name else []
    detail = await c.catalog.detail(ref, p.profile_id)
    return detail["id"], detail.get("genres", [])


class MediaState(Rating):
    saved: bool


@router.get("/feedback", response_model=MediaState)
async def read_feedback(id: str, c: C, p: P, response: Response) -> dict[str, Any]:
    key, _ = await rating_subject(c, p, id)
    async with c.db.session() as s:
        row = await s.get(RecommendationVote, (p.profile_id, key))
        saved = await s.scalar(
            select(Watchlist.id).where(Watchlist.profile_id == p.profile_id, Watchlist.ref_id.in_({id, key})).limit(1)
        )
    response.headers["Cache-Control"] = "private, no-store"
    return {"id": key, "vote": row.vote if row else None, "saved": saved is not None}


@router.put("/feedback", response_model=Rating)
async def feedback(body: Rating, c: C, p: P) -> dict[str, Any]:
    key, tags = await rating_subject(c, p, body.id)
    genres = sorted({g.strip()[:80] for g in tags if isinstance(g, str) and g.strip()})[:32]
    async with c.db.session() as s:
        if body.vote is None:
            await s.execute(
                delete(RecommendationVote).where(
                    RecommendationVote.profile_id == p.profile_id, RecommendationVote.ref_id == key
                )
            )
        else:
            stmt = insert(RecommendationVote).values(
                profile_id=p.profile_id, ref_id=key, vote=body.vote, genres=genres, updated_at=utcnow()
            )
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["profile_id", "ref_id"],
                    set_={k: getattr(stmt.excluded, k) for k in ("vote", "genres", "updated_at")},
                )
            )
        await s.commit()
    return {"id": key, "vote": body.vote}


class Filters(BaseModel):
    max_minutes: int | None = Field(default=None, ge=10, le=600)
    language: str | None = Field(default=None, min_length=2, max_length=20)


@dataclass
class Group:
    code: str
    filters: Filters
    expires: datetime
    members: dict[str, tuple[str, int]] = field(default_factory=dict)


def groups(request: Request) -> dict[str, Group]:
    existing: dict[str, Group] = getattr(request.app.state, "discovery_groups", {})
    current = {code: g for code, g in existing.items() if g.expires > utcnow()}
    request.app.state.discovery_groups = current
    return current


def member(group: Group | None, p: Principal) -> Group:
    if not group or group.members.get(p.profile_id) != (p.session_id, p.token_version):
        raise NotFound("This invitation has expired or this viewer has not joined.")
    return group


async def shared(c: Container, p: Principal, group: Group) -> dict[str, Any]:
    roster = dict(group.members)
    ranks: dict[str, list[int]] = {}
    own_tiles: dict[str, dict[str, Any]] = {}
    participants: list[Principal] = []
    for pid, (sid, version) in roster.items():
        try:
            principal = await c.auth.session_principal(pid, version, sid)
        except ServiceError as exc:
            if exc.status not in (401, 403) or pid == p.profile_id:
                raise
            if group.members.get(pid) == (sid, version):
                del group.members[pid]
            raise Conflict("A viewer has signed out. Refresh the picks.") from exc
        participants.append(principal)
        async with acting_as(c, principal):
            ctx = await c.catalog.tile_context(pid)
            tiles, _ = await recommendations(c.catalog, pid, ctx, 30)
            for n, tile in enumerate(tiles):
                if not any(source["playable_in_app"] for source in tile["sources"]):
                    continue
                ranks.setdefault(tile["id"], []).append(n)
                if pid == p.profile_id:
                    own_tiles[tile["id"]] = tile
    mutual = [key for key, scores in ranks.items() if len(scores) == len(roster) and key in own_tiles]
    mutual.sort(key=lambda key: (max(ranks[key]), sum(ranks[key]), key))
    picks = []
    for key in mutual:
        if group.filters.max_minutes or group.filters.language:
            detail = await c.catalog.detail(key, p.profile_id)
            if group.filters.max_minutes and (
                not detail.get("runtime_min") or detail["runtime_min"] > group.filters.max_minutes
            ):
                continue
            if group.filters.language:
                preferred = group.filters.language.casefold()
                eligible = True
                for viewer in participants:
                    async with acting_as(c, viewer):
                        choices = await c.playback.options(viewer.profile_id, key, probe=False)
                        if not any(
                            o.get("audio_verified") and preferred in o.get("audio_languages", [])
                            for o in choices["options"]
                        ):
                            eligible = False
                            break
                if not eligible:
                    continue
        picks.append(own_tiles[key])
        if len(picks) == 12:
            break
    if group.members != roster or group.expires <= utcnow():
        raise Conflict("The viewers have changed. Refresh the picks.")
    return {"code": group.code, "member_count": len(roster), "expires_at": group.expires, "tiles": picks}


@router.post("/together", response_model=TogetherPicks)
async def invite(body: Filters, request: Request, c: C, p: P) -> dict[str, Any]:
    active = groups(request)
    if len(active) >= 128 or sum(p.profile_id in g.members for g in active.values()) >= 5:
        raise Conflict("Leave an existing invitation before starting another.")
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    while code in active:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
    group = Group(code, body, utcnow() + timedelta(minutes=30), {p.profile_id: (p.session_id, p.token_version)})
    active[code] = group
    return await shared(c, p, group)


@router.post("/together/{code}/join", response_model=TogetherPicks)
async def join(code: str, request: Request, c: C, p: P) -> dict[str, Any]:
    group = groups(request).get(code.upper())
    if not group:
        raise NotFound("This invitation has expired.")
    if len(group.members) >= 8 and p.profile_id not in group.members:
        raise Conflict("This group is full.")
    group.members[p.profile_id] = (p.session_id, p.token_version)
    return await shared(c, p, group)


@router.get("/together/{code}", response_model=TogetherPicks)
async def picks(code: str, request: Request, c: C, p: P) -> dict[str, Any]:
    return await shared(c, p, member(groups(request).get(code.upper()), p))


@router.delete("/together/{code}", status_code=204)
async def leave(code: str, request: Request, p: P) -> Response:
    active = groups(request)
    group = member(active.get(code.upper()), p)
    del group.members[p.profile_id]
    if not group.members:
        del active[group.code]
    return Response(status_code=204)
