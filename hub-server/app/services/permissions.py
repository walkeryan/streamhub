"""The permission catalogue, the built-in roles and the algebra over them.

Pure functions only: no database, no request. A user's effective permissions are the
role's set plus the user's grants minus the user's denials; effective limits are the
role's limits with the user's overrides laid on top.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.classification import MOVIE_CODES, TV_CODES
from app.services.errors import ServiceError

WATCH = frozenset({"watch.library", "watch.vod", "watch.live"})
PERMISSIONS: frozenset[str] = WATCH | frozenset(
    {
        "watch.adult",
        "rooms.join",
        "rooms.create",
        "recordings.view",
        "recordings.schedule",
        "requests.create",
        "requests.auto_approve",
        "requests.approve",
        "remote.access",
        "users.invite",
        "users.manage",
        "sources.manage",
        "system.manage",
    }
)
# Handing these out, or the Owner role that carries them, is reserved to owners.
OWNER_GRANTED = frozenset({"users.manage", "sources.manage", "system.manage"})
OWNER_ROLE = "owner"

# Least to most permissive. A ceiling admits its own code and everything before it.
MOVIE_ORDER = ("G", "PG", "PG-13", "R", "NC-17")
TV_ORDER = ("TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14", "TV-MA")
assert set(MOVIE_ORDER) <= MOVIE_CODES and set(TV_ORDER) <= TV_CODES


@dataclass(frozen=True, slots=True)
class Limits:
    max_streams: int | None = None
    rating_ceiling_movie: str | None = None
    rating_ceiling_tv: str | None = None
    allow_unrated: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


NO_LIMITS = Limits()


@dataclass(frozen=True, slots=True)
class RoleSpec:
    name: str
    permissions: frozenset[str]
    limits: dict[str, Any] = field(default_factory=dict)
    on_shared_devices: bool = True


BUILTIN_ROLES: dict[str, RoleSpec] = {
    OWNER_ROLE: RoleSpec("Owner", PERMISSIONS),
    "family": RoleSpec(
        "Family",
        WATCH
        | {
            "rooms.join",
            "rooms.create",
            "recordings.view",
            "recordings.schedule",
            "requests.create",
            "requests.auto_approve",
            "remote.access",
            "users.invite",
        },
    ),
    "kid": RoleSpec(
        "Kid",
        WATCH | {"rooms.join", "recordings.view", "requests.create"},
        {"max_streams": 1, "rating_ceiling_movie": "PG", "rating_ceiling_tv": "TV-PG", "allow_unrated": False},
    ),
    "friend": RoleSpec(
        "Friend",
        WATCH | {"rooms.join", "rooms.create", "requests.create", "remote.access"},
        {"max_streams": 1},
        on_shared_devices=False,
    ),
}


def clean_permissions(values: Iterable[Any]) -> list[str]:
    """Validated, de-duplicated, sorted. Unknown names are an error, never silently dropped."""
    names = {str(v) for v in values}
    unknown = names - PERMISSIONS
    if unknown:
        raise ServiceError(f"unknown permission: {', '.join(sorted(unknown))}", code="invalid_permission")
    return sorted(names)


def clean_limits(values: Mapping[str, Any]) -> dict[str, Any]:
    """Validated limit fields. Only the keys present are returned, so this serves overrides too."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key == "max_streams":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100):
                raise ServiceError("max_streams must be between 1 and 100, or null", code="invalid_limits")
        elif key == "rating_ceiling_movie":
            if value is not None and value not in MOVIE_ORDER:
                raise ServiceError("unknown movie rating ceiling", code="invalid_limits")
        elif key == "rating_ceiling_tv":
            if value is not None and value not in TV_ORDER:
                raise ServiceError("unknown tv rating ceiling", code="invalid_limits")
        elif key == "allow_unrated":
            if not isinstance(value, bool):
                raise ServiceError("allow_unrated must be true or false", code="invalid_limits")
        else:
            raise ServiceError(f"unknown limit: {key}", code="invalid_limits")
        out[key] = value
    return out


def effective_permissions(
    role_permissions: Iterable[str], grants: Iterable[str], denials: Iterable[str]
) -> frozenset[str]:
    """Role plus grants minus denials. A denial wins over a grant; unknown names never count."""
    return ((frozenset(role_permissions) | frozenset(grants)) - frozenset(denials)) & PERMISSIONS


def effective_limits(role_limits: Mapping[str, Any], overrides: Mapping[str, Any]) -> Limits:
    """The role's limits with the user's overrides on top. An override of null lifts that limit."""
    merged = {**role_limits, **overrides}
    streams = merged.get("max_streams")
    movie = merged.get("rating_ceiling_movie")
    tv = merged.get("rating_ceiling_tv")
    return Limits(
        max_streams=streams if isinstance(streams, int) and not isinstance(streams, bool) and streams > 0 else None,
        rating_ceiling_movie=movie if movie in MOVIE_ORDER else None,
        rating_ceiling_tv=tv if tv in TV_ORDER else None,
        allow_unrated=bool(merged.get("allow_unrated", True)),
    )


def _rank(order: tuple[str, ...], code: str | None) -> int:
    return order.index(code) if code in order else len(order)  # no ceiling outranks every ceiling


def is_wider(a: frozenset[str], a_limits: Limits, b: frozenset[str], b_limits: Limits) -> bool:
    """Does access ``a`` exceed access ``b`` in any respect: a permission, a looser limit?"""
    if a - b:
        return True
    if (a_limits.max_streams or 10**9) > (b_limits.max_streams or 10**9):
        return True
    if _rank(MOVIE_ORDER, a_limits.rating_ceiling_movie) > _rank(MOVIE_ORDER, b_limits.rating_ceiling_movie):
        return True
    if _rank(TV_ORDER, a_limits.rating_ceiling_tv) > _rank(TV_ORDER, b_limits.rating_ceiling_tv):
        return True
    return a_limits.allow_unrated and not b_limits.allow_unrated
