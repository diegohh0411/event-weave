"""Run summary and retrieval recall at K. Evaluation only: recall reads gold, nothing in the pipeline does."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from uuid import UUID

import sqlalchemy as sa
from weave_core.schema import (cluster_decisions, cluster_members, clusters, documents, gold_mentions, gold_pairs, jobs,
                               mentions)
from weave_core.stages import JobStatus, Stage


@dataclass(frozen=True)
class Summary:
    mentions: int  # cluster jobs of the run
    clustered: int
    failed: int
    clusters: int
    singletons: int
    decision_calls: int  # decisions that went through a model deployment
    outcomes: dict[str, int] = field(default_factory=dict)  # reason -> decisions


@dataclass(frozen=True)
class RecallRow:
    language: str
    mentions_with_gold_antecedent: int
    hits: int
    recall: float  # hits / mentions_with_gold_antecedent, 0 when there are none


def summary(engine, run_id: UUID) -> Summary:
    j = jobs.c
    with engine.connect() as conn:
        by_status = dict(conn.execute(sa.select(j.status, sa.func.count()).where(
            j.run_id == run_id, j.stage == Stage.cluster).group_by(j.status)).all())
        sizes = [n for (n,) in conn.execute(sa.select(sa.func.count()).where(cluster_members.c.run_id == run_id)
                                            .group_by(cluster_members.c.cluster_id))]
        decided = conn.execute(sa.select(cluster_decisions.c.reason, cluster_decisions.c.deployment)
                               .where(cluster_decisions.c.run_id == run_id)).all()
    outcomes = Counter(reason or "(none)" for reason, _ in decided)
    return Summary(mentions=sum(by_status.values()), clustered=sum(sizes), failed=by_status.get(JobStatus.failed, 0),
                   clusters=len(sizes), singletons=sum(1 for n in sizes if n == 1),
                   decision_calls=sum(1 for _, deployment in decided if deployment), outcomes=dict(sorted(outcomes.items())))


def _gold_components(conn) -> dict[tuple, int]:
    """(document_id, start, end) -> component id, from the label-1 gold pairs (transitive closure)."""
    spans = {(r.document_id, r.mention_id): (r.document_id, r.start, r.end)
             for r in conn.execute(sa.select(gold_mentions))}
    parent: dict[tuple, tuple] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in conn.execute(sa.select(gold_pairs).where(gold_pairs.c.label == 1)):
        a = spans.get((p.source_document_id, p.source_mention_id))
        b = spans.get((p.target_document_id, p.target_mention_id))
        if a and b:
            parent[find(a)] = find(b)
    roots = {}
    return {x: roots.setdefault(find(x), len(roots)) for x in list(parent)}


def recall_at_k(engine, run_id: UUID, k: int | None = None) -> list[RecallRow]:
    """Per language: of the mentions with an earlier gold-coreferent mention in their partition, the share whose
    recorded top-k candidates include the cluster of at least one of those mentions.

    k below the recorded K re-slices the stored candidates; k above it (or None) uses everything recorded.
    """
    with engine.connect() as conn:
        component = _gold_components(conn)
        rows = conn.execute(
            sa.select(cluster_members.c.mention_id, cluster_members.c.cluster_id, cluster_members.c.partition,
                      cluster_members.c.order_key, mentions.c.document_id, mentions.c.start, mentions.c.end,
                      documents.c.language, cluster_decisions.c.candidates)
            .select_from(cluster_members.join(mentions, mentions.c.id == cluster_members.c.mention_id)
                         .join(documents, documents.c.id == mentions.c.document_id)
                         .outerjoin(cluster_decisions, sa.and_(cluster_decisions.c.run_id == cluster_members.c.run_id,
                                                               cluster_decisions.c.mention_id == cluster_members.c.mention_id)))
            .where(cluster_members.c.run_id == run_id)).all()
    chains = defaultdict(list)  # (partition, component) -> [(order_key, cluster_id)]
    for r in rows:
        comp = component.get((r.document_id, r.start, r.end))
        if comp is not None:
            chains[r.partition, comp].append((tuple(r.order_key), r.cluster_id))
    totals: dict[str, list[int]] = {}
    for r in rows:
        comp = component.get((r.document_id, r.start, r.end))
        if comp is None:
            continue
        earlier = {cid for order, cid in chains[r.partition, comp] if order < tuple(r.order_key)}
        if not earlier:
            continue
        offered = [c["cluster_id"] for c in (r.candidates or [])]
        if k is not None:
            offered = offered[:k]
        entry = totals.setdefault(r.language, [0, 0])
        entry[0] += 1
        entry[1] += bool(earlier & set(offered))
    return [RecallRow(lang, n, hits, hits / n if n else 0.0) for lang, (n, hits) in sorted(totals.items())]
