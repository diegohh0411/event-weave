"""Check model answers against the document: exact text only, nothing fuzzy-matched."""
from .shape import Field


def locate_nearest(doc_text: str, needle: str, anchor: int) -> tuple[int, int] | None:
    """The exact, case-sensitive occurrence starting closest to `anchor` (ties: earlier); None if absent or blank."""
    if not needle or not needle.strip():
        return None
    best, pos = None, doc_text.find(needle)
    while pos != -1:
        if best is None or abs(pos - anchor) < abs(best - anchor):
            best = pos
        pos = doc_text.find(needle, pos + 1)
    return None if best is None else (best, best + len(needle))


def verify_field(doc_text: str, answers: list[str], anchor: int, *, multi: bool) -> tuple[Field, int]:
    """Locate each answer; unlocated ones are dropped and counted. `multi=False` keeps the single closest."""
    found: list[tuple[int, int]] = []
    discarded = 0
    for answer in answers:
        span = locate_nearest(doc_text, answer, anchor)
        if span is None:
            discarded += 1
        elif span not in found:
            found.append(span)
    if not multi and found:
        found = [min(found, key=lambda s: (abs(s[0] - anchor), s[0]))]
    if not found:
        return Field(), discarded
    return Field(text="; ".join(doc_text[s:e] for s, e in found), spans=found), discarded
