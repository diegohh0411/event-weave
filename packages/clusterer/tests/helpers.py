import math
import threading
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from weave_clusterer.threshold import ThresholdClusterer
from weave_core.registry import Registry
from weave_core.stages import Stage
from weave_core.writer import run_writer
from weave_clusterer.vectors import to_literal
from weave_core.schema import (cluster_decisions, cluster_members, clusters, documents, enrichments, jobs, member_vectors, mentions, runs,
                               stage_outputs)


def make_run(c) -> uuid.UUID:
    rid = uuid.uuid4()
    c.execute(sa.insert(runs).values(id=rid, config={}, config_hash="h", state="running",
                                     created_at=datetime.now(timezone.utc)))
    return rid


def add_member(c, run_id, partition, cluster_id, mention_id, vectors, order=(1,), enrichment=None,
               language="en", published_at=None, deployment="d"):
    """Seed one mention (own document), its cluster membership, document vectors and enrich output."""
    doc_id = "d-" + mention_id
    text = f"Sentence of {mention_id}."
    c.execute(sa.insert(documents).values(
        id=doc_id, provider="p", external_id=doc_id, language=language, title="t", byline="", outlet="o", url="u",
        published_at=published_at, text=text, token_offsets=[], sentence_offsets=[[0, len(text)]], content_hash="x"))
    c.execute(sa.insert(mentions).values(id=mention_id, document_id=doc_id, start=0, end=1, tok_start=0, tok_end=1,
                                         sentence=0, text="x", extractor="e", extractor_version="1"))
    c.execute(sa.insert(enrichments).values(id="e-" + mention_id + str(run_id)[:4], mention_id=mention_id))
    if c.execute(sa.select(clusters.c.id).where(clusters.c.id == cluster_id)).first() is None:
        c.execute(sa.insert(clusters).values(id=cluster_id, run_id=run_id, partition=partition, size=0))
    c.execute(sa.insert(cluster_members).values(run_id=run_id, mention_id=mention_id, cluster_id=cluster_id,
                                                partition=partition, order_key=list(order)))
    for f, vec in vectors.items():
        c.execute(sa.insert(member_vectors).values(
            id=f"v-{mention_id}-{f}-{str(run_id)[:4]}-{deployment}-{len(vec)}", mention_id=mention_id,
            enrichment_id="e-" + mention_id + str(run_id)[:4], field=f, kind="document", deployment=deployment,
            dimension=len(vec), vector=vec))
    if enrichment is not None:
        key = f"k-{mention_id}-{run_id}"
        c.execute(sa.insert(stage_outputs).values(cache_key=key, stage="enrich", adapter="a", adapter_version="1",
                                                  output={"enrichment": enrichment}, emits=[],
                                                  created_at=datetime.now(timezone.utc)))
        c.execute(sa.insert(jobs).values(run_id=run_id, stage="enrich", unit_kind="mention", unit_id=mention_id,
                                         unit_content_hash="h", partition=partition, order_key=list(order),
                                         input_hash="i", status="done", attempts=1,
                                         available_at=datetime.now(timezone.utc), cache_key=key))


def q(**fields):
    return {f: to_literal(v) for f, v in fields.items()}


def add_vectors(c, run_id, mention_id, vectors, deployment):
    """A further document-vector set for an already seeded mention (another embed checkpoint)."""
    for f, vec in vectors.items():
        c.execute(sa.insert(member_vectors).values(
            id=f"v-{mention_id}-{f}-{str(run_id)[:4]}-{deployment}-{len(vec)}", mention_id=mention_id,
            enrichment_id="e-" + mention_id + str(run_id)[:4], field=f, kind="document", deployment=deployment,
            dimension=len(vec), vector=vec))


EMPTY_FIELD = {"text": None, "spans": [], "confidence": None}
THRESHOLD_SETTINGS = {"k": 5, "weights": {"action": 1.0, "participants": 1.0, "location": 1.0, "time": 1.0},
                      "threshold": 0.9}


