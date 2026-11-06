"""The mention shape and the document view extractors work on."""
import hashlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

import sqlalchemy as sa
from pydantic import BaseModel
from weave_core.adapters import AdapterFailure, UnitRef
from weave_core.db import make_engine
from weave_core.schema import documents
from weave_core.settings import Settings
from weave_core.stages import UnitKind

# Kept here rather than imported: a stage package never depends on another stage package.
ISO_CODES = {"english": "en", "spanish": "es", "hindi": "hi", "turkish": "tr", "ukrainian": "uk"}


class Mention(BaseModel):
    document_id: str
    start: int
    end: int
    tok_start: int
    tok_end: int
    sentence: int
    text: str
    extractor: str
    extractor_version: str
    probability: float | None = None

    @property
    def id(self) -> str:
        key = f"{self.document_id}:{self.start}:{self.end}:{self.extractor}:{self.extractor_version}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    def unit(self, document_content_hash: str) -> UnitRef:
        key = f"{document_content_hash}:{self.start}:{self.end}:{self.extractor}:{self.extractor_version}"
        return UnitRef(UnitKind.mention, self.id, hashlib.sha256(key.encode()).hexdigest())

    def row(self) -> dict:
        return {"id": self.id, **self.model_dump()}


@dataclass(frozen=True)
class Doc:
    id: str
    language: str  # ISO 639-1
    title: str
    text: str
    content_hash: str
    token_offsets: list[tuple[int, int]]
    sentence_offsets: list[tuple[int, int]]


def iso(language: str) -> str:
    return ISO_CODES.get(language.lower(), language.lower())


def load_document(doc_id: str) -> Doc:
    """Adapters receive no engine; like the replay provider, read the database from the environment."""
    d = documents.c
    engine = make_engine(Settings.from_env().database_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.select(d.id, d.language, d.title, d.text, d.content_hash, d.token_offsets,
                                         d.sentence_offsets).where(d.id == doc_id)).first()
    finally:
        engine.dispose()
    if row is None:
        raise AdapterFailure(f"document not found: {doc_id}", retryable=False)
    return Doc(row.id, iso(row.language), row.title, row.text, row.content_hash,
               [tuple(t) for t in row.token_offsets], [tuple(s) for s in row.sentence_offsets])


def char_to_token_span(doc: Doc, start: int, end: int) -> tuple[int, int, int]:
    """(tok_start, tok_end, sentence) of the stored tokens covering [start, end)."""
    ends = [e for _, e in doc.token_offsets]
    starts = [s for s, _ in doc.token_offsets]
    tok_start = bisect_right(ends, start)
    tok_end = max(bisect_left(starts, end), tok_start)
    sent_starts = [s for s, _ in doc.sentence_offsets]
    sentence = max(bisect_right(sent_starts, start) - 1, 0)
    return tok_start, tok_end, sentence
