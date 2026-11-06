import hashlib
import uuid

import sqlalchemy as sa
from weave_core.schema import documents, enrichments, jobs, mentions, runs, stage_outputs
from weave_enrichers.report import CoverageRow, coverage, sample

TEXT = "Police arrested two men. Talks resumed."


def seed(db, deployments=None):
    run_id = uuid.uuid4()
    with db.begin() as c:
        c.execute(sa.insert(runs).values(
            id=run_id, config={"stages": {"enrich": {"adapter": "clairic", "deployments": deployments or {}}}}, config_hash="h", state="running",
            created_at=sa.func.now()))
        for did, lang in (("fake:en", "en"), ("fake:es", "es")):
            if c.scalar(sa.select(documents.c.id).where(documents.c.id == did)) is None:
                c.execute(sa.insert(documents).values(
                    id=did, provider="fake", external_id=did[5:], language=lang, title="T", byline="", outlet="",
                    url="", text=TEXT, token_offsets=[], sentence_offsets=[[0, 24], [25, 38]], content_hash="x",
                    order_key=[0]))
    return run_id


def add(db, run_id, doc, i, enricher, fields, discarded, status="done", deployment=None):
    mid = f"m-{doc}-{i}"
    key = hashlib.sha256(f"{mid}:{run_id}".encode()).hexdigest()
    with db.begin() as c:
        if c.scalar(sa.select(mentions.c.id).where(mentions.c.id == mid)) is None:
            c.execute(sa.insert(mentions).values(
                id=mid, document_id=f"fake:{doc}", start=7, end=15, tok_start=0, tok_end=0, sentence=0,
                text="arrested", extractor="gold", extractor_version="1"))
        c.execute(sa.insert(jobs).values(
            run_id=run_id, stage="enrich", unit_kind="mention", unit_id=mid, unit_content_hash="x", partition="p",
            order_key=[i], input_hash="i", status=status, available_at=sa.func.now(),
            cache_key=key if status == "done" else None))
        if status != "done":
            return
        shaped = {n: {"text": fields.get(n), "spans": [], "confidence": None} for n in ("action", "participants", "location", "time")}
        c.execute(sa.insert(stage_outputs).values(
            cache_key=key, stage="enrich", adapter=enricher, adapter_version="1",
            output={"enrichment": shaped, "discarded": discarded}, emits=[], created_at=sa.func.now()))
        c.execute(sa.insert(enrichments).values(
            id=f"e-{mid}-{deployment}", mention_id=mid, enricher=enricher, enricher_version="1", deployment=deployment,
            discarded=discarded, **shaped))


def rows_by(rows):
    return {(r.enricher, r.language, r.field): r for r in rows}


def test_coverage_counts_shares_and_discards(db):
    run_id = seed(db)
    add(db, run_id, "en", 0, "clairic", {"action": "arrested", "location": "Madrid"}, {"location": 1})
    add(db, run_id, "en", 1, "clairic", {"action": "arrested"}, {"location": 2, "misaligned": 1})
    add(db, run_id, "es", 2, "clairic", {}, {"unparsed": 1})
    add(db, run_id, "es", 3, "clairic", {}, {}, status="failed")
    r = rows_by(coverage(db, run_id))
    assert r["clairic", "en", "action"] == CoverageRow("clairic", "en", "action", 2, 2, 1.0, 0)
    assert r["clairic", "en", "location"] == CoverageRow("clairic", "en", "location", 2, 1, 0.5, 3)
    assert r["clairic", "en", "time"].share == 0 and r["clairic", "en", "time"].filled == 0
    assert r["clairic", "en", "misaligned"] == CoverageRow("clairic", "en", "misaligned", 2, 0, 0.0, 1)
    assert r["clairic", "es", "unparsed"].discarded == 1
    assert r["clairic", "es", "action"].mentions == 1
    assert r["clairic", "es", "(failed)"].mentions == 1


def test_empty_run_has_no_rows(db):
    assert coverage(db, seed(db)) == []


def test_sample_is_deterministic_and_marked(db):
    run_id = seed(db)
    for i in range(6):
        add(db, run_id, "en", i, "clairic", {"action": "arrested"}, {})
    a, b = sample(db, run_id, 3, seed=1), sample(db, run_id, 3, seed=1)
    assert a == b and len(a) == 3
    ids = [s["id"] for s in sample(db, run_id, 6, seed=1)]
    assert ids == sorted(ids, key=lambda i: hashlib.sha256(f"1:{i}".encode()).hexdigest())
    assert a[0]["sentence"] == "Police ⟦arrested⟧ two men." and a[0]["enrichment"]["action"]["text"] == "arrested"
    assert a[0]["enricher"] == "clairic" and a[0]["mention_id"].startswith("m-en-")


def test_sample_only_the_runs_own_enrichments(db):
    run_a, run_b = seed(db, {"extraction": "dep-a"}), seed(db, {"extraction": "dep-b"})
    for i in range(3):
        add(db, run_a, "en", i, "clairic", {"action": "arrested"}, {}, deployment="dep-a")
        add(db, run_b, "en", i, "clairic", {"action": "arrested"}, {}, deployment="dep-b")
    got = sample(db, run_a, 10, seed=1)
    assert len(got) == 3 and {s["deployment"] for s in got} == {"dep-a"}
    assert len({s["id"] for s in got}) == 3