def make_pipeline_run(c, settings=None, adapter="threshold", deployments=None) -> uuid.UUID:
    """A running run whose embed stage used deployment 'stub-emb' at dimension 3."""
    rid = uuid.uuid4()
    cfg = {"name": "t", "track": "realistic", "seed": 1, "stages": {
        "embed": {"adapter": "embedding-gemma", "version": "1", "settings": {"dimension": 3},
                  "deployments": {"embeddings": "stub-emb"}},
        "cluster": {"adapter": adapter, "version": "1", "settings": settings or THRESHOLD_SETTINGS,
                    "deployments": deployments or {}}}}
    c.execute(sa.insert(runs).values(id=rid, config=cfg, config_hash="h", state="running",
                                     created_at=datetime.now(timezone.utc)))
    return rid


def seed_unit(c, run_id, mention_id, order, vectors, *, doc_id=None, partition="all", texts=None):
    """A mention that finished enrich and embed (query and document vectors alike) with a pending cluster job.

    vectors: field -> 3-d vector. Fields without a vector are empty in the enrichment.
    """
    doc_id = doc_id or "d-" + mention_id
    text = f"Sentence of {mention_id}."
    if c.execute(sa.select(documents.c.id).where(documents.c.id == doc_id)).first() is None:
        c.execute(sa.insert(documents).values(
            id=doc_id, provider="p", external_id=doc_id, language="en", title="t", byline="", outlet="o", url="u",
            published_at=None, text=text, token_offsets=[], sentence_offsets=[[0, len(text)]], content_hash="x"))
    c.execute(sa.insert(mentions).values(id=mention_id, document_id=doc_id, start=0, end=8, tok_start=0, tok_end=1,
                                         sentence=0, text="Sentence", extractor="e", extractor_version="1"))
    enrichment = {f: dict(EMPTY_FIELD) for f in ("action", "participants", "location", "time")}
    for f in vectors:
        enrichment[f] = {"text": (texts or {}).get(f, f"{f} of {mention_id}"), "spans": [[0, 1]], "confidence": 1.0}
    eid = "e-" + mention_id
    c.execute(sa.insert(enrichments).values(id=eid, mention_id=mention_id))
    for f, vec in vectors.items():
        for kind in ("document", "query"):
            c.execute(sa.insert(member_vectors).values(
                id=f"v-{mention_id}-{f}-{kind}", mention_id=mention_id, enrichment_id=eid, field=f, kind=kind,
                deployment="stub-emb", dimension=len(vec), vector=vec))
    key = "k-" + mention_id
    c.execute(sa.insert(stage_outputs).values(
        cache_key=key, stage="enrich", adapter="a", adapter_version="1",
        output={"enrichment": enrichment, "enrichment_id": eid}, emits=[], created_at=datetime.now(timezone.utc)))
    ids = {}
    for stage, status, parent in (("enrich", "done", None), ("embed", "done", "enrich"), ("cluster", "pending", "embed")):
        ids[stage] = c.execute(sa.insert(jobs).values(
            run_id=run_id, stage=stage, unit_kind="mention", unit_id=mention_id, unit_content_hash="h",
            parent_job_id=ids.get(parent), partition=partition, order_key=list(order), input_hash="i", status=status,
            attempts=0, available_at=datetime.now(timezone.utc), cache_key=key if stage == "enrich" else None,
        ).returning(jobs.c.id)).scalar_one()


# -- running the ordered stage ------------------------------------------------


def unit(angle):
    return [math.cos(angle), math.sin(angle), 0.0]


def drain(db, cls=ThresholdClusterer):
    registry = Registry()
    registry.register(cls)
    run_writer(db, registry, Stage.cluster, "w", stop=threading.Event(), once=True)


def setup(db, specs, **kw):
    with db.begin() as c:
        run = make_pipeline_run(c, **kw)
        for i, (mid, vectors) in enumerate(specs, start=1):
            seed_unit(c, run, mid, (i,), vectors)
    return run


def membership(db, run):
    with db.connect() as c:
        return dict(c.execute(sa.select(cluster_members.c.mention_id, cluster_members.c.cluster_id)
                              .where(cluster_members.c.run_id == run)).all())


def decisions(db, run):
    with db.connect() as c:
        return {r.mention_id: r for r in c.execute(sa.select(cluster_decisions).where(cluster_decisions.c.run_id == run))}

