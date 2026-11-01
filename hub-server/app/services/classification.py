"""Classification evidence ingestion only. This module makes no access decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.sqlite import insert

from app.infra.db import Database
from app.infra.models import AgeClassification

MOVIE_CODES = {"G", "PG", "PG-13", "R", "NC-17"}
TV_CODES = {"TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14", "TV-MA"}
UNRATED = {"NR", "UR", "UNRATED", "NOT RATED", "N/A"}


def classification_record(country: str | None, system: str | None, value: str, **extra: Any) -> dict[str, Any]:
    code = value.strip().upper()
    descriptors = []
    if system == "US-TVPG" and code == "TV-Y7-FV":
        code, descriptors = "TV-Y7", ["FV"]
    supported = country == "US" and (
        (system == "US-MPA" and code in MOVIE_CODES) or (system == "US-TVPG" and code in TV_CODES)
    )
    return {
        "country": country,
        "system": system,
        "raw_value": value[:128],
        "code": code[:128],
        "descriptors": descriptors,
        "status": "known" if supported else "unknown",
        "reason": None if supported else "unrated" if code in UNRATED else "unsupported_or_missing",
        **extra,
    }


def summarize(
    provider: str, scope: str, records: list[dict[str, Any]], *, loaded: bool = True, truncated: bool = False
) -> dict[str, Any]:
    us = [r for r in records if r["country"] == "US"]
    known = {(r["system"], r["code"]) for r in us if r["status"] == "known"}
    uncertain = any(r["status"] != "known" and r["code"] for r in us)
    status = "known" if len(known) == 1 and not uncertain else "conflicting" if len(known) > 1 else "unknown"
    reason = (
        None
        if status == "known"
        else "conflicting_classifications"
        if status == "conflicting"
        else "no_supported_classification"
    )
    if not loaded or truncated:
        status, reason = "unknown", "not_loaded" if not loaded else "truncated_evidence"
    elif uncertain:
        status, reason = "unknown", "unrated_or_unsupported"
    return {
        "schema_version": 1,
        "assessment_region": "US",
        "provider": provider,
        "scope": scope,
        "status": status,
        "reason": reason,
        "records": records,
    }


def tmdb_age_evidence(data: dict[str, Any], media_type: str) -> dict[str, Any]:
    field = "release_dates" if media_type == "movie" else "content_ratings"
    scope = "movie" if media_type == "movie" else "series"
    block = data.get(field)
    groups = block.get("results", []) if isinstance(block, dict) else []
    records: list[dict[str, Any]] = []
    truncated = False
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        country = str(group.get("iso_3166_1") or "").upper() or None
        system = ("US-MPA" if media_type == "movie" else "US-TVPG") if country == "US" else None
        entries = group.get("release_dates", []) if media_type == "movie" else [group]
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if len(records) >= 64:
                truncated = True
                break
            value = entry.get("certification" if media_type == "movie" else "rating")
            record = classification_record(country, system, str(value or ""))
            if media_type == "movie":
                record.update(release_type=entry.get("type"), release_date=str(entry.get("release_date") or "")[:40])
            records.append(record)
    result = summarize("tmdb", scope, records, loaded=isinstance(block, dict), truncated=truncated)
    result["subject_key"] = f"tmdb:{media_type}:{data.get('id')}"
    result["retrieved_at"] = data.get("_streamhub_retrieved_at")
    return result


def xmltv_age_evidence(values: list[tuple[str | None, str]]) -> dict[str, Any]:
    aliases = {
        "MPAA": "US-MPA",
        "MPA": "US-MPA",
        "US-MPA": "US-MPA",
        "TVPG": "US-TVPG",
        "TV-PG": "US-TVPG",
        "US-TVPG": "US-TVPG",
        "US-TV": "US-TVPG",
    }
    records = []
    for raw_system, value in values[:16]:
        system = aliases.get((raw_system or "").strip().upper())
        records.append(
            classification_record("US" if system else None, system, value, raw_system=(raw_system or "")[:128])
        )
    return summarize("xmltv", "programme", records, truncated=len(values) > 16)


class ClassificationStore:
    """Persist independent evidence snapshots; never modifies profiles or playback."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, subject_key: str, evidence: dict[str, Any]) -> None:
        provider = str(evidence.get("provider") or "tmdb")
        value = {**evidence, "subject_key": subject_key}
        async with self._db.session() as session:
            statement = insert(AgeClassification).values(
                subject_key=subject_key, provider=provider, evidence=value, observed_at=datetime.now(UTC)
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[AgeClassification.subject_key, AgeClassification.provider],
                    set_={"evidence": statement.excluded.evidence, "observed_at": statement.excluded.observed_at},
                    where=AgeClassification.evidence != statement.excluded.evidence,
                )
            )
            await session.commit()
