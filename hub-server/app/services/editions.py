"""Edition names come from provider metadata or explicitly named cuts. Runtime compares unnamed encodes."""

import hashlib
import re
from typing import Any

from app.infra.models import Title
from app.services.media_probe import cached_probe

CUT = re.compile(
    r"\b(director['’]?s cut|theatrical cut|extended (?:cut|edition)|final cut|unrated (?:cut|edition))\b", re.I
)
TAG = re.compile(r"\{edition-([^{}]{1,100})\}", re.I)


def runtime(row: Title) -> int | None:
    probe = cached_probe(row.extra) or {}
    return row.duration_ms or probe.get("duration_ms")


def compatible_runtime(a: int | None, b: int | None) -> bool:
    return bool(a and b and abs(a - b) <= max(1000, min(90000, min(a, b) * 0.02)))


def same_edition(saved: str, current: str) -> bool:
    if saved == current:
        return True
    if saved.startswith("runtime:") and current.startswith("runtime:"):
        first, second = saved[8:], current[8:]
        return first.isdigit() and second.isdigit() and compatible_runtime(int(first), int(second))
    return False


def safe_position(position: int, duration: int | None) -> int:
    return position if position >= 0 and (not duration or position < duration) else 0


def edition_name(row: Title) -> str:
    named = (row.extra or {}).get("edition_title")
    if isinstance(named, str) and named.strip():
        return named.strip()[:100]
    match = TAG.search(row.title) or CUT.search(row.title)
    return match[1].strip() if match else ""


def edition_info(row: Title, peers: list[Title]) -> dict[str, Any]:
    length = runtime(row)
    label = edition_name(row)
    if label:
        key = "named:" + hashlib.sha256(label.casefold().encode()).hexdigest()[:24]
    elif length:
        same = [
            value
            for other in peers
            if not edition_name(other) and (value := runtime(other)) is not None and compatible_runtime(value, length)
        ]
        key = f"runtime:{min(same) if same else length}"
        label = "Unlabelled edition"
    else:
        key = "variant:" + hashlib.sha256(f"{row.source_id}:{row.ext_id}".encode()).hexdigest()[:24]
        label = "Unlabelled edition"
    return {"edition_id": key, "edition_label": label, "runtime_ms": length}
