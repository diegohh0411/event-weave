"""The text an enricher sees: a few sentences around the mention, with the trigger wrapped in ⟦⟧."""
from dataclasses import dataclass

from ._shared import Doc, mark


@dataclass(frozen=True)
class MentionWindow:
    text: str  # window text with the trigger wrapped as ⟦trigger⟧
    offset: int  # document char offset of the window start
    trigger: tuple[int, int]  # trigger span in document offsets
    header: str  # "Title: …\nOutlet: …\nPublished: …", absent lines omitted


def build_header(doc: Doc) -> str:
    lines = []
    if doc.title:
        lines.append(f"Title: {doc.title}")
    if doc.outlet:
        lines.append(f"Outlet: {doc.outlet}")
    if doc.published_at is not None:
        lines.append(f"Published: {doc.published_at.isoformat()}")
    return "\n".join(lines)


def build_window(doc: Doc, mention: dict, sentences: int) -> MentionWindow:
    start, end = mention["start"], mention["end"]
    offsets = doc.sentence_offsets
    if offsets:
        i = min(max(mention.get("sentence", 0), 0), len(offsets) - 1)
        w_start = offsets[max(i - sentences, 0)][0]
        w_end = offsets[min(i + sentences, len(offsets) - 1)][1]
    else:
        w_start, w_end = 0, len(doc.text)
    w_start, w_end = min(w_start, start), max(w_end, end)
    return MentionWindow(mark(doc.text[w_start:w_end], start - w_start, end - w_start), w_start, (start, end),
                         build_header(doc))
