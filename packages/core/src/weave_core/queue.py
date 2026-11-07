from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from .adapters import AdapterFailure, AdapterSpec, StageResult, UnitRef
from .config import RunConfig
from .db import session_scope
from .pipeline import child_partition, child_units, next_stage
from .runs import mark_done_if_finished
from .schema import documents, jobs, metadata, runs, stage_outputs
from .stages import JobStatus, RunState, Stage, UnitKind

MAX_ATTEMPTS = 3
BACKOFF_BASE = timedelta(seconds=2)
STALE_AFTER = timedelta(seconds=60)
HEARTBEAT_EVERY = timedelta(seconds=10)


@dataclass
class ClaimedJob:
    id: int
    run_id: UUID
    stage: Stage
    unit: UnitRef
    partition: str
    order_key: tuple[int, ...]
    input_hash: str
    attempts: int
    claim_token: UUID
    parent_job_id: int | None


def _claimed(row) -> ClaimedJob:
    order = tuple(row.order_key)
    return ClaimedJob(row.id, row.run_id, Stage(row.stage),
                      UnitRef(UnitKind(row.unit_kind), row.unit_id, row.unit_content_hash, order),
                      row.partition, order, row.input_hash, row.attempts, row.claim_token, row.parent_job_id)


def _owns(job):
    return sa.and_(jobs.c.id == job.id, jobs.c.run_id == job.run_id,
                   jobs.c.status == JobStatus.claimed, jobs.c.claim_token == job.claim_token)


STAGE_ROW_TABLES = frozenset({"mentions", "mention_tags", "enrichments",
                              "member_vectors", "clusters", "cluster_members", "cluster_decisions"})
UPSERT_TABLES = frozenset({"clusters"})


