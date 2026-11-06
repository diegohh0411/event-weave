"""Test helpers: build documents and store them."""
import hashlib
import re
from uuid import uuid4

import sqlalchemy as sa
from weave_core.adapters import AdapterContext, UnitRef
from weave_core.schema import documents
from weave_core.stages import UnitKind
from weave_extractors.shape import Doc


def make_doc(text: str, doc_id: str = "fake:d1", language: str = "en", title: str = "Title") -> Doc:
    tokens = [m.span() for m in re.finditer(r"\w+|[^\w\s]", text)]
    sentences, start = [], 0
    for m in re.finditer(r"[.!?](?=\s|$)", text):
        sentences.append((start, m.end()))
        start = m.end() + 1
    if text[start:].strip():
        sentences.append((start, len(text)))
    return Doc(doc_id, language, title, text, hashlib.sha256(text.encode()).hexdigest(), tokens, sentences)


def store(engine, doc: Doc) -> None:
    provider, external = doc.id.split(":", 1)
    with engine.begin() as c:
        c.execute(sa.insert(documents).values(
            id=doc.id, provider=provider, external_id=external, language=doc.language, title=doc.title,
            byline="", outlet="", url="", published_at=None, text=doc.text,
            token_offsets=[list(t) for t in doc.token_offsets], sentence_offsets=[list(s) for s in doc.sentence_offsets],
            content_hash=doc.content_hash, order_key=[0]))


def ctx(**deployments) -> AdapterContext:
    """No run row exists in unit tests, so run_id is None (ledger.run_id is nullable)."""
    from weave_core.stages import Capability
    return AdapterContext(run_id=None, seed=1, deployments={Capability(k): v for k, v in deployments.items()})


def unit(doc: Doc) -> UnitRef:
    return UnitRef(UnitKind.document, doc.id, doc.content_hash, (0,))
