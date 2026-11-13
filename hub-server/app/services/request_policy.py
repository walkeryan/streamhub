"""Who may request, auto-approve and approve.

The only place that decides. Callers ask these functions instead of reading the
principal; each answers from one permission.
"""

from __future__ import annotations

from app.services.auth import Principal


def can_request(p: Principal) -> bool:
    return "requests.create" in p.permissions


def auto_approve(p: Principal) -> bool:
    return "requests.auto_approve" in p.permissions


def can_approve(p: Principal) -> bool:
    return "requests.approve" in p.permissions
