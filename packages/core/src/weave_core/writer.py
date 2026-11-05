"""Ordered, transactional stage execution under a session advisory lock."""
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .adapters import AdapterFailure
from .db import session_scope
from .queue import ClaimedJob, Queue, _claimed
from .schema import jobs, runs
from .stages import ORDERED_STAGES, PIPELINE, JobStatus, RunState, Stage
from .worker import build_adapter


@dataclass(frozen=True)
class Blocked:
    """The head cannot run yet; later jobs must not overtake it."""


def partition_lock_key(run_id: UUID, stage: Stage, partition: str) -> int:
    return int.from_bytes(sha256(f"{run_id}:{stage}:{partition}".encode()).digest()[:8],
                          "big", signed=True)


def next_job(session, run_id, stage, partition) -> ClaimedJob | Blocked | None:
    head = session.execute(sa.select(jobs).where(
        jobs.c.run_id == run_id, jobs.c.stage == stage, jobs.c.partition == partition,
        jobs.c.status.in_([JobStatus.pending, JobStatus.claimed])
    ).order_by(jobs.c.order_key, jobs.c.id).limit(1)).first()
    if head is None:
        return None
    # Choose the head BEFORE considering availability or locks. SKIP LOCKED on
    # an ordered candidate query would let a retry/active job be overtaken.
    row = session.execute(sa.select(jobs, (jobs.c.available_at <= sa.func.now()).label('ready'))
                          .where(jobs.c.id == head.id)
                          .with_for_update(skip_locked=True)).first()
    if row is None or row.status != JobStatus.pending or not row.ready:
        return Blocked()
    earlier = PIPELINE[:PIPELINE.index(stage)]
    blocker = session.execute(sa.select(jobs.c.id).where(
        jobs.c.run_id == run_id, jobs.c.stage.in_(earlier),
        jobs.c.partition.in_([partition, '*']),
        jobs.c.status.in_([JobStatus.pending, JobStatus.claimed]),
        jobs.c.order_key <= row.order_key,
    ).limit(1)).first()
    if blocker is not None:
        return Blocked()
    # Admit this locked job against cancellation using a short, shared run
    # lock. Concurrent partitions can both be admitted. PostgreSQL releases
    # locks acquired after a savepoint when it is rolled back; the outer job
    # lock remains held. A later cancellation may let this admitted job finish,
    # but prevents its successor from being admitted.
    with session.begin_nested() as admission:
        state = session.execute(sa.select(runs.c.state).where(runs.c.id == run_id)
                                .with_for_update(read=True, skip_locked=True)).scalar_one_or_none()
        admission.rollback()
    if state != RunState.running:
        return Blocked()
    claimed = session.execute(jobs.update().where(jobs.c.id == row.id).values(
        status=JobStatus.claimed, claim_token=uuid4(), heartbeat_at=sa.func.now()
    ).returning(jobs)).one()
    return _claimed(claimed)


def run_writer(engine, registry, stage: Stage, worker_id: str, *, threads: int = 4,
               poll_interval: float = 1.0, stop: threading.Event, once: bool = False) -> None:
    if stage not in ORDERED_STAGES:
        raise ValueError("writers require an ordered stage")
    if threads < 1:
        raise ValueError("threads must be positive")
    queue = Queue(engine)

    def write_partition(run_id, partition):
        count = 0
        key = partition_lock_key(run_id, stage, partition)
        # Never return a session-locked connection to the pool. A Session bound
        # to this connection keeps adapter writes and completion on this backend.
        with engine.connect() as connection:
            acquired = False
            try:
                acquired = connection.scalar(sa.select(sa.func.pg_try_advisory_lock(key)))
                connection.commit()
                if not acquired:
                    return 0
                with Session(bind=connection) as session:
                    adapter = None
                    while not stop.is_set():
                        with session.begin():
                            job = next_job(session, run_id, stage, partition)
                            if job is None or isinstance(job, Blocked):
                                break
                            session.execute(jobs.update().where(jobs.c.id == job.id)
                                            .values(claimed_by=worker_id))
                            try:
                                # The savepoint rolls adapter side effects back on
                                # failure, while retaining the claim to record retry.
                                with session.begin_nested():
                                    if adapter is None:
                                        adapter, _ = build_adapter(session, run_id, stage, registry)
                                    upstream = queue.upstream_in_session(session, job)
                                    result = adapter.process(job.unit, upstream, partition, session)
                                    if not queue.complete_in_session(session, job, result, None,
                                                                     spec=adapter.spec):
                                        raise RuntimeError("writer lost its claim")
                            except Exception as exc:
                                failure = exc if isinstance(exc, AdapterFailure) else AdapterFailure(
                                    f"{type(exc).__name__}: {exc}")
                                queue.fail_in_session(session, job, failure)
                            count += 1
                return count
            finally:
                if acquired:
                    try:
                        connection.rollback()
                        connection.execute(sa.select(sa.func.pg_advisory_unlock(key)))
                        connection.commit()
                    except BaseException:
                        # Closing a physical backend also releases session locks.
                        connection.invalidate()
                        raise

    with ThreadPoolExecutor(max_workers=threads) as pool:
        while not stop.is_set():
            with session_scope(engine) as session:
                candidates = session.execute(sa.select(jobs.c.run_id, jobs.c.partition)
                    .join(runs, runs.c.id == jobs.c.run_id)
                    .where(jobs.c.stage == stage, jobs.c.status == JobStatus.pending,
                           runs.c.state == RunState.running)
                    .distinct().order_by(jobs.c.run_id, jobs.c.partition)).all()
            futures = [pool.submit(write_partition, run_id, partition)
                       for run_id, partition in candidates]
            progressed = sum(future.result() for future in futures)
            if not progressed:
                if once:
                    return
                stop.wait(poll_interval)
