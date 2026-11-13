"""Unified search: channels and guide broadcasts beside the title search, and how each is ranked.

``CatalogService.search`` owns the title half (FTS candidates, canonical identity, metadata
discovery). This module holds the rules the three sections share and the two live sections.
Every read goes through ``ChannelService._channel_query`` and ORM entities, so the watch
scope and the content policy narrow it exactly as they narrow the channel list and the guide.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_

from app.infra.models import Channel, ChannelGroup, Programme
from app.services import ids
from app.services.availability import ATTRIBUTION
from app.services.channels import ChannelService, slot_named, slot_programme
from app.services.event_slots import DEFAULT_SLOT_CONFIG, display_name, is_placeholder, parse_event_slot
from app.services.search_match import token_match_rank
from app.services.sports import sport_facts

# Shorter than this is not a query: one letter matches most of a catalog and says nothing.
MIN_QUERY_LENGTH = 2
TITLE_CAP = 40
CHANNEL_CAP = 12
PROGRAMME_CAP = 24
# What "Show more" raises a section's cap to.
EXPANDED_CAP = 100
# A broadcast is a result while it is on, or starts within this long.
PROGRAMME_HORIZON = timedelta(hours=48)
# A guide row longer than this is a label hung on a channel for the day (a game feed's "on network X"),
# not a broadcast someone can tune in to at its start.
MAX_BROADCAST = timedelta(hours=8)
# Guide rows read for one query before grouping; a query that matches more says so with ``more``.
PROGRAMME_SCAN = 2000
SLOT_SCAN = 200
SECTIONS = ("programmes", "channels", "titles")
# A channel found by its number or its whole name is what was asked for: its section leads.
CHANNEL_EXACT_RANK = 1

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_ARTICLE = re.compile(r"^(?:the|a|an)\s+")
_QUERY_YEAR = re.compile(r"^(?P<text>.*[^\s(\[\-])[\s(\[\-]+(?P<year>(?:19|20)\d{2})[)\]]?\s*$")
_NON_ASCII = "*[^ -~]*"


@dataclass(frozen=True)
class _Carrier:
    """The channel columns a broadcast entry shows; a guide read loads no more than these."""

    id: int
    source_id: str
    ext_id: str
    name: str
    number: int
    logo: str | None


def fold(text: str) -> str:
    """A name as it is compared: no diacritics, case or punctuation, "&" as "and", no leading article."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return _ARTICLE.sub("", _NON_ALNUM.sub(" ", plain.replace("&", " and ")).strip())


def split_year(q: str) -> tuple[str, int | None]:
    """``"the mill 2023"`` is the title and a year; a query that is only a number is a title."""
    found = _QUERY_YEAR.match(q.strip())
    return (found.group("text"), int(found.group("year"))) if found else (q.strip(), None)


def title_rank(norm: str, tokens: list[str], title_norm: str) -> int | None:
    """Exact title, title prefix, word prefix, contains; then every word in any order, then one typo."""
    if title_norm == norm:
        return 0
    if title_norm.startswith(norm):
        return 1
    if f" {norm}" in title_norm:
        return 2
    if norm in title_norm:
        return 3
    loose = token_match_rank(tokens, title_norm) if tokens else None
    return None if loose is None else 4 + loose


def broadcast_matches(folded: str, words: list[str], title: str) -> bool:
    """The phrase at the start of a word, or every query word starting one of the title's."""
    if f" {folded}" in f" {title}":
        return True
    have = title.split()
    return all(any(word.startswith(part) for word in have) for part in words)


def empty_sections() -> dict[str, dict[str, Any]]:
    return {name: {"count": 0, "more": False} for name in SECTIONS}


def empty_search() -> dict[str, Any]:
    """What a query too short to look up returns: the whole shape, with nothing in it."""
    return {
        "results": [],
        "metadata_available": False,
        "discovery_note": "",
        "play_here": [],
        "open_in_app": [],
        "channels": [],
        "programmes": [],
        "sections": empty_sections(),
        "order": list(SECTIONS),
        "attribution": ATTRIBUTION,
    }


