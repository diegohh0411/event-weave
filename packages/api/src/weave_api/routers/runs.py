from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from weave_api.deps import get_service
from weave_api.paging import Page, Paging, paginate
from weave_api.queries import runs as q
from weave_core.config import RunConfig, config_hash, validate
from weave_core.runs import Estimate, RunService
from weave_core.stages import RunState, Stage, Track, UnitKind
from weave_inference.estimate import cache_coverage

router = APIRouter()


class Coverage(BaseModel):
    cached: int
    total: int


class EstimateOut(BaseModel):
    amount: Decimal | None
    reason: str | None
    cached: dict[Stage, Coverage] | None


class ValidatedOut(BaseModel):
    config: RunConfig
    config_hash: str


class CreatedOut(BaseModel):
    run_id: UUID
    config_hash: str
    estimate: EstimateOut


class RunSummary(BaseModel):
    id: UUID
    name: str
    track: Track
    state: RunState
    config_hash: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class Progress(BaseModel):
    queued: int
    running: int
    done: int
    failed: int


class RunOut(RunSummary):
    config: RunConfig
    progress: dict[Stage, Progress]
    dollars: Decimal
    dollars_estimated: bool
    mentions_per_hour: float | None


class FailureOut(BaseModel):
    stage: Stage
    unit_kind: UnitKind
    unit_id: str
    error: str | None
    attempts: int


Service = Annotated[RunService, Depends(get_service)]


def _estimate_out(service: RunService, cfg: RunConfig, estimate: Estimate) -> EstimateOut:
    coverage = cache_coverage(service.engine, service.registry, cfg)
    cached = None if coverage is None else {stage: Coverage(cached=c, total=t) for stage, (c, t) in coverage.items()}
    return EstimateOut(amount=estimate.amount, reason=estimate.reason, cached=cached)


def _summary(row) -> RunSummary:
    return RunSummary(id=row.id, name=row.name, track=row.track, state=row.state, config_hash=row.config_hash,
                      created_at=row.created_at, started_at=row.started_at, finished_at=row.finished_at)


def _detail(service: RunService, run_id: UUID) -> RunOut:
    with service.engine.connect() as conn:
        row = q.run_row(conn, run_id)
        spent, estimated = q.dollars(conn, run_id)
        return RunOut(**_summary(row).model_dump(), config=RunConfig.model_validate(row.config),
                      progress=q.progress(conn, run_id), dollars=spent, dollars_estimated=estimated,
                      mentions_per_hour=q.mentions_per_hour(conn, run_id, row.config, row.started_at, row.finished_at))


@router.post("/runs/validate")
def validate_run(cfg: RunConfig, service: Service) -> ValidatedOut:
    normalized = validate(cfg, service.registry, service.catalog)
    return ValidatedOut(config=normalized, config_hash=config_hash(normalized))


@router.post("/runs/estimate")
def estimate_run(cfg: RunConfig, service: Service) -> EstimateOut:
    normalized = validate(cfg, service.registry, service.catalog)
    return _estimate_out(service, normalized, service.estimator.estimate(normalized))


@router.post("/runs", status_code=201)
def create_run(cfg: RunConfig, service: Service) -> CreatedOut:
    normalized = validate(cfg, service.registry, service.catalog)
    created = service.create(cfg)
    return CreatedOut(run_id=created.run_id, config_hash=created.config_hash,
                      estimate=_estimate_out(service, normalized, created.estimate))


@router.get("/runs")
def list_runs(paging: Annotated[Paging, Depends()], service: Service) -> Page[RunSummary]:
    with service.engine.connect() as conn:
        return paginate(conn, q.summaries(), paging, _summary)


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, service: Service) -> RunOut:
    return _detail(service, run_id)


@router.post("/runs/{run_id}/start")
def start_run(run_id: UUID, service: Service) -> RunOut:
    service.start(run_id)
    return _detail(service, run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: UUID, service: Service) -> RunOut:
    service.cancel(run_id)
    return _detail(service, run_id)


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: UUID, service: Service) -> RunOut:
    service.resume(run_id)
    return _detail(service, run_id)


@router.get("/runs/{run_id}/failures")
def run_failures(run_id: UUID, paging: Annotated[Paging, Depends()], service: Service, stage: Stage | None = None) -> Page[FailureOut]:
    with service.engine.connect() as conn:
        q.run_row(conn, run_id)
        return paginate(conn, q.failures(run_id, stage), paging,
                        lambda r: FailureOut(stage=r.stage, unit_kind=r.unit_kind, unit_id=r.unit_id, error=r.error, attempts=r.attempts))
