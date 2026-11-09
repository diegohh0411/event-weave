from uuid import UUID

import sqlalchemy as sa

from weave_core.schema import cluster_members, clusters, documents, jobs, mentions, runs, stage_outputs
from weave_core.stages import JobStatus, PIPELINE, Stage, UnitKind


def mention_stage(conn, run_id: UUID) -> str | None:
    """The first configured stage after extract (as `next_stage` does); read from the raw config so a partial config works."""
    config = conn.scalar(sa.select(runs.c.config).where(runs.c.id == run_id)) or {}
    configured = config.get("stages") or {}
    after = PIPELINE[PIPELINE.index(Stage.extract) + 1:]
    return next((s.value for s in after if s.value in configured), None)


def _in_run(run_id: UUID):
    return sa.exists().where(jobs.c.run_id == run_id, jobs.c.unit_kind == UnitKind.document.value, jobs.c.unit_id == documents.c.id)


def listing(run_id: UUID, stage: str | None, q: str | None, language: str | None):
    count = (sa.select(sa.func.count()).select_from(mentions.join(jobs, sa.and_(
        jobs.c.run_id == run_id, jobs.c.unit_kind == UnitKind.mention.value, jobs.c.stage == stage, jobs.c.unit_id == mentions.c.id)))
        .where(mentions.c.document_id == documents.c.id).scalar_subquery()) if stage else sa.literal(0)
    query = (sa.select(documents.c.id, documents.c.title, documents.c.outlet, documents.c.language, documents.c.published_at,
                       count.label("mentions"))
             .where(_in_run(run_id)))
    if q:
        query = query.where(documents.c.title.icontains(q, autoescape=True))
    if language:
        query = query.where(documents.c.language == language)
    return query.order_by(documents.c.order_key, documents.c.id)


def document_row(conn, run_id: UUID, document_id: str):
    return conn.execute(sa.select(documents.c.id, documents.c.title, documents.c.byline, documents.c.outlet, documents.c.url,
                                  documents.c.language, documents.c.published_at, documents.c.text)
                        .where(documents.c.id == document_id, _in_run(run_id))).one()


def document_mentions(conn, run_id: UUID, stage: str | None, document_id: str):
    if stage is None:
        return []
    enrich_job = (sa.select(jobs.c.unit_id, jobs.c.cache_key).where(
        jobs.c.run_id == run_id, jobs.c.stage == Stage.enrich.value, jobs.c.unit_kind == UnitKind.mention.value,
        jobs.c.status == JobStatus.done.value).subquery("enrich_job"))
    query = (sa.select(mentions.c.id, mentions.c.start, mentions.c.end, cluster_members.c.cluster_id, clusters.c.size.label("cluster_size"),
                       stage_outputs.c.output["enrichment"].label("enrichment"))
             .select_from(mentions
                          .join(jobs, sa.and_(jobs.c.run_id == run_id, jobs.c.unit_kind == UnitKind.mention.value,
                                              jobs.c.stage == stage, jobs.c.unit_id == mentions.c.id))
                          .outerjoin(cluster_members, sa.and_(cluster_members.c.run_id == run_id, cluster_members.c.mention_id == mentions.c.id))
                          .outerjoin(clusters, sa.and_(clusters.c.run_id == run_id, clusters.c.id == cluster_members.c.cluster_id))
                          .outerjoin(enrich_job, enrich_job.c.unit_id == mentions.c.id)
                          .outerjoin(stage_outputs, stage_outputs.c.cache_key == enrich_job.c.cache_key))
             .where(mentions.c.document_id == document_id).order_by(mentions.c.start, mentions.c.id))
    return conn.execute(query).all()
