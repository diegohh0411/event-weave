from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa

from weave_core.config import RunConfig
from weave_core.pipeline import next_stage
from weave_core.schema import documents, jobs, ledger, run_documents, runs
from weave_core.stages import JobStatus, Stage

# Labels shown to users for the stored job statuses.
LABELS = {JobStatus.pending: "queued", JobStatus.claimed: "running", JobStatus.done: "done", JobStatus.failed: "failed"}

SUMMARY_COLUMNS = (runs.c.id, runs.c.config["name"].astext.label("name"), runs.c.config["track"].astext.label("track"),
                   runs.c.state, runs.c.config_hash, runs.c.created_at, runs.c.started_at, runs.c.finished_at)


def summaries():
    return sa.select(*SUMMARY_COLUMNS).order_by(runs.c.created_at.desc(), runs.c.id)


def run_row(conn, run_id: UUID):
    return conn.execute(sa.select(*SUMMARY_COLUMNS, runs.c.config, runs.c.snapshot_at, runs.c.source_run_id, runs.c.rerun_mode).where(runs.c.id == run_id)).one()


def progress(conn, run_id: UUID) -> dict[Stage, dict[str, int]]:
    out: dict[Stage, dict[str, int]] = {}
    for stage, status, count in conn.execute(
            sa.select(jobs.c.stage, jobs.c.status, sa.func.count()).where(jobs.c.run_id == run_id).group_by(jobs.c.stage, jobs.c.status)):
        out.setdefault(Stage(stage), {label: 0 for label in LABELS.values()})[LABELS[JobStatus(status)]] = count
    return out


def dollars(conn, run_id: UUID) -> tuple[Decimal, bool]:
    total, estimated = conn.execute(sa.select(sa.func.coalesce(sa.func.sum(ledger.c.cost), 0), sa.func.coalesce(sa.func.bool_or(ledger.c.estimated), False))
                                    .where(ledger.c.run_id == run_id)).one()
    return Decimal(total), bool(estimated)


def mentions_per_hour(conn, run_id: UUID, config: dict, started_at: datetime | None, finished_at: datetime | None) -> float | None:
    """Done jobs of the first stage after extract, per hour since the run started (until it finished, if it has)."""
    stage = next_stage(RunConfig.model_validate(config), Stage.extract)
    if stage is None or started_at is None:
        return None
    hours = ((finished_at or datetime.now(timezone.utc)) - started_at).total_seconds() / 3600
    if hours <= 0:
        return None
    done = conn.execute(sa.select(sa.func.count()).where(jobs.c.run_id == run_id, jobs.c.stage == stage,
                                                         jobs.c.status == JobStatus.done)).scalar_one()
    return done / hours


def failures(run_id: UUID, stage: Stage | None):
    query = sa.select(jobs.c.stage, jobs.c.unit_kind, jobs.c.unit_id, jobs.c.error, jobs.c.attempts).where(
        jobs.c.run_id == run_id, jobs.c.status == JobStatus.failed)
    if stage is not None:
        query = query.where(jobs.c.stage == stage)
    return query.order_by(jobs.c.id)


def snapshot_count(conn, run_id: UUID) -> int:
    return conn.scalar(sa.select(sa.func.count()).select_from(run_documents).where(run_documents.c.run_id == run_id))


def snapshot_diff(conn, a: UUID, b: UUID) -> dict[str, int]:
    def articles(run_id):
        return dict(conn.execute(sa.select(documents.c.article_id, documents.c.id)
            .select_from(run_documents.join(documents, run_documents.c.document_id == documents.c.id))
            .where(run_documents.c.run_id == run_id)).all())
    left, right = articles(a), articles(b)
    return {"added": len(right.keys() - left.keys()), "removed": len(left.keys() - right.keys()),
            "new_versions": sum(left[k] != right[k] for k in left.keys() & right.keys())}
