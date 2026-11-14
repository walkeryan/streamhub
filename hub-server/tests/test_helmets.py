"""Helmet descriptions for the matchup art prompt: lookup, the owner's overrides, prompt and key.

Fictional teams in a fixture resource; the shipped file is only checked for its shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from app.infra.crypto import Crypto
from app.services.helmets import RESOURCE, Helmets
from app.services.matchup_art import MatchupArt, helmet_phrase, matchup_key, matchup_prompt

KESTRELS = "glossy navy helmet with a white stripe and the white talon logo on the side"
BADGERS = "matte maroon helmet with the gold striped badger-head logo on the side"


def resource(tmp_path: Path) -> Path:
    path = tmp_path / "helmets.json"
    path.write_text(
        json.dumps({"_about": "fixture", "teams": {"Lakeview Kestrels": KESTRELS, "Brookfield Badgers": BADGERS}})
    )
    return path


def write_override(path: Path, teams: dict[str, Any], *, bump: float = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"teams": teams}))
    if bump:
        stamp = path.stat().st_mtime + bump
        os.utime(path, (stamp, stamp))


def test_lookup_by_display_name_however_spelled(tmp_path: Path) -> None:
    helmets = Helmets(resource=resource(tmp_path))
    assert helmets.describe("Lakeview Kestrels") == KESTRELS
    assert helmets.describe("the lakeview kestrels") == KESTRELS
    assert helmets.describe("Riverside Otters") is None
    assert helmets.count() == 2


def test_override_is_merged_over_the_resource_and_reread_when_changed(tmp_path: Path) -> None:
    override = tmp_path / "config" / "art" / "helmets.json"
    helmets = Helmets(override, resource=resource(tmp_path))
    # No override file yet: the curated descriptions alone.
    assert helmets.describe("Brookfield Badgers") == BADGERS
    write_override(
        override,
        {"Brookfield Badgers": "glossy silver helmet with a maroon stripe", "Riverside Otters": "glossy teal helmet"},
    )
    assert helmets.describe("Brookfield Badgers") == "glossy silver helmet with a maroon stripe"
    assert helmets.describe("Riverside Otters") == "glossy teal helmet"
    assert helmets.describe("Lakeview Kestrels") == KESTRELS
    # An empty string takes a description away; the change is picked up without a restart.
    write_override(override, {"Lakeview Kestrels": ""}, bump=5)
    assert helmets.describe("Lakeview Kestrels") is None
    assert helmets.describe("Riverside Otters") is None and helmets.describe("Brookfield Badgers") == BADGERS
    # A broken override is ignored as a whole.
    override.write_text("{not json")
    stamp = override.stat().st_mtime + 10
    os.utime(override, (stamp, stamp))
    assert helmets.describe("Lakeview Kestrels") == KESTRELS
    # Removed: back to the curated file.
    override.unlink()
    assert helmets.describe("Lakeview Kestrels") == KESTRELS


def test_descriptions_are_one_line_and_bounded(tmp_path: Path) -> None:
    override = tmp_path / "config" / "helmets.json"
    write_override(
        override,
        {"Lakeview Kestrels": "glossy navy\n helmet   with\ta stripe " + "x" * 1000, "": "nameless", "Herons": 7},
    )
    helmets = Helmets(override, resource=resource(tmp_path))
    text = helmets.describe("Lakeview Kestrels")
    assert text is not None and text.startswith("glossy navy helmet with a stripe x") and len(text) == 400
    assert helmets.describe("Herons") is None


def test_prompt_uses_the_description_or_the_generic_phrase() -> None:
    prompt = matchup_prompt("Lakeview Kestrels", "Riverside Otters", KESTRELS, None)
    assert f"Lakeview Kestrels' {KESTRELS} on the left" in prompt
    assert (
        "the Riverside Otters American football helmet with its real design and logo clearly visible on the right"
        in prompt
    )
    assert helmet_phrase("Harbor Heron", "glossy white helmet") == "Harbor Heron's glossy white helmet"
    # The style stays, and no colour codes reach the model.
    for words in (
        "Photorealistic broadcast background",
        "face to face in mid-air above a football field at night",
        "debris and sparks",
        "stadium lights bokeh",
        "cinematic",
        "16:9",
        "no other text",
        "no watermark",
    ):
        assert words in prompt
    assert "#" not in prompt


def test_key_changes_when_a_description_changes() -> None:
    plain = matchup_key("Lakeview Kestrels", "Brookfield Badgers", "college-football")
    described = matchup_key("Lakeview Kestrels", "Brookfield Badgers", "college-football", KESTRELS, BADGERS)
    corrected = matchup_key(
        "Lakeview Kestrels", "Brookfield Badgers", "college-football", KESTRELS, "glossy silver helmet"
    )
    assert len({plain, described, corrected}) == 3
    assert described == matchup_key("lakeview kestrels", "Brookfield Badgers", "College-Football", KESTRELS, BADGERS)


async def test_art_asks_with_descriptions_and_a_correction_is_a_new_image(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []
    placeholder = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"

    def handle(request: httpx.Request) -> httpx.Response:
        import base64

        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(placeholder).decode()}]})

    override = tmp_path / "config" / "art" / "helmets.json"
    art = MatchupArt(
        tmp_path / "config" / "art" / "matchups",
        api_key="xai-fixture-key-0123456789",
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        sign=Crypto("fixture-art-signing-key-0123456789").sign_json,
        helmets=Helmets(override, resource=resource(tmp_path)),
    )
    assert art.url_for("Lakeview Kestrels", "Brookfield Badgers", "college-football") is None
    await art.process_next()
    assert KESTRELS in sent[0]["prompt"] and BADGERS in sent[0]["prompt"]
    assert art.url_for("Lakeview Kestrels", "Brookfield Badgers", "college-football") is not None
    # The owner corrects one team: the old image is not served, a new one is made.
    write_override(override, {"Brookfield Badgers": "glossy silver helmet with a maroon stripe"})
    assert art.url_for("Lakeview Kestrels", "Brookfield Badgers", "college-football") is None
    await art.process_next()
    assert "glossy silver helmet with a maroon stripe" in sent[1]["prompt"] and BADGERS not in sent[1]["prompt"]


def test_the_shipped_descriptions_are_well_formed() -> None:
    data = json.loads(RESOURCE.read_text(encoding="utf-8"))
    teams = data["teams"]
    assert len(teams) >= 50
    for name, text in teams.items():
        assert isinstance(text, str) and text and len(text) <= 400 and "\n" not in text, name
        assert "#" not in text and "helmet" in text, name
    assert Helmets(resource=RESOURCE).count() == len(teams)
