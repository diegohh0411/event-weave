import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from weave_clusterer.vectors import to_literal
from weave_core.schema import (cluster_members, clusters, documents, enrichments, jobs, member_vectors, mentions, runs,
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
