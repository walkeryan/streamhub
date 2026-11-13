"""Who may see and schedule recordings, and who sees everyone's.

The only place that decides. Callers ask these functions instead of reading the
principal; each answers from one permission.
"""

from __future__ import annotations

from app.services.auth import Principal


def can_view(p: Principal) -> bool:
    return "recordings.view" in p.permissions


def can_schedule(p: Principal) -> bool:
    return "recordings.schedule" in p.permissions


def sees_all(p: Principal) -> bool:
    """Everyone's recordings and rules, not only one's own: whoever manages the household's users."""
    return "users.manage" in p.permissions
