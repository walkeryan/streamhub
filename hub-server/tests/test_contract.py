"""Every path+method in contracts/openapi.yaml must be implemented under /api/v1."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.infra.settings import AppSettings
from tests.conftest import Api

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
METHODS = ("get", "post", "put", "patch", "delete")


@pytest.fixture
def settings(settings: AppSettings) -> AppSettings:
    # The served schema is what these tests compare against; it is off unless enabled.
    return settings.model_copy(update={"api_docs_enabled": True})


def contract_ops() -> set[tuple[str, str]]:
    yaml = pytest.importorskip("yaml")
    if not CONTRACT.exists():
        pytest.skip("contract not present")
    spec = yaml.safe_load(CONTRACT.read_text())
    return {(m.upper(), p) for p, item in spec["paths"].items() for m in METHODS if m in item}


async def test_all_contract_paths_implemented(api: Api) -> None:
    spec = (await api.client.get("/api/openapi.json")).json()
    served = {
        (m.upper(), re.sub(r"^/api/v1", "", p)) for p, item in spec["paths"].items() for m in METHODS if m in item
    }
    missing = contract_ops() - served
    assert not missing, f"not implemented: {sorted(missing)}"


async def test_docs_served(api: Api) -> None:
    assert (await api.client.get("/api/docs")).status_code == 200


async def test_spa_fallback(api: Api) -> None:
    dist = api.c.settings.web_dist
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "a.js").write_text("console.log(1)")
    assert (await api.client.get("/")).text == "<html>app</html>"
    assert (await api.client.get("/live/guide")).text == "<html>app</html>"
    r = await api.client.get("/assets/a.js")
    assert r.text == "console.log(1)" and "immutable" in r.headers["cache-control"]
    assert (await api.client.get("/../../etc/passwd")).text == "<html>app</html>"
    assert (await api.client.get("/api/v1/nope")).status_code == 404


async def test_qr_svg(api: Api) -> None:
    r = await api.client.get("/api/v1/qr", params={"data": "http://hub.local/pair?code=ABC123"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and b"<svg" in r.content


def _property(schema: dict[str, Any], name: str) -> tuple[Any, Any, bool]:
    """(type, item type, nullable) of one property, in a published or a generated schema."""
    value = schema["properties"][name]
    nullable = bool(value.get("nullable"))
    if "anyOf" in value:
        nullable = any(v.get("type") == "null" for v in value["anyOf"])
        value = next(v for v in value["anyOf"] if v.get("type") != "null")
    return value.get("type"), (value.get("items") or {}).get("type"), nullable


def test_integration_fields_are_published_as_the_backend_sends_them() -> None:
    yaml = pytest.importorskip("yaml")
    from app.api import schemas

    spec = yaml.safe_load(CONTRACT.read_text())
    published = spec["components"]["schemas"]
    for model, fields in {
        "Programme": ["is_sport", "teams"],
        "Recording": ["is_sport", "teams", "has_file"],
        "RoomJoin": ["member_token", "client"],
        "Settings": ["home_networks", "home_shelves", "home_shelves_unknown"],
    }.items():
        actual = getattr(schemas, model).model_json_schema()
        for field in fields:
            assert _property(published[model], field) == _property(actual, field), (model, field)
    assert (
        _property(published["Settings"], "home_shelves")[:2]
        == _property(schemas.SettingsPatch.model_json_schema(), "home_shelves")[:2]
    )
    # The member's descriptor: asked with the member token, never a bearer.
    descriptor = spec["paths"]["/rooms/{code}/descriptor"]["get"]
    assert descriptor["security"] == []
    params = {p["name"]: p for p in descriptor["parameters"]}
    assert set(params) == {
        "code",
        "member_token",
        "client",
        "prefer_hls",
        "recovery_generation",
        "allow_unverified_audio",
    }
    assert params["member_token"]["required"] is True and params["client"]["schema"]["enum"] == ["android_tv", "web"]
    assert descriptor["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PlaybackDescriptor"
    )
    # The switch body takes an explicit null PIN.
    switch = spec["paths"]["/auth/switch"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert switch["properties"]["pin"]["nullable"] is True


async def test_room_descriptor_is_served_with_the_published_parameters(api: Api) -> None:
    served = (await api.client.get("/api/openapi.json")).json()["paths"]["/api/v1/rooms/{code}/descriptor"]["get"]
    assert {p["name"] for p in served["parameters"]} == {
        "code",
        "member_token",
        "client",
        "prefer_hls",
        "recovery_generation",
        "allow_unverified_audio",
    }
    assert "security" not in served or served["security"] == []
