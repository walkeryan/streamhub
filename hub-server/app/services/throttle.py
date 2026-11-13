"""Counting attempts, in memory: the sign-in throttle and the per-address request limiter.

Both keep their state in this process, which is why the server runs one worker (as the
Google and Plex sign-in transactions already require).
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.services.sessions import RateLimited


# ---------------------------------------------------------------- sign-in attempts
@dataclass(slots=True)
class _Record:
    attempts: list[float] = field(default_factory=list)
    locked_until: float = 0.0
    strikes: int = 0
    last: float = 0.0


@dataclass(frozen=True, slots=True)
class Ticket:
    """One attempt, counted against its keys from the moment it was let through."""

    keys: tuple[str, ...]
    at: float


class LoginThrottle:
    """Attempts are counted when they start, not when they fail, so a burst sent at once is
    held to the same number as one sent slowly. A key that fills its allowance with failures
    is locked, for twice as long each time, up to an hour; a day of quiet forgets that.

    Keys carry a one-letter scope: ``u:`` a name being signed in to, ``a:`` a client address.
    """

    WINDOW_S = 15 * 60
    BASE_LOCK_S = 60
    MAX_LOCK_S = 3600
    FORGET_S = 24 * 3600
    MAX_KEYS = 20_000

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        # scope -> (attempts allowed per window, before a lock)
        self.limits: dict[str, tuple[int]] = {"u": (5,), "a": (20,), "p": (10,)}
        # (failures by everyone, per this many seconds)
        self.ceiling: tuple[int, int] = (300, 300)
        self._records: dict[str, _Record] = {}
        self._all: deque[float] = deque()

    def _record(self, key: str, now: float) -> _Record:
        record = self._records.get(key)
        if record is None:
            if len(self._records) >= self.MAX_KEYS:
                self._records = {
                    k: r for k, r in self._records.items() if r.locked_until > now or now - r.last < self.WINDOW_S
                }
            record = self._records[key] = _Record()
        if record.strikes and now - record.last > self.FORGET_S:
            record.strikes = 0
        record.attempts = [t for t in record.attempts if now - t < self.WINDOW_S]
        return record

    def begin(self, *keys: str) -> Ticket:
        """Count one attempt against every key, or refuse it with how long to wait. No awaits:
        between the check and the count nothing else runs."""
        now = self.clock()
        limit, window = self.ceiling
        while self._all and now - self._all[0] >= window:
            self._all.popleft()
        if len(self._all) >= limit:
            raise RateLimited(max(1, int(window - (now - self._all[0]) + 0.999)))
        records = [(key, self._record(key, now)) for key in keys]
        for key, record in records:
            if record.locked_until > now:
                raise RateLimited(max(1, int(record.locked_until - now + 0.999)))
            if len(record.attempts) >= self.limits[key[0]][0]:
                # Full of attempts still being judged: wait for the oldest to age out.
                raise RateLimited(max(1, int(self.WINDOW_S - (now - record.attempts[0]) + 0.999)))
        for _key, record in records:
            record.attempts.append(now)
            record.last = now
        self._all.append(now)
        return Ticket(tuple(keys), now)

    def fail(self, ticket: Ticket) -> dict[str, int]:
        """The attempt was wrong. Returns the keys this failure locked, with the seconds each is locked for."""
        now = self.clock()
        locked: dict[str, int] = {}
        for key in ticket.keys:
            record = self._records.get(key)
            if record is None or record.locked_until > now:
                continue
            if len(record.attempts) >= self.limits[key[0]][0]:
                record.strikes += 1
                seconds = min(self.BASE_LOCK_S * 2 ** (record.strikes - 1), self.MAX_LOCK_S)
                record.locked_until, record.attempts, record.last = now + seconds, [], now
                locked[key] = seconds
        return locked

    def succeed(self, ticket: Ticket) -> None:
        """The attempt was right: it is not held against anyone, and the name starts clean."""
        for key in ticket.keys:
            record = self._records.get(key)
            if record is None:
                continue
            if key[0] == "a":
                if ticket.at in record.attempts:
                    record.attempts.remove(ticket.at)
            else:
                self._records.pop(key, None)
        try:
            self._all.remove(ticket.at)
        except ValueError:
            pass


# ---------------------------------------------------------------- per-address request limits
# bucket -> (requests, per this many seconds), for one client address. Generous for a household
# behind one address, small next to what guessing or flooding needs.
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "login": (60, 60),
    "refresh": (120, 60),
    "oauth": (30, 60),
    "plex": (20, 60),
    "device": (10, 60),
    "device_poll": (60, 60),
    "device_lookup": (30, 60),
    "invite": (20, 60),
    "password": (30, 60),
    "profiles": (60, 60),
    "qr": (60, 60),
}


class RateLimiter:
    """So many requests of one kind from one client address in a sliding window, then 429."""

    MAX_KEYS = 50_000

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.limits = dict(RATE_LIMITS)
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def hit(self, bucket: str, address: str | None) -> None:
        limit, window = self.limits[bucket]
        now = self.clock()
        # An address that cannot be known shares one allowance with every other such request.
        key = (bucket, address or "unknown")
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.MAX_KEYS:
                self._hits = {k: q for k, q in self._hits.items() if q and now - q[-1] < self.limits[k[0]][1]}
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= window:
            hits.popleft()
        if len(hits) >= limit:
            raise RateLimited(max(1, math.ceil(window - (now - hits[0]))))
        hits.append(now)
