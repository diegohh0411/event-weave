import pytest
import sqlalchemy as sa
from weave_clusterer.embed import EmbedSettings, EmbeddingGemmaEmbedder
from weave_core.adapters import AdapterContext, AdapterFailure, UnitRef
from weave_core.schema import ledger
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.clients import stub
from weave_inference.ledger import request_hash
from weave_inference.types import EmbeddingsRequest

UNIT = UnitRef(UnitKind.mention, "m1", "h")
EMPTY = {"text": None, "spans": [], "confidence": None}


def enrich(**fields):
    e = {f: dict(EMPTY) for f in ("action", "participants", "location", "time")}
    for k, v in fields.items():
        e[k] = {"text": v, "spans": [(0, 1)] if v else [], "confidence": 1.0}
    return {Stage.enrich: {"enrichment": e, "enrichment_id": "enr1"}}


def embedder(dimension=stub.DIM):
    ctx = AdapterContext(run_id=None, seed=1, deployments={Capability.embeddings: "stub-emb"},
                         stage=Stage.embed, adapter="embedding-gemma")
    return EmbeddingGemmaEmbedder(EmbedSettings(dimension=dimension), ctx)


def ledger_rows(db):
    with db.connect() as c:
        return c.execute(sa.select(ledger)).fetchall()


def test_embeds_non_empty_fields_with_prefixes(db, stub_inference):
    texts = ["title: none | text: arrested", "task: search result | query: arrested",
             "title: none | text: Monday", "task: search result | query: Monday"]
    result = embedder().process(UNIT, enrich(action="arrested", time="Monday"))
    assert result.emits == [UNIT]
    assert result.output["fields"] == ["action", "time"]
    rows = result.rows["member_vectors"]
    assert [(r["field"], r["kind"]) for r in rows] == [("action", "document"), ("action", "query"),
                                                       ("time", "document"), ("time", "query")]
    assert {r["mention_id"] for r in rows} == {"m1"} and {r["enrichment_id"] for r in rows} == {"enr1"}
    assert [r["vector"] for r in rows] == [stub._vector(t) for t in texts]
    assert set(result.output["vector_ids"].values()) == {r["id"] for r in rows}
    entries = ledger_rows(db)
    assert len(entries) == 1
    assert entries[0].request_hash == request_hash(EmbeddingsRequest(texts=texts).model_dump(mode="json"))


def test_no_fields_no_call(db, stub_inference):
    result = embedder().process(UNIT, enrich())
    assert result.output == {} and result.emits == [UNIT] and not result.rows
    assert ledger_rows(db) == []


def test_wrong_dimension_fails_non_retryable(db, stub_inference):
    with pytest.raises(AdapterFailure) as e:
        embedder(dimension=16).process(UNIT, enrich(action="arrested"))
    assert e.value.retryable is False


def test_vector_ids_stable(db, stub_inference):
    a = embedder().process(UNIT, enrich(action="arrested"))
    b = embedder().process(UNIT, enrich(action="arrested"))
    assert a.output["vector_ids"] == b.output["vector_ids"]
    assert [r["id"] for r in a.rows["member_vectors"]] == [r["id"] for r in b.rows["member_vectors"]]
    assert len(a.rows["member_vectors"]) == 2 and len({r["id"] for r in a.rows["member_vectors"]}) == 2
