"""Team facts for football listings, from ESPN's public site API (no key).

A football listing that names both sides is matched to one game of the day's scoreboard: the
teams' names, colours, top-25 ranking and records, the game clock and, for a game on now, the win
probability (the pregame prediction before kickoff, the live estimate during the game). Scores are
never read out of it.

Everything here is a best effort around the Sports response, never a part of it: lookups are lazy
(only the days and games a page lists), cached in memory, bounded by a short budget per response,
and a failure of any kind is "no facts". A lookup that outlives the budget keeps running and fills
the cache for the screen's next refresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx

if TYPE_CHECKING:
    from app.services.matchup_art import MatchupArt

log = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football"
COLLEGE, NFL = "college-football", "nfl"
# College is read for FBS (group 80) and FCS (group 81), each a slate far longer than the default page;
# the two answers are one day's games.
_GROUPS: dict[str, tuple[dict[str, str], ...]] = {
    COLLEGE: ({"groups": "80", "limit": "300"}, {"groups": "81", "limit": "300"}),
    NFL: ({},),
}
# ESPN's scoreboard days are US Eastern calendar days.
ESPN_DAY = ZoneInfo("America/New_York")
# A guide's start and the scoreboard's kickoff are the same game within this much.
WINDOW = timedelta(hours=3)
LIVE_TTL = 60.0
IDLE_TTL = 15 * 60.0
PREGAME_TTL = 30 * 60.0
# A failed request is not asked again for a minute: an outage must not cost a request per refresh.
FAILURE_TTL = 60.0
REQUEST_TIMEOUT_S = 8.0
BUDGET_S = 1.5
# Art is asked for games on now and the rest of the day's slate, so a morning visit finds every tile
# drawn; not for games further off, which may still move or lose their channel.
ART_LEAD = timedelta(hours=16)
UNRANKED = 99
TOP = 25

_FOOTBALL = re.compile(r"\b(?:american football|college football|ncaa football|nfl|football)\b", re.IGNORECASE)
_NOT_GRIDIRON = re.compile(r"\b(?:soccer|australian|gaelic|flag|women'?s football)\b", re.IGNORECASE)
_NFL = re.compile(r"\bnfl\b", re.IGNORECASE)
_COLLEGE = re.compile(r"\b(?:college|ncaa|ncaaf|fbs)\b", re.IGNORECASE)
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def is_football(sport: str | None) -> bool:
    """American football by the listing's sport ("American football", "Football", "College Football")."""
    return bool(sport) and bool(_FOOTBALL.search(sport or "")) and not _NOT_GRIDIRON.search(sport or "")


# ------------------------------------------------------------------ names
_RANK = re.compile(r"^\s*(?:no\.?\s*|#\s*)\d+\s+")
_APOSTROPHE = re.compile(r"['’`]")
_PUNCTUATION = re.compile(r"[^\w\s]|_")
_UNIVERSITY_OF = re.compile(r"\buniversity\s+of\b")
_DROPPED = {"the", "university"}


