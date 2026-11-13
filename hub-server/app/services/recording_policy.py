"""Who may see and schedule recordings.

The only place that decides. Callers ask these functions instead of reading the
principal, so a later permission system replaces the bodies without touching them.
"""

from __future__ import annotations

from app.services.auth import Principal


def can_view(p: Principal) -> bool:
    return True


def can_schedule(p: Principal) -> bool:
    return not p.kids
