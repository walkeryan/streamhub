"""Per-request context (public base URL) used when services build absolute URLs."""

from __future__ import annotations

from contextvars import ContextVar

public_base: ContextVar[str] = ContextVar("public_base", default="")
is_https: ContextVar[bool] = ContextVar("is_https", default=False)


def absolute(path: str) -> str:
    return public_base.get().rstrip("/") + path
