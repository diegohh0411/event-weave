import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa

from weave_core.config import RunConfig, config_hash, validate
from weave_core.db import session_scope
from weave_core.schema import jobs, runs
from weave_core.stages import JobStatus, RunState, Stage, UnitKind


class CostEstimator(Protocol):
    def estimate(self, cfg: RunConfig) -> Decimal | None: ...


class NoEstimate:
    def estimate(self, cfg: RunConfig) -> Decimal | None:
        return None


@dataclass
class RunCreated:
    run_id: UUID
    config_hash: str
    estimate: Decimal | None


@dataclass
class RunStatus:
    state: RunState
    stages: dict[Stage, dict[JobStatus, int]]


class RunStateError(Exception):
    pass


def mark_done_if_finished(session, run_id: UUID) -> bool:
    active = session.execute(sa.select(jobs.c.id).where(jobs.c.run_id == run_id, jobs.c.status.in_([JobStatus.pending, JobStatus.claimed])).limit(1)).first()
    if active:
        return False
    result = session.execute(runs.update().where(runs.c.id == run_id).values(state=RunState.done, finished_at=datetime.now(timezone.utc)))
    return result.rowcount == 1


class RunService:
    def __init__(self, engine, registry, catalog, estimator: CostEstimator = NoEstimate()):
        self.engine, self.registry, self.catalog, self.estimator = engine, registry, catalog, estimator

    def create(self, cfg: RunConfig) -> RunCreated:
        cfg = validate(cfg, self.registry, self.catalog)
        digest = config_hash(cfg)
        estimate = self.estimator.estimate(cfg)
        run_id = uuid4()
        with session_scope(self.engine) as session:
            session.execute(runs.insert().values(id=run_id, config=cfg.model_dump(mode='json'), config_hash=digest, state=RunState.created, created_at=datetime.now(timezone.utc)))
        return RunCreated(run_id, digest, estimate)

    def _transition(self, session, run_id, old, new, **values):
        row = session.execute(runs.update().where(runs.c.id == run_id, runs.c.state == old).values(state=new, **values).returning(runs.c.config_hash)).first()
        if row is None:
            raise RunStateError(f'Cannot transition run {run_id} from {old} to {new}')
        return row.config_hash

    def start(self, run_id):
        now = datetime.now(timezone.utc)
        with session_scope(self.engine) as session:
            self._transition(session, run_id, RunState.created, RunState.running, started_at=now)
            config = session.execute(sa.select(runs.c.config).where(runs.c.id == run_id)).scalar_one()
            provider = RunConfig.model_validate(config).stages[Stage.provide]
            digest = hashlib.sha256(json.dumps(provider.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            session.execute(jobs.insert().values(run_id=run_id, stage=Stage.provide, unit_kind=UnitKind.run, unit_id=str(run_id), unit_content_hash=digest, partition='*', order_key=[], input_hash=digest, status=JobStatus.pending, available_at=now))

    def cancel(self, run_id):
        with session_scope(self.engine) as session:
            self._transition(session, run_id, RunState.running, RunState.cancelled)

    def resume(self, run_id):
        with session_scope(self.engine) as session:
            self._transition(session, run_id, RunState.cancelled, RunState.running)

    def status(self, run_id) -> RunStatus:
        with session_scope(self.engine) as session:
            state = session.execute(sa.select(runs.c.state).where(runs.c.id == run_id)).scalar_one()
            rows = session.execute(sa.select(jobs.c.stage, jobs.c.status, sa.func.count()).where(jobs.c.run_id == run_id).group_by(jobs.c.stage, jobs.c.status))
            stages = {}
            for stage, status, count in rows:
                stages.setdefault(Stage(stage), {})[JobStatus(status)] = count
            return RunStatus(RunState(state), stages)
