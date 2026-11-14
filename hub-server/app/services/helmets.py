"""What each team's helmet looks like, in words, for the matchup art prompt.

The image model draws the wrong helmet from a team's name alone (an alternate, an old logo), and
paints the shell in the team's primary colour when given colours, which is wrong wherever the
helmet is another colour. A short, correct description per team fixes both. The descriptions are
curated by hand (``app/resources/helmets.json``): a language model asked for them makes them up.

``CONFIG_DIR/art/helmets.json``, in the same shape, is merged over the curated file and read
again whenever it changes, so a team can be corrected without a release. An empty string there
takes a team's description away (the prompt's generic phrase is used instead).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.sports_facts import normalise_name

log = logging.getLogger(__name__)

RESOURCE = Path(__file__).resolve().parents[1] / "resources" / "helmets.json"
MAX_LENGTH = 400


def _teams(data: Any) -> dict[str, str]:
    """``{normalised name: description}`` of one file; ``""`` is kept (it removes a team)."""
    teams = data.get("teams") if isinstance(data, dict) else None
    found: dict[str, str] = {}
    for name, text in (teams or {}).items() if isinstance(teams, dict) else ():
        key = normalise_name(name) if isinstance(name, str) else ""
        if key and isinstance(text, str):
            # One line, plain words: it goes into a prompt.
            found[key] = " ".join(text.split())[:MAX_LENGTH]
    return found


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Helmets:
    def __init__(self, override: Path | None = None, resource: Path = RESOURCE) -> None:
        self._override = override
        try:
            self._curated = _teams(_read(resource))
        except (OSError, ValueError):
            log.warning("helmet descriptions unreadable", extra={"path": str(resource)})
            self._curated = {}
        self._seen: float | None = None
        self._merged = dict(self._curated)

    def _refresh(self) -> None:
        if self._override is None:
            return
        try:
            stamp = self._override.stat().st_mtime
        except OSError:
            stamp = None
        if stamp == self._seen:
            return
        self._seen = stamp
        overrides: dict[str, str] = {}
        if stamp is not None:
            try:
                overrides = _teams(_read(self._override))
            except (OSError, ValueError):
                # A broken override is ignored, not half-read: the curated descriptions stand.
                log.warning("helmet overrides unreadable", extra={"path": str(self._override)})
        self._merged = {**self._curated, **overrides}

    def describe(self, team: str) -> str | None:
        """The helmet of ``team`` (a scoreboard display name), or ``None`` when it is not described."""
        self._refresh()
        return self._merged.get(normalise_name(team)) or None

    def count(self) -> int:
        self._refresh()
        return sum(1 for text in self._merged.values() if text)
