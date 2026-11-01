"""Provider-advertised archive windows. No recording or provider probes."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.infra.models import Channel
from app.services.errors import ServiceError


def archive_window(channel: Channel, *, now: datetime | None = None) -> dict[str, Any]:
    edge = (now or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
    data = channel.data or {}
    kind = str(data.get("catchup_type") or "").lower()
    supported = channel.catchup_days > 0 and (
        kind in {"xtream", "shift", "timeshift", "flussonic", "fs"}
        or (kind in {"default", "append"} and bool(data.get("catchup_source")))
    )
    return {
        "mode": "provider_archive" if supported else "unavailable",
        "advertised_start": edge - timedelta(hours=1) if supported else None,
        "advertised_end": edge if supported else None,
        "verified": False,
        "recording_enabled": False,
        "granularity_seconds": 60,
        "reason": "Provider advertises archive access; playback availability is not verified."
        if supported
        else "No supported provider archive is advertised. Local recording is disabled.",
    }


def archive_request(channel: Channel, requested: datetime, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    window = archive_window(channel, now=now)
    if window["mode"] != "provider_archive":
        raise ServiceError("Provider archive unavailable; local recording is disabled.", code="rewind_unavailable")
    if requested.tzinfo is None:
        raise ServiceError("An explicit UTC offset is required.", code="invalid_rewind_time")
    start = requested.astimezone(UTC).replace(second=0, microsecond=0)
    end = window["advertised_end"]
    if start < window["advertised_start"] or start >= end:
        raise ServiceError(
            "Requested position is outside the advertised rewind window. Go Live or choose a newer time.",
            code="rewind_expired",
        )
    return start, end