def section_order(channel_rank: int | None) -> list[str]:
    """Broadcasts, channels, titles; an exactly named or numbered channel puts channels first."""
    if channel_rank is not None and channel_rank <= CHANNEL_EXACT_RANK:
        return ["channels", "programmes", "titles"]
    return list(SECTIONS)


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _contains(column: Any, text: str) -> Any:
    return column.like("%" + _escape_like(text) + "%", escape="\\")


async def search_channels(
    channels: ChannelService,
    profile_id: str,
    q: str,
    *,
    language: str | None = None,
    source_id: str | None = None,
    cap: int = CHANNEL_CAP,
) -> tuple[list[dict[str, Any]], int, int | None]:
    """Channels by number or name: ``(rows as the channel list shows them, total, best rank)``.

    The stored normalised name has no quality marks or country tag, so "korvo" is the whole
    name of "Korvo HD"; the name as stored also matches, which is how a slot's network is found.
    """
    raw = " ".join(q.split())
    norm = ids.normalise_title(raw)
    named: list[Any] = [_contains(Channel.name, raw)]
    exact: list[Any] = [func.lower(Channel.name) == raw.lower()]
    ranks: list[tuple[Any, int]] = []
    # A channel number, not any run of digits: nothing a guide numbers its channels with is longer.
    if raw.isdecimal() and len(raw) <= 6:
        named.append(Channel.number == int(raw))
        ranks.append((Channel.number == int(raw), 0))
    if norm:
        named.append(_contains(Channel.norm_name, norm))
        exact.append(Channel.norm_name == norm)
    ranks.append((or_(*exact), CHANNEL_EXACT_RANK))
    if norm:
        ranks.append((Channel.norm_name.like(_escape_like(norm) + "%", escape="\\"), 2))
        ranks.append((Channel.norm_name.like("% " + _escape_like(norm) + "%", escape="\\"), 3))
    rank = case(*ranks, else_=4)
    query, _ = await channels._channel_query(profile_id, language=language, source_id=source_id)
    query = (
        query.where(or_(*named))
        .add_columns(rank.label("rank"), func.count().over().label("total"))
        .order_by(rank, Channel.number, ChannelGroup.sort, Channel.id)
        .limit(cap)
    )
    async with channels._db.session() as s:
        rows = (await s.execute(query)).all()
    if not rows:
        return [], 0, None
    return await channels.to_api([row[0] for row in rows], profile_id), int(rows[0].total), int(rows[0].rank)


