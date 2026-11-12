"""Who may request, auto-approve and approve.

The only place that decides. Callers ask these functions instead of reading the
principal, so a later permission system replaces the bodies without touching them.
"""

from __future__ import annotations

from app.services.auth import Principal


def can_request(p: Principal) -> bool:
    return True


def auto_approve(p: Principal) -> bool:
    return p.is_admin


def can_approve(p: Principal) -> bool:
    return p.is_admin
