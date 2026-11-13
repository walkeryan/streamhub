"""The phase rules: which answer of the request manager means what."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.adapters.seerr import ManagerSnapshot, QueueItem
from app.services import request_progress as rp

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


def item(status: str = "downloading", size: int | None = 1000, left: int | None = 500, **kw: Any) -> QueueItem:
    values: dict[str, Any] = {"eta": None, "time_left_s": None, "season": None, "episode": None, **kw}
    return QueueItem(status=status, size=size, size_left=left, **values)


def derive(media_type: str = "movie", seasons: list[int] | None = None, **snapshot: Any) -> rp.Progress:
    return rp.derive(ManagerSnapshot(tracked=True, **snapshot), media_type=media_type, seasons=seasons, now=NOW)


@pytest.mark.parametrize(
    ("request_status", "media_status", "phase"),
    [
        (1, 2, "approved"),  # waiting for approval inside the manager
        (2, 3, "searching"),
        (2, 2, "searching"),
        (2, 1, "searching"),
        (2, None, "searching"),
        (None, 3, "searching"),
        (None, 2, "approved"),
        (2, 5, "available"),
        (5, 3, "available"),
        (3, 5, "declined"),
        (3, None, "declined"),
        (4, 3, "failed"),
        (2, 7, "unknown"),  # a media status this code does not know
        (9, 3, "unknown"),
        (None, None, "unknown"),
        (None, 1, "unknown"),
    ],
)
def test_phase_without_a_queue(request_status: int | None, media_status: int | None, phase: str) -> None:
    got = derive(request_status=request_status, media_status=media_status)
    assert (got.phase, got.percent, got.eta) == (phase, None, None)


def test_only_a_queue_item_means_downloading() -> None:
    assert derive(request_status=2, media_status=3).phase == "searching"
    got = derive(request_status=2, media_status=3, queue=(item(size=4000, left=1000),))
    assert (got.phase, got.percent, got.detail) == ("downloading", 75, None)
    # Finished, declined and failed requests are not reopened by a leftover queue entry.
    assert derive(request_status=2, media_status=5, queue=(item(),)).phase == "available"
    assert derive(request_status=3, media_status=3, queue=(item(),)).phase == "declined"
    assert derive(request_status=4, media_status=3, queue=(item(),)).phase == "failed"


@pytest.mark.parametrize(
    ("sizes", "percent"),
    [
        ([(1000, 1000)], 0),
        ([(1000, 1)], 99),  # never 100 while a byte is missing
        ([(3, 2)], 33),
        ([(1000, 500), (3000, 3000)], 12),
        ([(1000, 0), (1000, 500)], 75),
        ([(0, 0)], None),
        ([(None, None)], None),
        ([(1000, None)], None),
        ([(1000, 500), (0, 0)], None),  # one unknown size makes the total unknown
        ([(1000, 500), (None, None)], None),
    ],
)
def test_percent_is_bytes_done_over_bytes_total(
    sizes: list[tuple[int | None, int | None]], percent: int | None
) -> None:
    got = derive(request_status=2, media_status=3, queue=tuple(item(size=s, left=n) for s, n in sizes))
    assert (got.phase, got.percent) == ("downloading", percent)


def test_eta_is_when_the_last_item_finishes() -> None:
    soon, later = NOW + timedelta(minutes=5), NOW + timedelta(minutes=12)
    got = derive(request_status=2, media_status=3, queue=(item(eta=soon), item(eta=later)))
    assert got.eta == later
    # timeLeft stands in where no completion time is given.
    got = derive(request_status=2, media_status=3, queue=(item(eta=soon), item(time_left_s=1800)))
    assert got.eta == NOW + timedelta(minutes=30)
    # An item without either leaves the whole request without one; so does a time that has passed.
    assert derive(request_status=2, media_status=3, queue=(item(eta=soon), item())).eta is None
    assert derive(request_status=2, media_status=3, queue=(item(eta=NOW - timedelta(minutes=1)),)).eta is None
    assert derive(request_status=2, media_status=3, queue=(item(time_left_s=0),)).eta is None
    # A finished item no longer counts.
    got = derive(request_status=2, media_status=3, queue=(item(left=0), item(eta=soon)))
    assert got.eta == soon


def test_waiting_items_say_so() -> None:
    queued = derive(request_status=2, media_status=3, queue=(item("queued", left=1000), item("delay", left=1000)))
    assert (queued.phase, queued.percent, queued.detail, queued.stallable) == ("downloading", 0, "Queued", False)
    paused = derive(request_status=2, media_status=3, queue=(item("paused"), item("queued", left=1000)))
    assert (paused.phase, paused.detail, paused.stallable) == ("downloading", "Paused", False)
    moving = derive(request_status=2, media_status=3, queue=(item("paused"), item("downloading")))
    assert (moving.detail, moving.stallable) == (None, True)


def test_series_detail_counts_episodes_in_the_queue() -> None:
    queue = (item(left=0), item("completed", size=None, left=None), *(item() for _ in range(6)))
    got = derive("tv", request_status=2, media_status=3, queue=queue)
    assert (got.phase, got.detail) == ("downloading", "2 of 8 episodes")
    assert derive("tv", request_status=2, media_status=3, queue=(item(),)).detail is None
    assert derive("movie", request_status=2, media_status=3, queue=(item(), item())).detail is None


def test_finished_downloads_not_yet_available_are_importing() -> None:
    got = derive(request_status=2, media_status=3, queue=(item("completed", left=0), item("downloading", left=0)))
    assert (got.phase, got.percent, got.stallable) == ("importing", None, True)
    # A size of zero is not a finished download.
    assert derive(request_status=2, media_status=3, queue=(item(size=0, left=0),)).phase == "downloading"


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        ("warning", "The download client reported a warning."),
        ("failed", "The download failed."),
        ("error", "The download failed."),
        ("stalled", "The download is stalled."),
        ("downloadclientunavailable", "The download client is unavailable."),
    ],
)
def test_a_troubled_download_is_stalled(status: str, detail: str) -> None:
    got = derive(request_status=2, media_status=3, queue=(item(status),))
    assert (got.phase, got.percent, got.detail) == ("stalled", None, detail)
    # While another item still moves the request is downloading, and says one is in trouble.
    mixed = derive("tv", request_status=2, media_status=3, queue=(item(status), item(), item()))
    assert (mixed.phase, mixed.percent, mixed.detail) == ("downloading", 50, "1 of 3 downloads has a problem")
    # A finished item does not make a troubled rest healthy.
    assert derive(request_status=2, media_status=3, queue=(item(status), item(left=0))).phase == "stalled"


def test_series_partial_availability() -> None:
    def show(seasons: list[int] | None, season_status: dict[int, int], **kw: Any) -> rp.Progress:
        values = {"request_status": 2, "media_status": 4, **kw}
        return derive("tv", seasons, season_status=season_status, **values)

    some = show([1, 2, 3], {1: 5, 2: 3, 3: 1})
    assert (some.phase, some.percent, some.detail) == ("partially_available", None, "1 of 3 seasons available")
    # Every requested season is there: the request is done even though the series is not complete.
    assert show([1, 2], {1: 5, 2: 5, 3: 1}).phase == "available"
    # Without a pick here, the seasons the manager lists for the request are the requested ones.
    assert show(None, {1: 5, 2: 5, 3: 1}, seasons_requested=(1, 2)).phase == "available"
    assert show(None, {1: 5, 2: 3}, seasons_requested=(1, 2)).detail == "1 of 2 seasons available"
    # One season, some of its episodes.
    one = show([2], {1: 1, 2: 4})
    assert (one.phase, one.detail) == ("partially_available", "Some episodes available")
    # Nothing to count against: the manager's word for the series stands, without a count.
    assert (show(None, {}).phase, show(None, {}).detail) == ("partially_available", None)
    # Seasons other than the requested ones being there says nothing about this request.
    assert show([3], {1: 5, 2: 5, 3: 3}, media_status=4).phase == "searching"
    # More on the way: the bar is shown, the count of what can be watched stays.
    more = show([1, 2], {1: 5, 2: 3}, queue=(item(size=1000, left=250),))
    assert (more.phase, more.percent, more.detail) == ("downloading", 75, "1 of 2 seasons available")
    # A film is never partly there.
    assert derive("movie", request_status=2, media_status=4).phase == "searching"


def test_intervals_and_back_off() -> None:
    assert rp.interval_s("downloading") == rp.interval_s("importing") == rp.FAST_S
    assert 15 <= rp.FAST_S <= 30
    for phase in ("searching", "approved", "partially_available", "stalled", "unknown"):
        assert rp.interval_s(phase) == rp.SLOW_S
    assert 120 <= rp.SLOW_S <= 600
    # Just sent: asked about sooner for a while, so the start of a download is seen quickly.
    assert rp.interval_s("searching", 0) == rp.interval_s("searching", rp.FRESH_FOR_S - 1) == rp.FRESH_S == 30
    assert rp.interval_s("searching", rp.FRESH_FOR_S) == rp.SLOW_S
    assert rp.interval_s("downloading", 0) == rp.FAST_S
    assert [rp.backoff_s(n) for n in range(1, 7)] == [30, 60, 120, 240, 300, 300]
    assert rp.backoff_s(0) == 30 and rp.backoff_s(500) == 300


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("The request manager answered 500.", True),
        ("The request manager answered 500: Unable to connect to the download service", True),
        ("No request manager is configured.", True),
        ("failed: http://media.example.test:7878/api rejected", False),
        ("could not write /data/media/films/file.mkv", False),
        ("could not write C:\\media\\file.mkv", False),
        ("lookup of tracker.example.test failed", False),
        ("refused by 192.0.2.10:8080", False),
        ("refused by [2001:db8::1]", False),
    ],
)
def test_details_never_carry_addresses_or_paths(text: str, kept: bool) -> None:
    assert rp.safe_detail(text, "fallback") == (text if kept else "fallback")
    assert rp.safe_detail(None, "fallback") is None
    assert rp.safe_detail("x" * 500, "fallback") == "x" * 200
