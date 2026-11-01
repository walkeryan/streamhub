"""Audio evidence is separate from provider labels and original movie language.

Only imported stream/track metadata or a media probe can establish audio languages.
Category/title hints are displayed for an explicit choice, never an automatic match.
"""

from __future__ import annotations

from typing import Any

from app.adapters.language.inferrer import lang_from_group, normalise_code

EVIDENCE = {"provider_audio_tracks", "media_probe"}


def audio_info(extra: dict[str, Any], *, label: str = "", group: str | None = None) -> dict[str, Any]:
    raw = extra.get("audio_tracks")
    evidence = extra.get("audio_evidence")
    tracks = [t for t in raw if isinstance(t, dict)] if isinstance(raw, list) and evidence in EVIDENCE else []
    languages = [normalise_code(str(t.get("language") or "")) for t in tracks]
    known = sorted({v for v in languages if v and v != "und"})
    defaults = [languages[i] for i, t in enumerate(tracks) if t.get("default") is True]
    default = defaults[0] if len(defaults) == 1 else None
    # A single-language file needs no track switching. Mixed/untagged audio
    # without one declared default is ambiguous and requires explicit choice.
    if not default and languages and len(set(languages)) == 1:
        default = languages[0]
    if default == "und":
        default = None
    hint = normalise_code(lang_from_group(group or "") or lang_from_group(label) or "")
    return {
        "audio_languages": known,
        "audio_verified": bool(known),
        "audio_evidence": "audio_track_metadata"
        if evidence == "provider_audio_tracks" and known
        else "media_probe"
        if evidence == "media_probe" and known
        else "unknown",
        "default_audio_language": default,
        "language_hint": hint or None,
    }


def preferred_audio(info: dict[str, Any], preferred: list[str]) -> bool:
    return bool(info["audio_verified"] and info["default_audio_language"] in preferred)


def provider_audio_extra(raw: Any) -> dict[str, Any]:
    """Normalize explicit provider audio-stream objects, never generic language."""
    rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    tracks = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("codec_type") not in (None, "audio"):
            continue
        raw_tags = row.get("tags")
        tags = raw_tags if isinstance(raw_tags, dict) else {}
        code = normalise_code(
            str(row.get("languageCode") or row.get("Language") or tags.get("language") or row.get("language") or "")
        )
        raw_disposition = row.get("disposition")
        disposition = raw_disposition if isinstance(raw_disposition, dict) else {}
        default = row.get("IsDefault", disposition.get("default", row.get("default")))
        tracks.append({"language": code or "und", "default": default is True or default == 1})
    return {"audio_tracks": tracks, "audio_evidence": "provider_audio_tracks"} if tracks else {}
