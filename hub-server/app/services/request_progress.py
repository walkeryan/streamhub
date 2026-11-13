"""What a request is doing right now, worked out from the request manager's last answer.

``state`` (see ``RequestService``) says where a request stands in StreamHub's own approval
flow. ``phase`` says what is actually happening to the title, and only ever claims what an
answer showed:

    requested             waiting for approval here                  (state pending_approval)
    approved              approved, the manager has not taken it yet (state approved), or the
                          manager holds it for an approval of its own (request status 1)
    searching             the manager has it and nothing is in its download queue
    downloading           the download queue has an unfinished item for it
    importing             every queue item has finished and the title is not available yet
    partially_available   series: some of the requested seasons can be watched
    available | declined | failed
    stalled               every unfinished queue item reports trouble, or the bytes done have
                          not changed for ``STALL_AFTER_S``
    unknown               the manager did not answer, or its answer said nothing this code knows

Nothing here is optimistic: a field that is absent or malformed arrives as ``None`` or as an
empty queue, and no rule below turns that into progress.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.adapters.seerr import ManagerSnapshot, QueueItem
from app.adapters.seerr.client import (
    MEDIA_AVAILABLE,
    MEDIA_PARTIALLY_AVAILABLE,
    MEDIA_PENDING,
    MEDIA_PROCESSING,
    MEDIA_UNKNOWN,
    REQUEST_APPROVED,
    REQUEST_COMPLETED,
    REQUEST_DECLINED,
    REQUEST_FAILED,
    REQUEST_PENDING,
)

PHASES = (
    "requested",
    "approved",
    "searching",
    "downloading",
    "importing",
    "partially_available",
    "available",
    "declined",
    "failed",
    "stalled",
    "unknown",
)
# What a poll can leave on a request that stays open.
OPEN_POLLED_PHASES = ("approved", "searching", "downloading", "importing", "partially_available", "stalled", "unknown")

# How often the manager is asked about one request. A download moves by the second, so its
# bar is refreshed every 15 s; a search takes minutes to days, so everything else is asked
# every 3 minutes. Requests waiting for approval or not yet sent are not polled at all: they
# change when somebody acts on them here.
FAST_S = 15
SLOW_S = 180
# A title that can be had is usually found within a minute or two of asking, so for its first
# 10 minutes with the manager a request is asked about every 30 s: the step from searching to
# downloading is seen soon after it happens. At most 20 calls per request, once.
FRESH_S = 30
FRESH_FOR_S = 600
# A request the manager failed to answer for is asked again after 30 s, then 60, 120, 240,
# and from then on every 5 minutes, which is also how often the full sweep runs.
BACKOFF_FIRST_S = 30
BACKOFF_MAX_S = 300
# A download that claims to be running but whose bytes done have not changed for this long,
# or finished downloads that have not been imported for this long, are reported as stalled.
# Queued and paused items are waiting on purpose and never trip it; neither does a search,
# which may rightly last until the title is released.
STALL_AFTER_S = 30 * 60

DETAIL_MAX = 200

# The download clients' words for an item, as the manager passes them on.
_WAITING = frozenset({"queued", "delay"})
_TROUBLE = {
    "warning": "The download client reported a warning.",
    "failed": "The download failed.",
    "error": "The download failed.",
    "stalled": "The download is stalled.",
    "downloadclientunavailable": "The download client is unavailable.",
}

# Anything that could be an address or a place on a disk: a slash, a dotted host or IP, a bracketed IPv6.
_ADDRESS_RE = re.compile(r"[/\\]|\b[\w-]+\.[a-z]{2,}\b|\b\d{1,3}(?:\.\d{1,3}){3}\b|\[[0-9a-f:.]+\]", re.IGNORECASE)


@dataclass(frozen=True)
class Progress:
    phase: str
    percent: int | None = None
    eta: datetime | None = None
    detail: str | None = None
    # What the stall clock watches: it restarts whenever this changes. None: nothing to watch.
    mark: str | None = None

    @property
    def stallable(self) -> bool:
        return self.mark is not None


def interval_s(phase: str, sent_ago_s: float | None = None) -> int:
    """Seconds until a request in ``phase``, sent to the manager ``sent_ago_s`` ago, is asked about again."""
    if phase in ("downloading", "importing"):
        return FAST_S
    return FRESH_S if sent_ago_s is not None and sent_ago_s < FRESH_FOR_S else SLOW_S


def backoff_s(failures: int) -> int:
    return min(BACKOFF_FIRST_S * 2 ** min(max(failures, 1) - 1, 8), BACKOFF_MAX_S)


def safe_detail(text: str | None, fallback: str) -> str | None:
    """A message that came from the manager, or ``fallback`` when it names a host, a URL or a path."""
    text = (text or "").strip()
    if not text:
        return None
    return fallback if _ADDRESS_RE.search(text) else text[:DETAIL_MAX]


def _finished(item: QueueItem) -> bool:
    return item.status == "completed" or (bool(item.size) and item.size_left == 0)


def _percent(queue: Sequence[QueueItem]) -> tuple[int | None, int | None]:
    """Bytes done over bytes total across the queue, and the bytes done; None when any size is unknown."""
    total = done = 0
    for item in queue:
        if not item.size or item.size_left is None:
            return None, None
        total += item.size
        done += item.size - item.size_left
    # Floored, so 100 is only said when no byte is missing.
    return (done * 100 // total, done) if total else (None, None)


def _eta(unfinished: Sequence[QueueItem], now: datetime) -> datetime | None:
    """When the last unfinished item is due; None unless every one of them gives a time still ahead."""
    latest: datetime | None = None
    for item in unfinished:
        due = item.eta or (now + timedelta(seconds=item.time_left_s) if item.time_left_s is not None else None)
        if due is None or due <= now:
            return None
        latest = due if latest is None or due > latest else latest
    return latest


def _seasons(snapshot: ManagerSnapshot, seasons: Sequence[int] | None) -> tuple[bool, bool, str | None]:
    """For a series: (every requested season is available, some of it is, how to say how much)."""
    wanted = tuple(seasons or snapshot.seasons_requested)
    if not any(n in snapshot.season_status for n in wanted):
        # Nothing to count against: the manager's word for the whole series is all there is.
        return False, snapshot.media_status == MEDIA_PARTIALLY_AVAILABLE, None
    have = sum(1 for n in wanted if snapshot.season_status.get(n) == MEDIA_AVAILABLE)
    if have == len(wanted):
        return True, False, None
    if len(wanted) > 1:
        return False, have > 0, f"{have} of {len(wanted)} seasons available"
    partly = snapshot.season_status.get(wanted[0]) == MEDIA_PARTIALLY_AVAILABLE
    return False, partly, "Some episodes available" if partly else None


def _queue(queue: Sequence[QueueItem], media_type: str, partial: str | None, now: datetime) -> Progress:
    unfinished = [item for item in queue if not _finished(item)]
    if not unfinished:
        return Progress("importing", mark="importing")
    troubled = [item for item in unfinished if item.status in _TROUBLE]
    if len(troubled) == len(unfinished):
        return Progress("stalled", detail=_TROUBLE[troubled[0].status])
    percent, done = _percent(queue)
    waiting = all(item.status in _WAITING or item.status == "paused" for item in unfinished)
    if troubled:
        detail: str | None = (
            f"{len(troubled)} of {len(queue)} downloads {'has' if len(troubled) == 1 else 'have'} a problem"
        )
    elif partial:
        detail = partial
    elif waiting:
        detail = "Paused" if any(item.status == "paused" for item in unfinished) else "Queued"
    elif media_type == "tv" and len(queue) > 1:
        # Of the episodes in the download queue right now, not of the season.
        detail = f"{len(queue) - len(unfinished)} of {len(queue)} episodes"
    else:
        detail = None
    # Only a download that says it is running, with sizes to compare, can be caught standing still.
    mark = f"downloading:{done}" if done is not None and not waiting else None
    return Progress("downloading", percent=percent, eta=_eta(unfinished, now), detail=detail, mark=mark)


def derive(snapshot: ManagerSnapshot, *, media_type: str, seasons: Sequence[int] | None, now: datetime) -> Progress:
    """The phase one answer of the manager stands for. The first rule that applies wins."""
    request, media = snapshot.request_status, snapshot.media_status
    if request == REQUEST_DECLINED:
        return Progress("declined")
    complete, partly, partial = _seasons(snapshot, seasons) if media_type == "tv" else (False, False, None)
    if media == MEDIA_AVAILABLE or request == REQUEST_COMPLETED or complete:
        return Progress("available")
    if request == REQUEST_FAILED:
        return Progress("failed")
    if snapshot.queue:
        return _queue(snapshot.queue, media_type, partial if partly else None, now)
    if partly:
        return Progress("partially_available", detail=partial)
    if request == REQUEST_PENDING or (request is None and media == MEDIA_PENDING):
        return Progress("approved", detail="Waiting for approval in the request manager")
    known = (None, MEDIA_UNKNOWN, MEDIA_PENDING, MEDIA_PROCESSING, MEDIA_PARTIALLY_AVAILABLE)
    if (request == REQUEST_APPROVED and media in known) or (request is None and media == MEDIA_PROCESSING):
        return Progress("searching")
    return Progress("unknown")
