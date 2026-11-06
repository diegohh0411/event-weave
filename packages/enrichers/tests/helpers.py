"""Test helpers: build documents."""
import hashlib
import re
from datetime import datetime

from weave_core.adapters import AdapterContext, UnitRef
from weave_core.stages import UnitKind
from weave_enrichers._shared import Doc, mention_id


def make_doc(text: str, doc_id: str = "fake:d1", title: str = "Title", outlet: str | None = None,
             published_at: datetime | None = None) -> Doc:
    tokens = [m.span() for m in re.finditer(r"\w+|[^\w\s]", text)]
    sentences, start = [], 0
    for m in re.finditer(r"[.!?](?=\s|$)", text):
        sentences.append((start, m.end()))
        start = m.end() + 1
    if text[start:].strip():
        sentences.append((start, len(text)))
    return Doc(doc_id, "en", title, text, hashlib.sha256(text.encode()).hexdigest(), tokens, sentences,
               outlet, published_at)


def make_mention(doc: Doc, start: int, end: int, sentence: int = 0) -> dict:
    return {"document_id": doc.id, "start": start, "end": end, "tok_start": 0, "tok_end": 0, "sentence": sentence,
            "text": doc.text[start:end], "extractor": "fake", "extractor_version": "1", "deployment": None,
            "probability": None}


def unit_for(mention: dict, doc: Doc) -> UnitRef:
    return UnitRef(UnitKind.mention, mention_id(mention), doc.content_hash)


def ctx() -> AdapterContext:
    return AdapterContext(run_id=None, seed=1, deployments={})