async def search_programmes(
    channels: ChannelService,
    profile_id: str,
    q: str,
    *,
    language: str | None = None,
    source_id: str | None = None,
    cap: int = PROGRAMME_CAP,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Guide broadcasts whose title matches, on now or starting soon: ``(entries, total, more)``.

    One entry per (title, start); the channels carrying it are its feeds, lowest number first.
    A programme that repeats its channel's name is filler and an event slot's guide rows repeat
    the slot, so neither is a result: a slot's game comes from its name instead.
    """
    now = now or datetime.now(UTC)
    until = now + PROGRAMME_HORIZON
    folded = fold(q)
    words = folded.split()[:8]
    # SQL narrows on the words as typed; the folded comparison below decides. "and" may be "&".
    typed = [word for word in words if word != "and"]
    if not typed:
        return [], 0, False
    eligible, _ = await channels._channel_query(profile_id, language=language, source_id=source_id)
    plain = and_(*(_contains(Programme.title, word) for word in typed))
    # Only a title with letters outside ASCII needs folding to be compared; few have any.
    key = func.canonical_title_key(Programme.title)
    accented = and_(Programme.title.op("GLOB")(_NON_ASCII), *(_contains(key, word) for word in typed))
    # The source id is compared as an expression so that a channel is looked up by its guide id.
    # Left as a bare column, SQLite reaches for the (source_id, ext_id) index and walks every channel
    # of the source for each programme: seconds, for a query that matches a few thousand rows.
    carried = and_(Programme.feed == Channel.source_id + "", Programme.epg_channel == Channel.epg_id)
    listed = (
        eligible.join(Programme, carried)
        .where(
            Programme.stop > now,
            Programme.start < until,
            or_(plain, accented),
            func.lower(func.trim(Programme.title)) != func.lower(func.trim(Channel.name)),
            func.julianday(Programme.stop) - func.julianday(Programme.start) <= MAX_BROADCAST / timedelta(days=1),
        )
        .with_only_columns(
            Programme.id,
            Programme.title,
            Programme.category,
            Programme.start,
            Programme.stop,
            Channel.id,
            Channel.source_id,
            Channel.ext_id,
            Channel.name,
            Channel.number,
            Channel.logo,
        )
        .order_by(case((Programme.start <= now, 0), else_=1), Programme.start, Programme.title, Channel.number)
        .limit(PROGRAMME_SCAN)
    )
    slots = eligible.where(slot_named(Channel.name), *(_contains(Channel.name, word) for word in typed)).limit(
        SLOT_SCAN
    )
    async with channels._db.session() as s:
        aired = (await s.execute(listed)).all()
        slot_channels = list((await s.execute(slots)).scalars())
    found: list[tuple[dict[str, Any], _Carrier]] = []
    for pid, title, category, start, stop, *carrier in aired:
        ch = _Carrier(*carrier)
        if is_placeholder(title, ch.name) or parse_event_slot(ch.name) is not None:
            continue
        if broadcast_matches(folded, words, fold(title)):
            is_sport, teams = sport_facts(title, category)
            entry = {
                "id": ids.prog_id(pid),
                "start": start,
                "stop": stop,
                "title": title,
                "description": None,
                "category": category,
                "icon": None,
                "is_sport": is_sport,
                "teams": list(teams),
            }
            found.append((entry, ch))
    configs = await channels.slot_configs(slot_channels)
    for ch in slot_channels:
        slot = parse_event_slot(ch.name)
        config = configs.get(ch.source_id, DEFAULT_SLOT_CONFIG)
        if slot is None or slot.state(now, config) not in ("live", "upcoming"):
            continue
        game = slot_programme(ch, slot, config)
        if game is not None and game["start"] < until and broadcast_matches(folded, words, fold(game["title"])):
            found.append((game, _Carrier(ch.id, ch.source_id, ch.ext_id, ch.name, ch.number, ch.logo)))
    return await _grouped(channels, found, cap, now, truncated=len(aired) >= PROGRAMME_SCAN)


async def _grouped(
    channels: ChannelService,
    found: Sequence[tuple[dict[str, Any], _Carrier]],
    cap: int,
    now: datetime,
    *,
    truncated: bool,
) -> tuple[list[dict[str, Any]], int, bool]:
    groups: dict[tuple[str, datetime], list[tuple[dict[str, Any], _Carrier]]] = {}
    for entry, ch in found:
        groups.setdefault((fold(entry["title"]), entry["start"]), []).append((entry, ch))
    ordered = sorted(groups.items(), key=lambda item: (item[0][1] > now, item[0][1], item[0][0]))
    shown = ordered[:cap]
    sources = await channels._sources.rows_by_id() if channels.image_fn and shown else {}
    image = channels.image_fn(sources) if channels.image_fn else None

    def feed(entry: dict[str, Any], ch: _Carrier) -> dict[str, Any]:
        cid = ids.channel_id(ch.source_id, ch.ext_id)
        return {
            "programme_id": entry["id"],
            "channel_id": cid,
            "channel_number": ch.number,
            "channel_name": display_name(ch.name),
            "channel_logo": image(sources.get(ch.source_id), ch.logo, cid) if image else ch.logo,
        }

    out = []
    for (_, start), members in shown:
        # A guide that lists the same programme twice on a channel still names the channel once.
        carried = {ch.id: (entry, ch) for entry, ch in reversed(members)}
        members = sorted(carried.values(), key=lambda member: (member[1].number, member[1].id))
        feeds = [feed(entry, ch) for entry, ch in members]
        first = members[0][0]
        lead = {key: value for key, value in feeds[0].items() if key != "programme_id"}
        out.append(
            {
                **first,
                **lead,
                "catchup_available": False,
                "on_now": start <= now < first["stop"],
                "feeds": feeds,
            }
        )
    # A scan cut short with nothing found in it promises no more: there is nothing to show more of.
    return out, len(ordered), len(ordered) > cap or (truncated and bool(ordered))
