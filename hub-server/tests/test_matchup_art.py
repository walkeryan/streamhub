"""Matchup art: the key, the prompt, the queue, the daily cap on disk, the retry limit, the signed URL.

No request leaves the process: the image API is an ``httpx.MockTransport`` and the image is a
placeholder made here (a few bytes with a JPEG header), never a real logo or photo.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.infra.crypto import Crypto
from app.infra.logging import RedactionFilter, TextFormatter
from app.services.auth import Principal
from app.services.content_policy import active_principal
from app.services.errors import Forbidden
from app.services.matchup_art import (
    GIVE_UP_S,
    RETRY_AFTER_S,
    MatchupArt,
    art_payload,
    image_type,
    matchup_key,
    matchup_prompt,
    validate_art_grant,
)

API_KEY = "xai-fixture-key-0123456789"
# Not a picture of anything: a JPEG start-of-image marker and filler.
PLACEHOLDER = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"


class ImageApi:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.body: Any = {"data": [{"b64_json": base64.b64encode(PLACEHOLDER).decode()}]}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status >= 400:
            return httpx.Response(self.status, json={"error": "fixture failure"})
        return httpx.Response(200, json=self.body)

    def factory(self, **kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


class Clock:
    def __init__(self) -> None:
        self.now = 1_794_700_800.0  # 2026-11-15 00:00 UTC

    def __call__(self) -> float:
        return self.now


def make(tmp_path: Path, api: ImageApi, clock: Clock | None = None, **kw: Any) -> MatchupArt:
    crypto = Crypto("fixture-art-signing-key-0123456789")
    options: dict[str, Any] = {"api_key": API_KEY, "daily_limit": 3, **kw}
    return MatchupArt(
        tmp_path / "art" / "matchups",
        client_factory=api.factory,
        sign=crypto.sign_json,
        clock=clock or Clock(),
        **options,
    )


def test_key_and_prompt() -> None:
    key = matchup_key("Lakeview Kestrels", "Brookfield Badgers", "college-football")
    assert len(key) == 32 and int(key, 16) >= 0
    # Spelling does not matter; the side and the league do.
    assert key == matchup_key("lakeview kestrels", "The Brookfield Badgers", "College-Football")
    assert key != matchup_key("Brookfield Badgers", "Lakeview Kestrels", "college-football")
    assert key != matchup_key("Lakeview Kestrels", "Brookfield Badgers", "nfl")
    prompt = matchup_prompt("Lakeview Kestrels", "Brookfield Badgers")
    assert "Lakeview Kestrels American football helmet on the left" in prompt
    assert "Brookfield Badgers" in prompt and "no other text" in prompt and "no watermark" in prompt
    assert image_type(PLACEHOLDER) == "image/jpeg" and image_type(b"<svg/>") is None


async def test_queue_generates_once_and_dedupes(tmp_path: Path) -> None:
    api = ImageApi()
    art = make(tmp_path, api)
    key = matchup_key("Lakeview", "Brookfield", "football")
    assert art.url_for("Lakeview", "Brookfield", "football") is None
    assert art.url_for("Lakeview", "Brookfield", "football") is None
    assert art.pending == 1
    assert await art.process_next() is True
    assert art.path(key).read_bytes() == PLACEHOLDER
    assert len(api.requests) == 1
    sent = json.loads(api.requests[0].content)
    assert sent["model"] == "grok-imagine-image-2.0" and sent["aspect_ratio"] == "16:9"
    assert sent["response_format"] == "b64_json" and sent["n"] == 1
    assert "Lakeview" in sent["prompt"] and "Brookfield" in sent["prompt"]
    assert api.requests[0].headers["authorization"] == f"Bearer {API_KEY}"
    url = art.url_for("Lakeview", "Brookfield", "football")
    assert url is not None and "/api/v1/sports/art/" in url
    # Made once: asking again queues nothing.
    assert art.request(key, "Lakeview", "Brookfield") is False and art.pending == 0
    assert await art.read(key) == (PLACEHOLDER, "image/jpeg")


async def test_no_key_no_queue(tmp_path: Path) -> None:
    api = ImageApi()
    art = make(tmp_path, api, api_key=None)
    assert not art.enabled
    assert art.url_for("Lakeview", "Brookfield", "football") is None
    assert art.pending == 0 and api.requests == []
    art.start()
    await art.shutdown()


async def test_daily_cap_persists_across_restart(tmp_path: Path) -> None:
    api, clock = ImageApi(), Clock()
    art = make(tmp_path, api, clock)
    for n in range(5):
        art.url_for(f"Team {n}", "Brookfield", "football")
    # Past the cap nothing is queued.
    assert art.pending == 3
    for _ in range(3):
        await art.process_next()
    assert art.used_today == 3
    restarted = make(tmp_path, api, clock)
    assert restarted.used_today == 3
    assert restarted.request(matchup_key("Team 4", "Brookfield", "football"), "Team 4", "Brookfield") is False
    # A new UTC day starts a new count.
    clock.now += 86400
    assert restarted.request(matchup_key("Team 4", "Brookfield", "football"), "Team 4", "Brookfield") is True
    assert await restarted.process_next() is True and restarted.used_today == 1


async def test_failures_retry_twice_then_rest_a_day(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    api, clock = ImageApi(), Clock()
    api.status = 500
    art = make(tmp_path, api, clock, daily_limit=50)
    key = matchup_key("Lakeview", "Brookfield", "football")
    caplog.set_level(logging.INFO)
    for attempt in range(3):
        assert art.request(key, "Lakeview", "Brookfield") is True, attempt
        assert await art.process_next() is False
        # Not asked again at once.
        assert art.request(key, "Lakeview", "Brookfield") is False
        clock.now += RETRY_AFTER_S + 1
    # Three failures: left alone for a day, also after a restart.
    assert len(api.requests) == 3
    restarted = make(tmp_path, api, clock, daily_limit=50)
    assert restarted.request(key, "Lakeview", "Brookfield") is False
    clock.now += GIVE_UP_S
    api.status = 200
    assert restarted.request(key, "Lakeview", "Brookfield") is True
    assert await restarted.process_next() is True
    # The key never reaches a log line, even through the formatter.
    handler_filter, formatter = RedactionFilter(), TextFormatter()
    lines = []
    for record in caplog.records:
        handler_filter.filter(record)
        lines.append(formatter.format(record))
    assert any("matchup art failed" in line for line in lines)
    assert all(API_KEY not in line for line in lines)
    assert API_KEY not in caplog.text


async def test_unreadable_answers_are_failures(tmp_path: Path) -> None:
    api = ImageApi()
    api.body = {"data": [{"b64_json": base64.b64encode(b"<svg/>").decode()}]}
    art = make(tmp_path, api)
    key = matchup_key("Lakeview", "Brookfield", "football")
    art.request(key, "Lakeview", "Brookfield")
    assert await art.process_next() is False and not art.has(key)
    assert not list(art.root.glob("*.jpg"))


async def test_worker_runs_in_background(tmp_path: Path) -> None:
    import asyncio

    api = ImageApi()
    art = make(tmp_path, api)
    art.start()
    try:
        art.url_for("Lakeview", "Brookfield", "football")
        for _ in range(50):
            if art.has(matchup_key("Lakeview", "Brookfield", "football")):
                break
            await asyncio.sleep(0.01)
        assert art.has(matchup_key("Lakeview", "Brookfield", "football"))
    finally:
        await art.shutdown()


# ------------------------------------------------------------------ the signed URL
KEY = "0123456789abcdef0123456789abcdef"


def policy(*, enabled: bool = False, applies: bool = False) -> Any:
    return SimpleNamespace(enabled=enabled, applies_to=lambda principal: applies)


async def test_unbound_grant_for_an_unrestricted_viewer() -> None:
    token = active_principal.set(
        Principal("adult", True, True, 1, "adult-session", permissions=frozenset({"remote.access"}))
    )
    try:
        payload = art_payload(KEY, policy())
    finally:
        active_principal.reset(token)
    assert payload is not None and "profile" not in payload and "home" not in payload
    assert await validate_art_grant(payload, policy(), SimpleNamespace(session_valid=AsyncMock())) == KEY
    with pytest.raises(Forbidden):
        await validate_art_grant({**payload, "x": time.time() - 1}, policy(), None)
    with pytest.raises(Forbidden):
        await validate_art_grant({**payload, "m": "../secret.key"}, policy(), None)
    # Under the legacy policy flag an unbound grant is no grant.
    with pytest.raises(Forbidden):
        await validate_art_grant(payload, policy(enabled=True), None)


async def test_bound_grant_checks_the_session() -> None:
    token = active_principal.set(Principal("kid", False, True, 4, "kid-session"))
    try:
        payload = art_payload(KEY, policy(applies=True))
    finally:
        active_principal.reset(token)
    assert payload is not None
    # The kid has no remote access: home network only, and their own session.
    assert payload["home"] is True and payload["profile"] == "kid" and payload["session"] == "kid-session"
    assert time.time() + 300 <= payload["expires"] <= time.time() + 600
    auth = SimpleNamespace(session_valid=AsyncMock())
    assert await validate_art_grant(payload, policy(applies=True), auth) == KEY
    auth.session_valid.assert_awaited_once_with("kid", 4, "kid-session")
    for change in ({"session": None}, {"version": True}, {"expires": time.time() - 1}, {"expires": time.time() + 4000}):
        with pytest.raises(Forbidden):
            await validate_art_grant({**payload, **change}, policy(applies=True), auth)
    # No session to bind to: no URL.
    assert art_payload(KEY, policy(applies=True)) is None
