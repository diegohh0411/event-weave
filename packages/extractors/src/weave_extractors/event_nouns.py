"""Per-language event-noun lemma lists, built from MCECR train-split gold triggers."""
from functools import cache
from importlib.resources import files

import sqlalchemy as sa
from weave_core.schema import documents, gold_documents, gold_mentions

from .shape import iso
from .stanza_client import StanzaClient


@cache
def load_event_nouns(lang: str) -> frozenset[str]:
    path = files("weave_extractors") / "data" / "event_nouns" / f"{lang}.txt"
    if not path.is_file():
        return frozenset()
    return frozenset(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def build_event_nouns(engine, client: StanzaClient) -> dict[str, list[str]]:
    """Lowercase lemmas of NOUN words inside train-split gold triggers, per ISO language. Dev tool, not a stage."""
    d, gd, gm = documents.c, gold_documents.c, gold_mentions.c
    with engine.connect() as conn:
        docs = conn.execute(sa.select(d.id, d.language, d.text).join(gold_documents, gd.document_id == d.id)
                            .where(gd.split == "train").order_by(d.id)).all()
        spans: dict[str, list[tuple[int, int]]] = {}
        for row in conn.execute(sa.select(gm.document_id, gm.start, gm.end).join(
                gold_documents, gd.document_id == gm.document_id).where(gd.split == "train")):
            spans.setdefault(row.document_id, []).append((row.start, row.end))
    nouns: dict[str, set[str]] = {}
    for doc in docs:
        lang = iso(doc.language)
        found = nouns.setdefault(lang, set())
        triggers = spans.get(doc.id, [])
        for sentence in client.parse(doc.text, lang):
            for w in sentence.words:
                if w.upos == "NOUN" and w.lemma and any(s <= w.start and w.end <= e for s, e in triggers):
                    found.add(w.lemma.lower())
    return {lang: sorted(v) for lang, v in nouns.items()}
