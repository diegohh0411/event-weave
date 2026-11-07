"""System output: the mentions a run extracted and the cluster each one joined."""
from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from weave_core.schema import cluster_members, jobs, mentions
from weave_core.stages import UnitKind


@dataclass(frozen=True)
class SystemMention:
    mention_id: str
    document_id: str
    start: int
    end: int
    tok_start: int
    tok_end: int  # end exclusive
    cluster_id: str | None  # None: no membership (a failed cluster job)


def load_system(conn, run_id, document_ids, mention_stage) -> list[SystemMention]:
    """The run's own mentions: those with a `mention_stage` job (the stage after extract) in this run.

    Mention ids are content-addressed and `mentions` has no run_id, so other runs' mentions on the same documents
    are excluded through jobs. A mention whose cluster job failed stays in (cluster_id None): it is a prediction."""
    ids = list(document_ids)
    if mention_stage is None or not ids:
        return []
    stage = getattr(mention_stage, "value", mention_stage)
    q = (sa.select(mentions.c.id, mentions.c.document_id, mentions.c.start, mentions.c.end,
                   mentions.c.tok_start, mentions.c.tok_end, cluster_members.c.cluster_id)
         .select_from(mentions
                      .join(jobs, sa.and_(jobs.c.run_id == run_id, jobs.c.unit_kind == UnitKind.mention.value,
                                          jobs.c.stage == stage, jobs.c.unit_id == mentions.c.id))
                      .outerjoin(cluster_members, sa.and_(cluster_members.c.mention_id == mentions.c.id,
                                                          cluster_members.c.run_id == run_id)))
         .where(mentions.c.document_id.in_(ids)).order_by(mentions.c.id))
    return [SystemMention(r.id, r.document_id, r.start, r.end, r.tok_start, r.tok_end, r.cluster_id)
            for r in conn.execute(q)]
