"""Candidate clusters for a mention: best member per field, combined with renormalised weights."""
from dataclasses import dataclass

import sqlalchemy as sa

_SQL = sa.text("""
    SELECT cm.cluster_id AS cluster_id, max(1 - (mv.vector <=> CAST(:q AS vector))) AS score
    FROM cluster_members cm
    JOIN member_vectors mv ON mv.mention_id = cm.mention_id AND mv.field = :f AND mv.kind = 'document'
      AND mv.deployment = :dep AND mv.dimension = :dim
    WHERE cm.run_id = :run AND cm.partition = :p
    GROUP BY cm.cluster_id""")


_FIRST = sa.text("""
    SELECT cluster_id, (array_agg(mention_id ORDER BY order_key, mention_id))[1] AS first_mention
    FROM cluster_members WHERE run_id = :run AND partition = :p AND cluster_id = ANY(:ids)
    GROUP BY cluster_id""")


@dataclass(frozen=True)
class Candidate:
    cluster_id: str
    score: float
    field_scores: dict[str, float]


def retrieve(session, run_id, partition: str, query: dict[str, str], weights: dict[str, float], k: int,
             *, deployment: str, dimension: int) -> list[Candidate]:
    per_field: dict[str, dict[str, float]] = {}
    for f, literal in query.items():
        rows = session.execute(_SQL, {"q": literal, "f": f, "run": run_id, "p": partition,
                                   "dep": deployment, "dim": dimension}).all()
        per_field[f] = {r.cluster_id: float(r.score) for r in rows}
    clusters = {c for scores in per_field.values() for c in scores}
    # Tie-break on the cluster's earliest member: mention ids are content-addressed, cluster ids are run-scoped.
    first = {r.cluster_id: r.first_mention for r in session.execute(
        _FIRST, {"run": run_id, "p": partition, "ids": sorted(clusters)})}
    out = []
    for c in clusters:
        fs = {f: scores[c] for f, scores in per_field.items() if c in scores}
        total = sum(weights.get(f, 0.0) for f in fs)
        score = sum(weights.get(f, 0.0) * s for f, s in fs.items()) / total if total > 0 else 0.0
        out.append(Candidate(c, score, fs))
    out.sort(key=lambda x: (-x.score, first[x.cluster_id], x.cluster_id))
    return out[:k]
