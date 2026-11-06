"""Validate a model's normalised time: an ISO 8601 date, date-time or A/B interval. Dates are never computed here."""
import re
from datetime import date, datetime

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?")
_YEAR = re.compile(r"(?<!\d)\d{4}(?!\d)")


def _is_point(text: str) -> bool:
    """A real calendar date or date-time in one of the accepted shapes (the regexes fix the shape, fromisoformat the values)."""
    try:
        if _DATE.fullmatch(text):
            date.fromisoformat(text)
        elif _DATETIME.fullmatch(text):
            datetime.fromisoformat(text)
        else:
            return False
    except ValueError:
        return False
    return True


def _is_relative(time_text: str | None) -> bool:
    """Relative when there is time text but it names no 4-digit year."""
    return time_text is not None and _YEAR.search(time_text) is None


def check_normalized(value: str | None, published_at: datetime | None, time_text: str | None = None) -> str | None:
    """Return `value` if it is a valid date, date-time or interval, else None.

    Without a publication date, relative time text (no 4-digit year) cannot be anchored, so it yields None.
    """
    if not value:
        return None
    parts = value.split("/")
    if len(parts) > 2 or not all(_is_point(part) for part in parts):
        return None
    if published_at is None and _is_relative(time_text):
        return None
    return value
