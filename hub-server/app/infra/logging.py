"""Structured logging with a secret-redaction filter.

Every record passes through :class:`RedactionFilter`, which removes registered
secrets (passwords, tokens) and credential-looking URL parts before anything is
emitted.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "***"

_SENSITIVE_PARAMS = {
    "password",
    "pass",
    "pw",
    "username",
    "user",
    "api_key",
    "apikey",
    "token",
    "x-plex-token",
    "access_token",
    "auth",
    "key",
}
_QUERY_RE = re.compile(
    r"(?i)([?&](?:password|pass|pw|username|user|api_key|apikey|token|x-plex-token|access_token|auth)=)[^&\s\"']+"
)
# Xtream style path credentials: /live/{user}/{pass}/123.ts, /movie/.., /series/.., /timeshift/..
_XTREAM_PATH_RE = re.compile(r"(?i)(/(?:live|movie|series|timeshift)/)([^/\s]+)/([^/\s]+)/")
_AUTH_HEADER_RE = re.compile(r"(?i)((?:authorization|x-plex-token|x-emby-token)[\"']?\s*[:=]\s*[\"']?)[^\"',\s}]+")
_BASIC_AUTH_RE = re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@")


class SecretRegistry:
    """Process-wide set of literal secret strings that must never be logged."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def add(self, *values: str | None) -> None:
        with self._lock:
            for v in values:
                if v and len(v) >= 4:
                    self._secrets.add(v)

    def scrub(self, text: str) -> str:
        with self._lock:
            items = sorted(self._secrets, key=len, reverse=True)
        for s in items:
            if s in text:
                text = text.replace(s, REDACTED)
        return text


secret_registry = SecretRegistry()


def redact_text(text: str) -> str:
    text = _BASIC_AUTH_RE.sub(r"\1***:***@", text)
    text = _XTREAM_PATH_RE.sub(r"\1***/***/", text)
    text = _QUERY_RE.sub(r"\1" + REDACTED, text)
    text = _AUTH_HEADER_RE.sub(r"\1" + REDACTED, text)
    return secret_registry.scrub(text)


def redact_url(url: str | None) -> str | None:
    """Redact credentials from a URL meant for display (not for playback)."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact_text(url)
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[1]
    query = urlencode(
        [(k, REDACTED if k.lower() in _SENSITIVE_PARAMS else v) for k, v in parse_qsl(parts.query, True)],
        safe="*",
    )
    path = _XTREAM_PATH_RE.sub(r"\1***/***/", parts.path)
    return secret_registry.scrub(urlunsplit((parts.scheme, netloc, path, query, parts.fragment)))


class RedactionFilter(logging.Filter):
    """Redacts the message and any string args in place (keeps args, which some formatters,
    e.g. uvicorn's access formatter, read positionally)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_redacted", False):
            return True
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_text(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact_text(v) if isinstance(v, str) else v for k, v in record.args.items()}
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        record._redacted = True
        return True


_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = redact_text(str(v)) if isinstance(v, str) else v
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        if extras:
            base += " " + " ".join(f"{k}={redact_text(str(v))}" for k, v in extras.items())
        return base


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_streamhub", False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler._streamhub = True  # type: ignore[attr-defined]
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "apscheduler", "aiosqlite", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # uvicorn access logs include query strings — make sure they are filtered too.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.addFilter(RedactionFilter())
        for h in lg.handlers:
            h.addFilter(RedactionFilter())
