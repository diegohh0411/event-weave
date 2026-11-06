"""Read-side view of one partition's clusters: members joined with their mentions, documents, vectors and enrichments."""
from dataclasses import dataclass, field
from datetime import datetime

import sqlalchemy as sa
from weave_core.schema import cluster_members, documents, jobs, member_vectors, mentions, stage_outputs
from weave_core.stages import Stage


@dataclass(frozen=True)
class Member:
    mention_id: str
    order_key: tuple
    enrichment: dict
    sentence: str
    language: str
    published_at: datetime | None
    vectors: dict[str, list[float]] = field(default_factory=dict)  # document vectors only


class PartitionView:
    def __init__(self, session, run_id, partition: str, *, deployment: str, dimension: int):
        self.session, self.run_id, self.partition = session, run_id, partition
        self.deployment, self.dimension = deployment, dimension

    def cluster_ids(self) -> list[str]:
        rows = self.session.execute(
            sa.select(cluster_members.c.cluster_id).where(cluster_members.c.run_id == self.run_id,
                                                          cluster_members.c.partition == self.partition)
            .distinct().order_by(cluster_members.c.cluster_id)).all()
        return [r[0] for r in rows]

    def members(self, cluster_id: str) -> list[Member]:
        cm = cluster_members
        rows = self.session.execute(
            sa.select(cm.c.mention_id, cm.c.order_key, mentions.c.sentence, documents.c.text,
                      documents.c.sentence_offsets, documents.c.language, documents.c.published_at,
                      stage_outputs.c.output)
            .select_from(cm.join(mentions, mentions.c.id == cm.c.mention_id)
                         .join(documents, documents.c.id == mentions.c.document_id)
                         .outerjoin(jobs, sa.and_(jobs.c.run_id == cm.c.run_id, jobs.c.stage == Stage.enrich.value,
                                                  jobs.c.unit_id == cm.c.mention_id))
                         .outerjoin(stage_outputs, stage_outputs.c.cache_key == jobs.c.cache_key))
            .where(cm.c.run_id == self.run_id, cm.c.partition == self.partition, cm.c.cluster_id == cluster_id)
            .order_by(cm.c.order_key, cm.c.mention_id)).all()
        ids = [r.mention_id for r in rows]
        vectors: dict[str, dict[str, list[float]]] = {i: {} for i in ids}
        if ids:
            for v in self.session.execute(
                    sa.select(member_vectors.c.mention_id, member_vectors.c.field, member_vectors.c.vector)
                    .where(member_vectors.c.mention_id.in_(ids), member_vectors.c.kind == "document",
                           member_vectors.c.deployment == self.deployment, member_vectors.c.dimension == self.dimension)):
                vectors[v.mention_id][v.field] = v.vector
        out = []
        for r in rows:
            offsets = r.sentence_offsets
            start, end = offsets[r.sentence] if r.sentence < len(offsets) else (0, len(r.text))
            enrichment = (r.output or {}).get("enrichment") or {}
            out.append(Member(r.mention_id, tuple(r.order_key or ()), enrichment, r.text[start:end],
                              r.language, r.published_at, vectors[r.mention_id]))
        return out
