"""Cost, speed and failure report for one run, read from the ledger and jobs tables."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

from weave_core.config import RunConfig
from weave_core.pipeline import next_stage
from weave_core.schema import jobs, ledger, runs
from weave_core.stages import JobStatus, Stage, UnitKind

NO_STAGE = "(none)"


@dataclass(frozen=True)
class CostRow:
    stage: str
    deployment: str
    calls: int
    errors: int  # ledger rows whose status is not 'ok'
    input_tokens: int
    output_tokens: int
    dollars: Decimal
    estimated_dollars: Decimal
    p50_ms: float | None
    p95_ms: float | None


@dataclass(frozen=True)
class RunCost:
    rows: list[CostRow]
    total_dollars: Decimal
    any_estimated: bool
    mentions: int
    dollars_per_1000_mentions: Decimal | None
    mentions_per_hour: float | None
    cache_share: float | None


def _rows(engine, run_id) -> list[CostRow]:
    stage = sa.func.coalesce(ledger.c.stage, NO_STAGE)
    lat = ledger.c.latency_ms
    query = sa.select(
        stage.label("stage"), ledger.c.deployment, sa.func.count(),
        sa.func.count().filter(ledger.c.status != "ok"),
        sa.func.coalesce(sa.func.sum(ledger.c.input_tokens), 0), sa.func.coalesce(sa.func.sum(ledger.c.output_tokens), 0),
        sa.func.sum(ledger.c.cost), sa.func.coalesce(sa.func.sum(ledger.c.cost).filter(ledger.c.estimated), Decimal(0)),
        sa.func.percentile_cont(0.5).within_group(lat), sa.func.percentile_cont(0.95).within_group(lat),
    ).where(ledger.c.run_id == run_id).group_by(stage, ledger.c.deployment).order_by(stage, ledger.c.deployment)
    with engine.connect() as c:
        return [CostRow(s, d, int(n), int(e), int(i), int(o), dollars, est,
                        None if p50 is None else float(p50), None if p95 is None else float(p95))
                for s, d, n, e, i, o, dollars, est, p50, p95 in c.execute(query)]


def cost_report(engine, run_id) -> RunCost:
    rows = _rows(engine, run_id)
    total = sum((r.dollars for r in rows), Decimal(0))
    with engine.connect() as c:
        config, started, finished = c.execute(
            sa.select(runs.c.config, runs.c.started_at, runs.c.finished_at).where(runs.c.id == run_id)).one()
        first = next_stage(RunConfig.model_validate(config), Stage.extract)
        mentions = 0 if first is None else c.scalar(
            sa.select(sa.func.count()).where(jobs.c.run_id == run_id, jobs.c.stage == first.value)) or 0
        eligible, hits = c.execute(sa.select(
            sa.func.count(), sa.func.count().filter(jobs.c.cache_hit)
        ).where(jobs.c.run_id == run_id, jobs.c.status == JobStatus.done.value, jobs.c.cache_key.is_not(None),
                jobs.c.stage != Stage.cluster.value)).one()
    hours = (finished - started).total_seconds() / 3600 if started and finished else 0
    return RunCost(
        rows=rows, total_dollars=total, any_estimated=any(r.estimated_dollars for r in rows), mentions=mentions,
        dollars_per_1000_mentions=total * 1000 / mentions if mentions else None,
        mentions_per_hour=mentions / hours if mentions and hours > 0 else None,
        cache_share=hits / eligible if eligible else None)


def failures(engine, run_id) -> dict[str, dict[str, int]]:
    """Failed jobs per stage, split by unit kind; shown beside every score."""
    query = sa.select(jobs.c.stage, jobs.c.unit_kind, sa.func.count()).where(
        jobs.c.run_id == run_id, jobs.c.status == JobStatus.failed.value).group_by(jobs.c.stage, jobs.c.unit_kind)
    out: dict[str, dict[str, int]] = {}
    with engine.connect() as c:
        for stage, kind, n in c.execute(query):
            entry = out.setdefault(stage, {"documents": 0, "mentions": 0})
            if kind == UnitKind.document.value:
                entry["documents"] += n
            elif kind == UnitKind.mention.value:
                entry["mentions"] += n
    return out
