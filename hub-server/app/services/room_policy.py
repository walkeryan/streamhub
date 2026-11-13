"""Who may create, join and close watch rooms.

The one place the room endpoints ask. Each answer is one permission: ``rooms.create``,
``rooms.join``, and for closing a room that is not one's own, ``users.manage``.
"""

from __future__ import annotations

from app.services.auth import Principal


def can_create(p: Principal) -> bool:
    return "rooms.create" in p.permissions


def can_join(p: Principal) -> bool:
    return "rooms.join" in p.permissions


def can_close(p: Principal, is_host: bool) -> bool:
    """The host ends their own room; whoever manages the household's users may end anyone's."""
    return is_host or "users.manage" in p.permissions
