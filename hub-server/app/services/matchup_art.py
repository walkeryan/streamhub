"""Generated matchup art: the two teams' helmets nose to nose, one image per pairing.

Made with xAI's image API, and only when ``XAI_API_KEY`` is set. One background worker sends one
request at a time from a de-duplicated queue; a request for an image that does not exist yet
enqueues it and answers no URL, and the Sports screen's next refresh picks the image up.

Cost is bounded twice: a daily cap of attempts (``MATCHUP_ART_DAILY_LIMIT``, per UTC day, kept on
disk so a restart does not reset it), and a failing pairing is tried three times at most and then
left alone for a day. Images are files under ``{config_dir}/art/matchups``, never in the database
and never in git, and reach an ``<img>`` through signed, expiring URLs (``art_payload``) the way
the hub's other artwork does.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.infra.logging import secret_registry
from app.services.context import absolute
from app.services.errors import Forbidden
from app.services.image_grants import IMAGE_TTL, UNBOUND_TTL
from app.services.sports_facts import normalise_name

log = logging.getLogger(__name__)

XAI_IMAGES = "https://api.x.ai/v1/images/generations"
DEFAULT_MODEL = "grok-imagine-image-2.0"
DEFAULT_DAILY_LIMIT = 100
MAX_ATTEMPTS = 3  # the first try and two retries
RETRY_AFTER_S = 120.0
GIVE_UP_S = 24 * 3600.0
REQUEST_TIMEOUT_S = 180.0
KEY = re.compile(r"^[0-9a-f]{32}$")
STATE_FILE = "state.json"

PROMPT = (
    "Photorealistic broadcast background: the {away} American football helmet on the left versus the {home} "
    "helmet on the right, each team's real helmet design and logo clearly visible, face to face in mid-air above "
    "a football field at night, flying turf, debris and sparks between them, stadium lights bokeh, cinematic "
    "lighting, 16:9, no other text, no watermark."
)


def matchup_key(away: str, home: str, league: str) -> str:
    """One image per (away, home, league): the same pairing on another channel, or in a replay,
    reuses it; swapped sides are another image (the helmets face the other way)."""
    text = f"{normalise_name(away)}|{normalise_name(home)}|{league.casefold()}"
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def matchup_prompt(away: str, home: str) -> str:
    return PROMPT.format(away=away.strip(), home=home.strip())


def image_type(body: bytes) -> str | None:
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


class ArtError(Exception):
    pass


def art_payload(key: str, policy: Any) -> dict[str, Any] | None:
    """What a matchup image URL is signed over, by the rules of ``image_grants.image_payload``.

    Bound to the caller's session whenever the content policy applies to them, and to the home
    network for a caller without ``remote.access``. A bound URL keeps one value for five minutes
    so the screen's refreshes do not reload the image; it lasts five to ten.
    """
    from app.services.content_policy import active_principal

    payload: dict[str, Any] = {"m": key}
    principal = active_principal.get()
    if principal is not None and "remote.access" not in principal.permissions:
        payload["home"] = True
    if policy is None or not policy.applies_to(principal):
        payload["x"] = (int(time.time()) // UNBOUND_TTL + 2) * UNBOUND_TTL
        return payload
    if principal is None or not principal.session_id:
        return None
    payload.update(
        profile=principal.profile_id,
        version=principal.token_version,
        session=principal.session_id,
        expires=(int(time.time()) // IMAGE_TTL + 2) * IMAGE_TTL,
    )
    return payload


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


async def validate_art_grant(payload: dict[str, Any], policy: Any, auth: Any) -> str:
    """The image key of a signed matchup art URL that is still good for its viewer."""
    key = payload.get("m")
    if not isinstance(key, str) or not KEY.match(key):
        raise Forbidden("Artwork authorization expired or missing", code="image_expired")
    now = time.time()
    if not policy.enabled and "profile" not in payload:
        until = payload.get("x")
        if (
            isinstance(until, bool)
            or not isinstance(until, (int, float))
            or not now < until <= now + 2 * UNBOUND_TTL + 2
        ):
            raise Forbidden("Artwork authorization expired or missing", code="image_expired")
        return key
    profile, version, session = payload.get("profile"), payload.get("version"), payload.get("session")
    expires = payload.get("expires")
    if (
        not isinstance(profile, str)
        or not profile
        or not isinstance(session, str)
        or not session
        or not isinstance(version, int)
        or isinstance(version, bool)
        or isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or not now < expires <= now + 2 * IMAGE_TTL + 2
    ):
        raise Forbidden("Artwork authorization expired or missing", code="content_restricted")
    await auth.session_valid(profile, version, session)
    return key


class MatchupArt:
    def __init__(
        self,
        root: Path,
        *,
        api_key: str | None,
        model: str = DEFAULT_MODEL,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
        client_factory: Callable[..., httpx.AsyncClient],
        sign: Callable[[Any], str],
        policy: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = root
        self._api_key = api_key or None
        secret_registry.add(self._api_key)
        self.model = model or DEFAULT_MODEL
        self.daily_limit = max(0, daily_limit)
        self._client_factory = client_factory
        self._sign = sign
        self.policy = policy
        self._clock = clock
        self._queue: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        self._queued: set[str] = set()
        self._present: set[str] = set()
        self._worker: asyncio.Task[None] | None = None
        self._state = self._load()

    @property
    def enabled(self) -> bool:
        return self._api_key is not None and self.daily_limit > 0

    # ---- files
    def path(self, key: str) -> Path:
        return self.root / f"{key}.jpg"

    def has(self, key: str) -> bool:
        if key in self._present:
            return True
        if self.path(key).is_file():
            self._present.add(key)
            return True
        return False

    async def read(self, key: str) -> tuple[bytes, str] | None:
        if not KEY.match(key):
            return None
        try:
            body = await asyncio.to_thread(self.path(key).read_bytes)
        except OSError:
            return None
        kind = image_type(body)
        return (body, kind) if kind else None

    def _write(self, key: str, body: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(key)
        partial = target.with_name(f"{target.name}.{secrets.token_hex(4)}.part")
        partial.write_bytes(body)
        os.replace(partial, target)
        self._present.add(key)

    # ---- the daily cap and failures, on disk
    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).date().isoformat()

    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads((self.root / STATE_FILE).read_text())
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        failures = state.get("failures")
        count = state.get("count")
        return {
            "day": state.get("day") if isinstance(state.get("day"), str) else self._today(),
            "count": count if isinstance(count, int) and not isinstance(count, bool) else 0,
            "failures": failures if isinstance(failures, dict) else {},
        }

    def _roll(self) -> None:
        today = self._today()
        if self._state["day"] != today:
            self._state["day"], self._state["count"] = today, 0

    def _save(self) -> None:
        with contextlib.suppress(OSError):
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / STATE_FILE
            partial = path.with_name(f"{STATE_FILE}.{secrets.token_hex(4)}.part")
            partial.write_text(json.dumps(self._state, separators=(",", ":")))
            os.replace(partial, path)

    @property
    def used_today(self) -> int:
        self._roll()
        return int(self._state["count"])

    def _resting(self, key: str) -> bool:
        failure = self._state["failures"].get(key)
        return isinstance(failure, dict) and _number(failure.get("until")) and failure["until"] > self._clock()

    # ---- asking
    def request(self, key: str, away: str, home: str) -> bool:
        """Queue the image for ``key``: not when it exists, is queued, rests after failures, or the day is spent."""
        if not self.enabled or key in self._queued or self.has(key) or self._resting(key):
            return False
        if self.used_today + len(self._queued) >= self.daily_limit:
            return False
        self._queued.add(key)
        self._queue.put_nowait((key, away, home))
        return True

    def url(self, key: str) -> str | None:
        if not self.has(key):
            return None
        payload = art_payload(key, self.policy)
        return absolute(f"/api/v1/sports/art/{self._sign(payload)}") if payload is not None else None

    def url_for(self, away: str, home: str, league: str, *, generate: bool = True) -> str | None:
        """The signed URL of this pairing's image; without one, the image is asked for (when ``generate``)."""
        key = matchup_key(away, home, league)
        found = self.url(key)
        if found is None and generate and not self.has(key):
            self.request(key, away, home)
        return found

    @property
    def pending(self) -> int:
        return len(self._queued)

    # ---- the worker
    def start(self) -> None:
        if self.enabled and self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def shutdown(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            await self.process_next()

    async def process_next(self) -> bool:
        """Make the next queued image (one request). True when an image was written."""
        key, away, home = await self._queue.get()
        try:
            return await self._generate(key, away, home)
        finally:
            self._queued.discard(key)

    async def _generate(self, key: str, away: str, home: str) -> bool:
        if self.has(key) or self._resting(key):
            return False
        if self.used_today >= self.daily_limit:
            return False
        # Counted before the request, and on disk: a crash mid-request still spent it.
        self._state["count"] += 1
        await asyncio.to_thread(self._save)
        try:
            body = await self._call(matchup_prompt(away, home))
            await asyncio.to_thread(self._write, key, body)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed(key, exc)
            await asyncio.to_thread(self._save)
            return False
        self._state["failures"].pop(key, None)
        await asyncio.to_thread(self._save)
        log.info("matchup art made", extra={"art_key": key, "used_today": self._state["count"]})
        return True

    def _failed(self, key: str, exc: Exception) -> None:
        previous = self._state["failures"].get(key)
        tries = (previous.get("n", 0) if isinstance(previous, dict) else 0) + 1
        resting = tries >= MAX_ATTEMPTS
        self._state["failures"][key] = {
            "n": 0 if resting else tries,
            "until": self._clock() + (GIVE_UP_S if resting else RETRY_AFTER_S),
        }
        # The reason without the request: its headers carry the key.
        log.warning(
            "matchup art failed",
            extra={
                "art_key": key,
                "err": str(exc) if isinstance(exc, ArtError) else type(exc).__name__,
                "resting": resting,
            },
        )

    async def _call(self, prompt: str) -> bytes:
        body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "response_format": "b64_json",
            "aspect_ratio": "16:9",
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with self._client_factory(timeout=REQUEST_TIMEOUT_S) as client:
            resp = await client.post(XAI_IMAGES, json=body, headers=headers)
            if resp.status_code == 400 and "aspect" in resp.text.lower():
                # A model without the aspect parameter: ask again without it (a refused request costs nothing).
                body.pop("aspect_ratio")
                resp = await client.post(XAI_IMAGES, json=body, headers=headers)
        if resp.status_code >= 400:
            raise ArtError(f"image API answered {resp.status_code}")
        try:
            encoded = resp.json()["data"][0]["b64_json"]
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ArtError("image API answer unreadable") from exc
        if image_type(image) is None:
            raise ArtError("image API answer is not an image")
        return image