def normalise_name(name: str) -> str:
    """A team name reduced to what two spellings of it share.

    Case, accents and punctuation go, as do a leading ranking ("No. 12", "#12") and the words
    "university" and "the"; "St." reads as "State" on both sides of a comparison.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = _RANK.sub("", text)
    text = _UNIVERSITY_OF.sub(" ", _APOSTROPHE.sub("", text))
    words = [w for w in _PUNCTUATION.sub(" ", text).split() if w not in _DROPPED]
    return " ".join("state" if w == "st" else w for w in words)


# ------------------------------------------------------------------ parsing
@dataclass(frozen=True)
class Team:
    name: str
    short_name: str
    color: str | None
    alt_color: str | None
    rank: int | None
    record: str | None
    conference_record: str | None
    keys: frozenset[str]

    def api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "short_name": self.short_name,
            "color": self.color,
            "alt_color": self.alt_color,
            "rank": self.rank,
            "record": self.record,
            "conference_record": self.conference_record,
        }


@dataclass(frozen=True)
class Game:
    id: str
    league: str
    start: datetime
    state: str  # pre | in | post
    detail: str | None
    home: Team
    away: Team


def _color(value: Any) -> str | None:
    found = _HEX.match(value) if isinstance(value, str) else None
    return f"#{found.group(1).lower()}" if found else None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _team(competitor: dict[str, Any], conference: str | None) -> Team:
    team = competitor["team"]
    name = _text(team.get("displayName")) or _text(team.get("location"))
    if name is None:
        raise ValueError("competitor without a name")
    location, nickname = _text(team.get("location")), _text(team.get("name"))
    spellings = [
        name,
        _text(team.get("shortDisplayName")),
        location,
        nickname,
        f"{location} {nickname}" if location and nickname else None,
        _text(team.get("abbreviation")),
    ]
    rank = (competitor.get("curatedRank") or {}).get("current")
    records = {r.get("type"): _text(r.get("summary")) for r in competitor.get("records") or [] if isinstance(r, dict)}
    vsconf = records.get("vsconf")
    return Team(
        name=name,
        short_name=_text(team.get("shortDisplayName")) or location or name,
        color=_color(team.get("color")),
        alt_color=_color(team.get("alternateColor")),
        rank=rank if isinstance(rank, int) and not isinstance(rank, bool) and 0 < rank <= TOP else None,
        record=records.get("total"),
        conference_record=f"{vsconf} {conference}" if vsconf and conference else vsconf,
        keys=frozenset(key for s in spellings if s and (key := normalise_name(s))),
    )


def _detail(status: dict[str, Any]) -> str | None:
    """What a card shows of the clock: "Q2 7:14", "Halftime", "End of Q3", "OT 2:01", "Final". Never a score."""
    kind = status.get("type") or {}
    state = kind.get("state")
    if state == "post":
        return "Final"
    if state != "in":
        return None
    name = str(kind.get("name") or "")
    if "HALFTIME" in name or "halftime" in str(kind.get("shortDetail") or "").lower():
        return "Halftime"
    period = status.get("period")
    if not isinstance(period, int) or isinstance(period, bool) or period < 1:
        return _text(kind.get("shortDetail"))
    label = f"Q{period}" if period <= 4 else "OT" if period == 5 else f"{period - 4}OT"
    if name == "STATUS_END_PERIOD":
        return f"End of {label}"
    clock = _text(status.get("displayClock"))
    return f"{label} {clock}" if clock else label


def _when(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_scoreboard(league: str, data: Any) -> list[Game]:
    """The games of a scoreboard answer. A game that does not read cleanly is left out, not guessed."""
    games: list[Game] = []
    events = data.get("events") if isinstance(data, dict) else None
    for event in events if isinstance(events, list) else []:
        try:
            competition = (event.get("competitions") or [])[0]
            groups = competition.get("groups") or {}
            conference = (
                _text(groups.get("shortName"))
                if competition.get("conferenceCompetition") and groups.get("isConference", True)
                else None
            )
            sides = {c.get("homeAway"): c for c in competition.get("competitors") or []}
            status = competition.get("status") or event.get("status") or {}
            games.append(
                Game(
                    id=str(event["id"]),
                    league=league,
                    start=_when(competition.get("date") or event["date"]),
                    state=str((status.get("type") or {}).get("state") or "pre"),
                    detail=_detail(status),
                    home=_team(sides["home"], conference),
                    away=_team(sides["away"], conference),
                )
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            continue
    return games


def _percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 100 else None


def parse_probability(data: Any, state: str) -> dict[str, Any] | None:
    """Win probability (0..100) of a game summary: live during the game, the prediction before it.

    Nothing after the final whistle (a 100/0 split is the result) and nothing made up: a summary
    without a probability is ``None``, never 50/50.
    """
    data = data or {}
    if state == "in":
        series = data.get("winprobability")
        last = series[-1] if isinstance(series, list) and series and isinstance(series[-1], dict) else {}
        share = last.get("homeWinPercentage")
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share <= 1:
            return None
        home = round(float(share) * 100, 1)
        return {"away": round(100 - home, 1), "home": home, "live": True}
    if state != "pre":
        return None
    predictor = data.get("predictor") or {}
    home_p = _percent((predictor.get("homeTeam") or {}).get("gameProjection"))
    away_p = _percent((predictor.get("awayTeam") or {}).get("gameProjection"))
    if home_p is None and away_p is None:
        return None
    home_p = 100 - away_p if home_p is None and away_p is not None else home_p
    away_p = 100 - home_p if away_p is None and home_p is not None else away_p
    total = (home_p or 0) + (away_p or 0)
    if total <= 0:
        return None
    home = round((home_p or 0) * 100 / total, 1)
    return {"away": round(100 - home, 1), "home": home, "live": False}


# ------------------------------------------------------------------ matching
def leagues_for(event: dict[str, Any]) -> tuple[str, ...]:
    """The scoreboards a listing can be on: the one its own words name, else both."""
    text = f"{event.get('title') or ''} {event.get('sport') or ''} {event.get('league') or ''}"
    if _NFL.search(text):
        return (NFL,)
    if _COLLEGE.search(text):
        return (COLLEGE,)
    return (COLLEGE, NFL)


def espn_days(start: datetime) -> set[date]:
    """The scoreboard days a kickoff within the matching window of ``start`` can be listed on."""
    return {(start + offset).astimezone(ESPN_DAY).date() for offset in (-WINDOW, timedelta(0), WINDOW)}


def match_game(event: dict[str, Any], games: Iterable[Game]) -> Game | None:
    """The one game whose two teams are the listing's two sides (either order), kicking off within
    three hours of it. Two candidates are no match."""
    first, second = normalise_name(event.get("home") or ""), normalise_name(event.get("away") or "")
    if not first or not second:
        return None
    start: datetime = event["start"]
    found = {
        (g.league, g.id): g
        for g in games
        if abs(g.start - start) <= WINDOW
        and ((first in g.home.keys and second in g.away.keys) or (first in g.away.keys and second in g.home.keys))
    }
    return next(iter(found.values())) if len(found) == 1 else None


def _guide_team(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "short_name": name,
        "color": None,
        "alt_color": None,
        "rank": None,
        "record": None,
        "conference_record": None,
    }


# ------------------------------------------------------------------ the service
class SportsFacts:
    """Annotates a page of sports events with ``matchup`` facts (and art, when that is configured)."""

    def __init__(
        self,
        client_factory: Callable[..., httpx.AsyncClient],
        *,
        enabled: bool = True,
        art: MatchupArt | None = None,
        budget_s: float = BUDGET_S,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client_factory = client_factory
        self._now = now
        self.enabled = enabled
        self.art = art
        self._budget_s = budget_s
        self._clock = clock
        self._entries: dict[tuple[str, ...], tuple[float, Any]] = {}
        self._inflight: dict[tuple[str, ...], asyncio.Task[None]] = {}

    # ---- cache
    def _cached(self, key: tuple[str, ...]) -> tuple[bool, Any]:
        entry = self._entries.get(key)
        if entry is None or entry[0] <= self._clock():
            return False, None
        return True, entry[1]

    def _ensure(
        self, key: tuple[str, ...], fetch: Callable[[], Awaitable[tuple[Any, float]]]
    ) -> asyncio.Task[None] | None:
        """A running lookup for ``key``, started if nothing fresh is cached; ``None`` when it is."""
        if self._cached(key)[0]:
            return None
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fill(key, fetch))
            self._inflight[key] = task
        return task

    async def _fill(self, key: tuple[str, ...], fetch: Callable[[], Awaitable[tuple[Any, float]]]) -> None:
        try:
            value, ttl = await fetch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # any failure is "no facts" for a while
            log.info("sports facts unavailable", extra={"lookup": key[0], "err": type(exc).__name__})
            value, ttl = None, FAILURE_TTL
        finally:
            self._inflight.pop(key, None)
        self._entries[key] = (self._clock() + ttl, value)
        if len(self._entries) > 512:
            now = self._clock()
            for stale in [k for k, (until, _) in self._entries.items() if until <= now]:
                self._entries.pop(stale, None)

    async def _get(self, url: str, params: dict[str, str]) -> Any:
        async with self._client_factory(timeout=REQUEST_TIMEOUT_S) as client:
            resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def _board(self, league: str, day: date) -> Callable[[], Awaitable[tuple[Any, float]]]:
        async def fetch() -> tuple[Any, float]:
            url, dates = f"{ESPN}/{league}/scoreboard", day.strftime("%Y%m%d")
            answers = await asyncio.gather(
                *(self._get(url, {"dates": dates, **group}) for group in _GROUPS[league]), return_exceptions=True
            )
            read = [answer for answer in answers if not isinstance(answer, BaseException)]
            if not read:
                failure = answers[0]
                raise failure if isinstance(failure, BaseException) else RuntimeError("no scoreboard")
            # One game listed by both groups is one game.
            games = list({g.id: g for data in reversed(read) for g in parse_scoreboard(league, data)}.values())
            # Refreshed every minute while a game is on or about to be: its clock and state change.
            soon = self._now() + timedelta(seconds=IDLE_TTL)
            busy = any(g.state == "in" or (g.state == "pre" and g.start <= soon) for g in games)
            return games, LIVE_TTL if busy else IDLE_TTL

        return fetch

    def _summary(self, game: Game) -> Callable[[], Awaitable[tuple[Any, float]]]:
        async def fetch() -> tuple[Any, float]:
            data = await self._get(f"{ESPN}/{game.league}/summary", {"event": game.id})
            return parse_probability(data, game.state), LIVE_TTL if game.state == "in" else PREGAME_TTL

        return fetch

    async def _wait(self, tasks: list[asyncio.Task[None]], deadline: float) -> None:
        if tasks:
            # Not cancelled at the deadline: a slow lookup finishes for the next refresh.
            await asyncio.wait(tasks, timeout=max(0.0, deadline - asyncio.get_running_loop().time()))

    async def annotate(self, events: list[dict[str, Any]]) -> None:
        """Set ``matchup`` on each event of a page: facts for a dated football game with two sides, else null."""
        for event in events:
            event.setdefault("matchup", None)
        wanted = [
            e
            for e in events
            if e.get("kind", "event") == "event" and is_football(e.get("sport")) and e.get("home") and e.get("away")
        ]
        if not wanted:
            return
        deadline = asyncio.get_running_loop().time() + self._budget_s
        games: dict[int, Game | None] = {}
        probabilities: dict[int, dict[str, Any] | None] = {}
        if self.enabled:
            boards = {(league, day) for e in wanted for league in leagues_for(e) for day in espn_days(e["start"])}
            await self._wait(
                [t for lg, d in boards if (t := self._ensure(("board", lg, d.isoformat()), self._board(lg, d)))],
                deadline,
            )
            for e in wanted:
                listed = [
                    g
                    for lg in leagues_for(e)
                    for d in espn_days(e["start"])
                    for g in (self._cached(("board", lg, d.isoformat()))[1] or [])
                ]
                games[id(e)] = match_game(e, listed)
            # Win probability only where a card shows it: a game on now, and never a replay's.
            live = {
                id(e): g
                for e in wanted
                if (g := games[id(e)]) is not None
                and e.get("status") == "current"
                and not e.get("replay")
                and g.state in ("pre", "in")
            }
            await self._wait(
                [
                    t
                    for g in live.values()
                    if (t := self._ensure(("summary", g.league, g.id, g.state), self._summary(g)))
                ],
                deadline,
            )
            for key, g in live.items():
                probabilities[key] = self._cached(("summary", g.league, g.id, g.state))[1]
        now = self._now()
        # Games on now ask for their art before games still to come.
        for e in sorted(wanted, key=lambda e: e.get("status") != "current"):
            e["matchup"] = self._matchup(e, games.get(id(e)), probabilities.get(id(e)), now)

    def _matchup(
        self, event: dict[str, Any], game: Game | None, probability: dict[str, Any] | None, now: datetime
    ) -> dict[str, Any]:
        replay = bool(event.get("replay"))
        if game is not None:
            away, home = game.away.api(), game.home.api()
            league, detail = game.league, game.detail
        else:
            away, home = _guide_team(event["away"]), _guide_team(event["home"])
            league, detail = "football", None
        art_url = None
        if self.art is not None:
            soon = event.get("status") == "current" or (event["start"] - now) <= ART_LEAD
            art_url = self.art.url_for(away["name"], home["name"], league, generate=soon)
        return {
            "away": away,
            "home": home,
            # A replay is shown without the clock or the odds: either gives the game away.
            "status_detail": None if replay else detail,
            "win_probability": None if replay else probability,
            "art_url": art_url,
        }

    async def shutdown(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)