class Queue:
    def __init__(self, engine):
        self.engine = engine

    def claim(self, stage: Stage, limit: int, worker_id: str, now: datetime | None = None) -> list[ClaimedJob]:
        clock = now if now is not None else sa.func.now()
        with session_scope(self.engine) as session:
            rows = session.execute(
                sa.select(jobs).join(runs, jobs.c.run_id == runs.c.id)
                .where(jobs.c.stage == stage, jobs.c.status == JobStatus.pending,
                       runs.c.state == RunState.running, jobs.c.available_at <= clock)
                .order_by(jobs.c.available_at, jobs.c.id).limit(limit)
                .with_for_update(skip_locked=True, of=jobs)
            ).all()
            claimed = []
            for row in rows:
                updated = session.execute(jobs.update().where(jobs.c.id == row.id).values(
                    status=JobStatus.claimed, claimed_by=worker_id, claim_token=uuid4(), heartbeat_at=clock
                ).returning(jobs)).one()
                claimed.append(_claimed(updated))
            return claimed

    def complete(self, job: ClaimedJob, result: StageResult, cache_key: str | None, *, spec: AdapterSpec,
                 cache_hit: bool = False) -> bool:
        if spec.stage != job.stage:
            raise ValueError(f"spec stage {spec.stage} does not match job stage {job.stage}")
        with session_scope(self.engine) as session:
            return self.complete_in_session(session, job, result, cache_key, spec=spec, cache_hit=cache_hit)

    def complete_in_session(self, session, job: ClaimedJob, result: StageResult,
                            cache_key: str | None, *, spec: AdapterSpec,
                            cache_hit: bool = False) -> bool:
        """Complete within the caller's transaction; never commit this session."""
        if spec.stage != job.stage:
            raise ValueError(f"spec stage {spec.stage} does not match job stage {job.stage}")
        # All finalizers lock job BEFORE run, including writers that already
        # hold their job during processing. Never hold the run while waiting
        # for a job: that would invert the writer's order and risk a deadlock.
        owned = session.execute(sa.select(jobs.c.id).where(_owns(job)).with_for_update()).first()
        if owned is None:
            return False
        # Serialize terminal transitions through commit. The active-job check
        # below uses a fresh READ COMMITTED snapshot after any run-lock wait,
        # so concurrent last jobs cannot both leave the run unfinished.
        # NO KEY UPDATE also permits FK KEY SHARE locks held by concurrent
        # adapter writes/claims. FOR UPDATE would deadlock on their upgrades.
        config = session.execute(sa.select(runs.c.config).where(runs.c.id == job.run_id)
                                 .with_for_update(key_share=True)).scalar_one()
        cfg = RunConfig.model_validate(config)
        if spec.name != cfg.stages[job.stage].adapter:
            raise ValueError(f"spec adapter {spec.name} does not match configured adapter {cfg.stages[job.stage].adapter}")
        for name in result.rows:
            if not (name.startswith("gold_") or name in STAGE_ROW_TABLES) or name not in metadata.tables:
                raise ValueError(f"stage rows may only target gold_ tables or {sorted(STAGE_ROW_TABLES)}, got {name!r}")
        for document in result.documents:
            values = asdict(document) | {"order_key": list(document.order_key)}
            session.execute(insert(documents).values(id=document.id, **values).on_conflict_do_nothing())
        order = {t.name: i for i, t in enumerate(metadata.sorted_tables)}  # FK parents before children
        for name, rows in sorted(result.rows.items(), key=lambda item: order[item[0]]):
            if not rows:
                continue
            table = metadata.tables[name]
            statement = insert(table)
            if name in UPSERT_TABLES:
                keys = {c.name for c in table.primary_key.columns}
                statement = statement.on_conflict_do_update(
                    index_elements=list(table.primary_key.columns),
                    set_={c.name: statement.excluded[c.name] for c in table.columns if c.name not in keys})
            else:
                statement = statement.on_conflict_do_nothing()
            session.execute(statement, rows)  # executemany: a single multi-VALUES statement would hit the 65,535 bind-parameter cap
        if cache_key is not None:
            session.execute(insert(stage_outputs).values(
                cache_key=cache_key, stage=job.stage, adapter=spec.name, adapter_version=spec.version,
                output=result.output, emits=None if result.emits is None else [asdict(u) for u in result.emits],
                created_at=sa.func.now(),
            ).on_conflict_do_nothing(index_elements=[stage_outputs.c.cache_key]))
        session.execute(jobs.update().where(_owns(job)).values(status=JobStatus.done, cache_key=cache_key, cache_hit=cache_hit))
        following = next_stage(cfg, job.stage)
        if following is not None:
            for unit in child_units(job, result):
                partition = child_partition(cfg.track, job.partition, unit)
                session.execute(insert(jobs).values(
                    run_id=job.run_id, stage=following, unit_kind=unit.kind, unit_id=unit.id,
                    unit_content_hash=unit.content_hash, parent_job_id=job.id, partition=partition,
                    order_key=list(unit.order_key),
                    input_hash=sha256((unit.content_hash + ":" + (cache_key or "")).encode()).hexdigest(),
                    status=JobStatus.pending, available_at=sa.func.now(),
                ).on_conflict_do_nothing(index_elements=[jobs.c.run_id, jobs.c.stage, jobs.c.unit_id]))
        mark_done_if_finished(session, job.run_id)
        return True

    def fail(self, job: ClaimedJob, failure: AdapterFailure) -> None:
        with session_scope(self.engine) as session:
            self.fail_in_session(session, job, failure)

    def fail_in_session(self, session, job: ClaimedJob, failure: AdapterFailure) -> None:
        """Record failure within the caller's transaction, job lock before run."""
        row = session.execute(sa.select(jobs.c.attempts).where(_owns(job)).with_for_update()).first()
        if row is None:
            return
        # Match completion's NO KEY UPDATE lock (compatible with run FKs).
        session.execute(sa.select(runs.c.id).where(runs.c.id == job.run_id)
                        .with_for_update(key_share=True)).first()
        attempts = row.attempts + 1
        retry = failure.retryable and attempts < MAX_ATTEMPTS
        values = dict(attempts=attempts, status=JobStatus.pending if retry else JobStatus.failed,
                      error=failure.reason, claimed_by=None, claim_token=None, heartbeat_at=None)
        if retry:
            values['available_at'] = sa.func.now() + BACKOFF_BASE * 2**(attempts - 1)
        session.execute(jobs.update().where(_owns(job)).values(**values))
        mark_done_if_finished(session, job.run_id)

    def heartbeat(self, worker_id: str, now: datetime | None = None) -> None:
        with session_scope(self.engine) as session:
            session.execute(jobs.update().where(jobs.c.status == JobStatus.claimed, jobs.c.claimed_by == worker_id)
                            .values(heartbeat_at=now if now is not None else sa.func.now()))

    def release_stale(self, now: datetime | None = None) -> int:
        clock = now if now is not None else sa.func.now()
        with session_scope(self.engine) as session:
            # Job rows first, then their runs, like fail_in_session.
            stale = session.execute(
                sa.select(jobs.c.id, jobs.c.run_id, jobs.c.attempts)
                .where(jobs.c.status == JobStatus.claimed, jobs.c.heartbeat_at < clock - STALE_AFTER)
                .order_by(jobs.c.id).with_for_update()).all()
            if not stale:
                return 0
            run_ids = sorted({row.run_id for row in stale})
            session.execute(sa.select(runs.c.id).where(runs.c.id.in_(run_ids))
                            .order_by(runs.c.id).with_for_update(key_share=True)).all()
            lost = [row.id for row in stale if row.attempts + 1 >= MAX_ATTEMPTS]
            retry = [row.id for row in stale if row.attempts + 1 < MAX_ATTEMPTS]
            cleared = dict(claim_token=None, claimed_by=None, heartbeat_at=None, attempts=jobs.c.attempts + 1)
            if retry:
                session.execute(jobs.update().where(jobs.c.id.in_(retry))
                                .values(status=JobStatus.pending, **cleared))
            if lost:
                session.execute(jobs.update().where(jobs.c.id.in_(lost))
                                .values(status=JobStatus.failed, error="worker lost", **cleared))
                for run_id in sorted({row.run_id for row in stale if row.id in lost}):
                    mark_done_if_finished(session, run_id)
            return len(stale)

    def upstream(self, job: ClaimedJob) -> dict[Stage, dict]:
        with session_scope(self.engine) as session:
            return self.upstream_in_session(session, job)

    def upstream_in_session(self, session, job: ClaimedJob) -> dict[Stage, dict]:
        outputs = {}
        parent_id = job.parent_job_id
        while parent_id is not None:
            row = session.execute(sa.select(jobs.c.stage, jobs.c.cache_key, jobs.c.parent_job_id)
                                  .where(jobs.c.id == parent_id, jobs.c.run_id == job.run_id)).first()
            if row is None:
                break
            if row.cache_key is not None:
                output = session.execute(sa.select(stage_outputs.c.output).where(stage_outputs.c.cache_key == row.cache_key)).first()
                if output is not None:
                    outputs[Stage(row.stage)] = output.output
            parent_id = row.parent_job_id
        return outputs
