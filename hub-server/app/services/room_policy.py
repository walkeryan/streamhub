"""Who may create and join watch rooms.

The one place the room endpoints ask, so the permission system (``rooms.create``,
``rooms.join``) can back these two functions without touching the endpoints. Until it
does, every signed-in profile may do both.
"""

from __future__ import annotations

from app.services.auth import Principal


def can_create(p: Principal) -> bool:
    return True


def can_join(p: Principal) -> bool:
    return True
