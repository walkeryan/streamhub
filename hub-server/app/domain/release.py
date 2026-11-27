"""A release date is a calendar date supplied by metadata, separate from the date added."""

from datetime import date


def release_date(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if len(value) < 10 or (len(value) > 10 and value[10] not in ("T", " ")):
        return None
    day = value[:10]
    if day[4:5] != "-" or day[7:8] != "-":
        return None
    try:
        return date.fromisoformat(day).isoformat()
    except ValueError:
        return None


def release_year(raw: object) -> int | None:
    if isinstance(raw, (int, str)):
        text = str(raw).strip()
        if len(text) == 4 and text.isascii() and text.isdigit() and 1000 <= int(text) <= 9999:
            return int(text)
        day = release_date(text)
        if day:
            return int(day[:4])
    return None
