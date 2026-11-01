"""Published playback contracts preserve the backend's additive safety fields."""

from pathlib import Path

import pytest

from app.api import schemas

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"


def shape(value):
    if "anyOf" in value:
        variants = value["anyOf"]
        nullable = any(v.get("type") == "null" for v in variants)
        value = next(v for v in variants if v.get("type") != "null")
    else:
        nullable = value.get("nullable", False)
    return (
        value.get("type"),
        nullable,
        value.get("$ref"),
        value.get("enum"),
        value.get("minLength"),
        value.get("maxLength"),
        value.get("minItems"),
        value.get("maxItems"),
        shape(value["items"]) if "items" in value else None,
    )


def test_published_playback_safety_fields_match_backend():
    yaml = pytest.importorskip("yaml")
    contract = yaml.safe_load(CONTRACT.read_text())
    selected = {
        "PlaybackDescriptor": [
            "preferred_audio_languages",
            "audio_language_required",
            "audio_languages",
            "audio_verified",
            "audio_evidence",
            "default_audio_language",
        ],
        "PlaybackRequest": ["multiview", "companion_id"],
        "ProgressReport": ["expected_profile_id"],
        "MultiviewRequest": ["panes", "recovery_generation"],
        "MultiviewDescriptor": ["lease_id", "expires_in_seconds", "panes"],
        "MultiviewRelease": ["lease_id"],
    }
    for name, fields in selected.items():
        actual = getattr(schemas, name).model_json_schema(ref_template="#/components/schemas/{model}")
        published = contract["components"]["schemas"][name]
        for field in fields:
            assert shape(published["properties"][field]) == shape(actual["properties"][field]), (name, field)
            assert (field in published.get("required", [])) == (field in actual.get("required", [])), (name, field)
    paths = contract["paths"]
    for path, request in [
        ("/playback/multiview", "MultiviewRequest"),
        ("/playback/multiview/release", "MultiviewRelease"),
    ]:
        operation = paths[path]["post"]
        assert operation["requestBody"]["required"] is True
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/" + request)
    assert paths["/playback/multiview"]["post"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/MultiviewDescriptor")
    assert "204" in paths["/playback/multiview/release"]["post"]["responses"]
