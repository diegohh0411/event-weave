import json

import sqlalchemy as sa
from contract import run_contract
from ext_helpers import ctx, make_doc, store, unit
from weave_core.schema import gold_mentions
from weave_extractors.gold import GoldExtractor, GoldSettings

TEXT = "Police said the protest ended after the attack."


def seed(engine):
    doc = make_doc(TEXT)
    store(engine, doc)
    with engine.begin() as c:
        c.execute(sa.insert(gold_mentions), [
            {"document_id": doc.id, "mention_id": "T19", "start": 7, "end": 11, "text": "said"},
            {"document_id": doc.id, "mention_id": "T20", "start": 16, "end": 23, "text": "protest"},
            {"document_id": doc.id, "mention_id": "T21", "start": 40, "end": 46, "text": "attack"}])
    return doc


def test_gold_equals_gold_spans(db):
    doc = seed(db)
    result = GoldExtractor(GoldSettings(), ctx()).process(unit(doc), {})
    assert [(m["start"], m["end"]) for m in result.output["mentions"]] == [(7, 11), (16, 23), (40, 46)]
    dumped = json.dumps(result.output) + json.dumps(result.rows) + repr(result.emits)
    assert not any(gid in dumped for gid in ("T19", "T20", "T21"))


def test_gold_contract(db):
    docs = [seed(db)]
    store(db, make_doc("", doc_id="fake:empty"))
    run_contract(lambda d: GoldExtractor(GoldSettings(), ctx()), docs)
