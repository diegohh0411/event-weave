"""Shared extract-stage behaviour: load the document, validate spans, build the result."""
from dataclasses import dataclass, field
from typing import Mapping

from pydantic import BaseModel
from weave_core.adapters import AdapterContext, StageResult, UnitRef
from weave_core.stages import Stage

from .shape import Doc, Mention, char_to_token_span, load_document


@dataclass
class SpanBatch:
    spans: list[tuple[int, int, float | None]]
    extra: dict = field(default_factory=dict)


class ExtractorBase:
    spec = None  # set by subclasses

    def __init__(self, settings: BaseModel, ctx: AdapterContext):
        self.settings = settings
        self.ctx = ctx

    def load_document(self, doc_id: str) -> Doc:
        return load_document(doc_id)

    def spans(self, doc: Doc) -> SpanBatch:
        raise NotImplementedError

    def process(self, unit: UnitRef, upstream: Mapping[Stage, dict]) -> StageResult:
        doc = self.load_document(unit.id)
        batch = self.spans(doc) if doc.text else SpanBatch([])
        extra = dict(batch.extra)
        valid, misaligned = [], 0
        for start, end, p in batch.spans:
            if 0 <= start < end <= len(doc.text) and doc.text[start:end].strip():
                valid.append((start, end, p))
            else:
                misaligned += 1
        extra["misaligned"] = extra.get("misaligned", 0) + misaligned
        mentions = [self._mention(doc, s, e, p) for s, e, p in _longest_first(valid)]
        return StageResult(output={"mentions": [m.model_dump() for m in mentions], **extra},
                           emits=[m.unit(doc.content_hash) for m in mentions],
                           rows={"mentions": [m.row() for m in mentions]})

    def _mention(self, doc: Doc, start: int, end: int, p: float | None) -> Mention:
        tok_start, tok_end, sentence = char_to_token_span(doc, start, end)
        return Mention(document_id=doc.id, start=start, end=end, tok_start=tok_start, tok_end=tok_end,
                       sentence=sentence, text=doc.text[start:end], extractor=self.spec.name,
                       extractor_version=self.spec.version, probability=p)


def _longest_first(spans):
    """Drop spans overlapping a longer kept one (ties: earlier start); return sorted by (start, end)."""
    kept: list[tuple[int, int, float | None]] = []
    for s, e, p in sorted(spans, key=lambda x: (-(x[1] - x[0]), x[0])):
        if all(e <= ks or s >= ke for ks, ke, _ in kept):
            kept.append((s, e, p))
    return sorted(kept, key=lambda x: (x[0], x[1]))
