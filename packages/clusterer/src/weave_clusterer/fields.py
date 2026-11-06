"""Enrichment fields the clusterer works with."""

FIELDS = ("action", "participants", "location", "time")


def field_text(enrichment: dict, field: str) -> str | None:
    """The field's text, or None when it is missing, null or blank."""
    text = (enrichment.get(field) or {}).get("text")
    return text if isinstance(text, str) and text.strip() else None
