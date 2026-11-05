import threading
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa

from fakes import SPECS
from test_worker import PARALLEL, drain, start
from weave_core.adapters import AdapterFailure
from weave_core.db import session_scope
from weave_core.schema import jobs, runs
from weave_core.stages import Stage, Track, RunState, JobStatus
from weave_core.writer import Blocked, next_job, partition_lock_key, run_writer


@pytest.fixture
def engine(service):
    return service.engine


@pytest.fixture(autouse=True)
def writer_defaults(good_config, engine):
    # Keep the shared config's paper default for earlier suites.
    good_config.track = Track.realistic
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE fake_cluster_log (seq bigserial PRIMARY KEY, run_id uuid NOT NULL, partition text NOT NULL, unit_id text NOT NULL, cluster integer NOT NULL)')


@pytest.fixture
def log(engine):
    def read(run):
        with engine.connect() as conn:
            return list(conn.execute(sa.text('SELECT partition, unit_id FROM fake_cluster_log WHERE run_id=:run ORDER BY seq'), {"run": run}))
    return read


@pytest.fixture
def log_units(log):
    return lambda run: [u for _, u in log(run)]


@pytest.fixture
def clusters(engine):
    def read(run):
        with engine.connect() as conn:
            return list(conn.execute(sa.text('SELECT unit_id, cluster FROM fake_cluster_log WHERE run_id=:run ORDER BY seq'), {"run": run}))
    return read


@pytest.fixture
def hold_advisory_lock(engine):
    @contextmanager
    def hold(key):
        with engine.connect() as conn:
            conn.execute(sa.select(sa.func.pg_advisory_lock(key)))
            conn.commit()
            try:
                yield
            finally:
                conn.execute(sa.select(sa.func.pg_advisory_unlock(key)))
                conn.commit()
    return hold


def run_all(engine, registry):
    drain(engine, registry, PARALLEL)
    run_writer(engine, registry, Stage.cluster, "w", stop=threading.Event(), once=True)


def test_order_is_strict_per_partition(engine, registry, service, good_config, log_units):
    run = start(service, good_config)
    run_all(engine, registry)
    assert log_units(run) == ["fake:d0#0", "fake:d0#1", "fake:d1#0", "fake:d1#1", "fake:d2#0", "fake:d2#1"]


def test_blocked_until_earlier_upstream_done(engine, registry, service, good_config, force_claim, log_units):
    run = start(service, good_config)
    drain(engine, registry, [Stage.provide, Stage.extract, Stage.tag])
    force_claim(run, Stage.enrich, "fake:d1#0")
    drain(engine, registry, PARALLEL)
    run_writer(engine, registry, Stage.cluster, "w", stop=threading.Event(), once=True)
    assert log_units(run) == ["fake:d0#0", "fake:d0#1"]


def test_paper_track_partitions_by_topic(engine, registry, service, good_config, log):
    run = start(service, good_config.model_copy(update={"track": Track.paper}))
    run_all(engine, registry)
    assert {p for p, _ in log(run)} == {"t0", "t1"}
    assert all(u.startswith(("fake:d0", "fake:d2")) for p, u in log(run) if p == "t0")


def test_realistic_track_one_partition(engine, registry, service, good_config, log):
    run = start(service, good_config); run_all(engine, registry)
    assert {p for p, _ in log(run)} == {"all"}


def test_one_writer_per_partition(engine, registry, service, good_config, hold_advisory_lock, log_units):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    with hold_advisory_lock(partition_lock_key(run, Stage.cluster, "all")):
        run_writer(engine, registry, Stage.cluster, "w2", stop=threading.Event(), once=True)
    assert log_units(run) == []


def test_same_config_same_result(engine, registry, service, good_config, log_units, clusters):
    a = start(service, good_config); run_all(engine, registry)
    b = start(service, good_config); run_all(engine, registry)
    assert log_units(a) == log_units(b) and clusters(a) == clusters(b)
    assert service.status(a).state == RunState.done
    assert all(cluster == hash(unit) % 3 for unit, cluster in clusters(a))


def test_lock_key_signed_sha256():
    run = UUID('00000000-0000-0000-0000-000000000001')
    # Independently checked with shasum -a 256: d95fa719ec611ad7...
    assert partition_lock_key(run, Stage.cluster, 'all') == -2783322314909869353


@pytest.mark.parametrize('partition,order', [('all', []), ('*', []), ('all', [0]), ('all', [0, 0])])
def test_pending_upstream_blocks_including_prefixes(engine, registry, service, good_config, log_units, partition, order):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    with engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.run_id == run, jobs.c.stage == Stage.enrich, jobs.c.unit_id == 'fake:d0#0').values(status=JobStatus.pending, partition=partition, order_key=order))
    with session_scope(engine) as session:
        assert isinstance(next_job(session, run, Stage.cluster, 'all'), Blocked)
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert log_units(run) == []


def test_failed_upstream_and_other_partition_do_not_block(engine, registry, service, good_config, log_units):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    with engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.run_id == run, jobs.c.stage == Stage.enrich, jobs.c.unit_id == 'fake:d0#0').values(status=JobStatus.failed))
        conn.execute(jobs.update().where(jobs.c.run_id == run, jobs.c.stage == Stage.tag, jobs.c.unit_id == 'fake:d0#0').values(status=JobStatus.pending, partition='elsewhere'))
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert len(log_units(run)) == 6


def test_retry_rolls_back_output_and_waits_in_place(engine, registry, service, good_config, log_units, monkeypatch):
    cluster = SPECS[-1]
    original = cluster.process
    attempts = []
    def fail(self, unit, upstream, partition, session):
        attempts.append(unit.id)
        result = original(self, unit, upstream, partition, session)
        if unit.id == 'fake:d0#0':
            raise AdapterFailure('try again')
        return result
    monkeypatch.setattr(cluster, 'process', fail)
    run = start(service, good_config); run_all(engine, registry)
    assert attempts == ['fake:d0#0']
    assert log_units(run) == []
    with engine.connect() as conn:
        row = conn.execute(sa.select(jobs).where(jobs.c.run_id == run, jobs.c.stage == Stage.cluster, jobs.c.unit_id == 'fake:d0#0')).one()
        assert row.status == JobStatus.pending and row.attempts == 1 and row.error == 'try again'
    # Retry delay must not allow the second mention to overtake the first.
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert attempts == ['fake:d0#0']
    for _ in range(2):
        with engine.begin() as conn:
            conn.execute(jobs.update().where(jobs.c.run_id == run, jobs.c.stage == Stage.cluster).values(available_at=sa.func.now()))
        run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert attempts[:3] == ['fake:d0#0'] * 3
    assert log_units(run) == ['fake:d0#1', 'fake:d1#0', 'fake:d1#1', 'fake:d2#0', 'fake:d2#1']
    assert service.status(run).state == RunState.done


@pytest.mark.parametrize("mismatch", [{"name": "wrong"}, {"stage": Stage.enrich}])
def test_completion_failure_rolls_back_adapter_writes(engine, registry, service, good_config, log_units, monkeypatch, mismatch):
    # A mismatched executed spec must roll back adapter writes too.
    from dataclasses import replace
    cluster = SPECS[-1]
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    original = cluster.process
    def wrong_spec(self, unit, upstream, partition, session):
        result = original(self, unit, upstream, partition, session)
        self.spec = replace(self.spec, **mismatch)
        return result
    monkeypatch.setattr(cluster, 'process', wrong_spec)
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert log_units(run) == []
    assert service.status(run).stages[Stage.cluster] == {JobStatus.pending: 6}


def test_cancelled_run_is_not_claimed(engine, registry, service, good_config, log_units):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    service.cancel(run)
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert log_units(run) == []
    assert service.status(run).state == RunState.cancelled
    service.resume(run)
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert len(log_units(run)) == 6


def test_two_live_writers_and_lock_cleanup(engine, registry, service, good_config, log_units, monkeypatch):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    entered, release = threading.Event(), threading.Event()
    original = SPECS[-1].process
    def pause(self, unit, upstream, partition, session):
        if unit.id == 'fake:d0#0':
            entered.set()
            assert release.wait(5)
        return original(self, unit, upstream, partition, session)
    monkeypatch.setattr(SPECS[-1], 'process', pause)
    errors = []
    def write():
        try:
            run_writer(engine, registry, Stage.cluster, 'w1', stop=threading.Event(), once=True)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=write)
    thread.start()
    try:
        assert entered.wait(5)
        run_writer(engine, registry, Stage.cluster, 'w2', stop=threading.Event(), once=True)
        assert log_units(run) == []
        assert service.status(run).stages[Stage.cluster] == {JobStatus.pending: 6}
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert len(log_units(run)) == len(set(log_units(run))) == 6
    assert_unlocked(engine, run)


def test_once_returns_for_unavailable_head(engine, registry, service, good_config, log_units):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    with engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.run_id == run, jobs.c.stage == Stage.cluster, jobs.c.unit_id == 'fake:d0#0').values(available_at=sa.func.now() + timedelta(hours=1)))
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert log_units(run) == []


def test_writer_refuses_parallel_stage(engine, registry):
    with pytest.raises(ValueError):
        run_writer(engine, registry, Stage.enrich, 'w', stop=threading.Event(), once=True)



def assert_unlocked(engine, run):
    # Session advisory locks are reentrant: use a new physical backend, never
    # a checkout from the writer's pool, to detect a leak on a returned backend.
    independent = sa.create_engine(engine.url, poolclass=sa.pool.NullPool)
    try:
        with independent.connect() as conn:
            key = partition_lock_key(run, Stage.cluster, 'all')
            assert conn.scalar(sa.select(sa.func.pg_try_advisory_lock(key)))
            assert conn.scalar(sa.select(sa.func.pg_advisory_unlock(key)))
    finally:
        independent.dispose()


def test_fatal_interruption_rolls_back_and_unlocks_before_pool_return(
    engine, registry, service, good_config, log_units, monkeypatch
):
    class Interrupted(BaseException):
        pass
    original = SPECS[-1].process
    def interrupt(self, unit, upstream, partition, session):
        original(self, unit, upstream, partition, session)
        raise Interrupted()
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    monkeypatch.setattr(SPECS[-1], 'process', interrupt)
    with pytest.raises(Interrupted):
        run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert log_units(run) == []
    assert service.status(run).stages[Stage.cluster] == {JobStatus.pending: 6}
    assert_unlocked(engine, run)
    monkeypatch.setattr(SPECS[-1], 'process', original)
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert len(log_units(run)) == 6


def test_database_failure_rolls_back_and_records_retry(
    engine, registry, service, good_config, log_units, monkeypatch
):
    original = SPECS[-1].process
    def bad_sql(self, unit, upstream, partition, session):
        original(self, unit, upstream, partition, session)
        session.execute(sa.text('SELECT 1 / 0'))
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    monkeypatch.setattr(SPECS[-1], 'process', bad_sql)
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert log_units(run) == []
    with engine.connect() as conn:
        row = conn.execute(sa.select(jobs.c.status, jobs.c.attempts, jobs.c.error).where(
            jobs.c.run_id == run, jobs.c.stage == Stage.cluster,
            jobs.c.unit_id == 'fake:d0#0')).one()
        assert row.status == JobStatus.pending and row.attempts == 1
        assert 'division by zero' in row.error
    assert_unlocked(engine, run)


def test_once_returns_when_run_row_is_locked(engine, registry, service, good_config, log_units):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    with engine.connect() as conn:
        conn.execute(sa.select(runs.c.id).where(runs.c.id == run).with_for_update())
        run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
        assert log_units(run) == []
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert len(log_units(run)) == 6


def test_claimed_head_cannot_be_overtaken(engine, registry, service, good_config, log_units, force_claim):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    force_claim(run, Stage.cluster, 'fake:d0#0')
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert log_units(run) == []


def test_single_connection_pool_and_ordered_stage_does_not_cache(
    engine, registry, service, good_config, log_units
):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    small_pool = sa.create_engine(engine.url, pool_size=1, max_overflow=0, pool_timeout=1)
    try:
        run_writer(small_pool, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
        assert len(log_units(run)) == 6
        with small_pool.connect() as conn:
            assert conn.scalar(sa.select(sa.func.count()).select_from(jobs).where(
                jobs.c.run_id == run, jobs.c.stage == Stage.cluster,
                jobs.c.status == JobStatus.done, jobs.c.cache_key.is_(None))) == 6
            assert conn.scalar(sa.text("SELECT count(*) FROM stage_outputs WHERE stage='cluster'")) == 0
        assert service.status(run).state == RunState.done
        assert_unlocked(small_pool, run)
    finally:
        small_pool.dispose()


def test_paper_partitions_process_concurrently(engine, registry, service, good_config, log, monkeypatch):
    run = start(service, good_config.model_copy(update={"track": Track.paper}))
    drain(engine, registry, PARALLEL)
    # Real ordered output tables will reference their run. Keep both adapters'
    # FK KEY SHARE locks live through finalization to catch lock-upgrade cycles.
    with engine.begin() as conn:
        conn.exec_driver_sql('ALTER TABLE fake_cluster_log ADD FOREIGN KEY (run_id) REFERENCES runs(id)')
    entered = {partition: threading.Event() for partition in ('t0', 't1')}
    release = threading.Event()
    original = SPECS[-1].process
    errors = []

    def pause(self, unit, upstream, partition, session):
        result = original(self, unit, upstream, partition, session)
        if not entered[partition].is_set():
            entered[partition].set()
            assert release.wait(10), 'test did not release both partitions'
        return result

    def write():
        try:
            run_writer(engine, registry, Stage.cluster, 'parallel', threads=2,
                       stop=threading.Event(), once=True)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(SPECS[-1], 'process', pause)
    thread = threading.Thread(target=write)
    thread.start()
    try:
        # Neither first adapter may finish until BOTH have entered. This proves
        # overlap through synchronization rather than elapsed processing times.
        assert entered['t0'].wait(3), 't0 did not enter adapter processing'
        assert entered['t1'].wait(3), 't1 was blocked while t0 was processing'
        assert log(run) == []
        assert service.status(run).stages[Stage.cluster] == {JobStatus.pending: 6}
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert [u for p, u in log(run) if p == 't0'] == [
        'fake:d0#0', 'fake:d0#1', 'fake:d2#0', 'fake:d2#1']
    assert [u for p, u in log(run) if p == 't1'] == ['fake:d1#0', 'fake:d1#1']
    assert service.status(run).state == RunState.done


def test_cancel_during_processing_finishes_only_active_job(
    engine, registry, service, good_config, log_units, monkeypatch
):
    run = start(service, good_config); drain(engine, registry, PARALLEL)
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    original = SPECS[-1].process
    errors = []

    def pause(self, unit, upstream, partition, session):
        entered.set()
        assert release.wait(10)
        return original(self, unit, upstream, partition, session)

    def write():
        try:
            run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
        except BaseException as exc:
            errors.append(exc)

    def cancel():
        try:
            service.cancel(run)
            cancelled.set()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(SPECS[-1], 'process', pause)
    writer = threading.Thread(target=write)
    canceller = threading.Thread(target=cancel)
    writer.start()
    try:
        assert entered.wait(3)
        canceller.start()
        assert cancelled.wait(3), 'cancel waited for adapter processing'
        assert service.status(run).state == RunState.cancelled
    finally:
        release.set()
        writer.join(10)
        if canceller.ident is not None:
            canceller.join(10)
    assert not writer.is_alive() and not canceller.is_alive() and not errors
    assert log_units(run) == ['fake:d0#0']
    assert service.status(run).state == RunState.cancelled
    assert service.status(run).stages[Stage.cluster] == {JobStatus.done: 1, JobStatus.pending: 5}
    service.resume(run)
    run_writer(engine, registry, Stage.cluster, 'w', stop=threading.Event(), once=True)
    assert len(log_units(run)) == 6
    assert service.status(run).state == RunState.done


@pytest.mark.parametrize('operation', ['complete', 'fail'])
def test_shared_finalization_waits_for_job_before_locking_run(
    engine, registry, service, good_config, operation
):
    import time
    from weave_core.adapters import StageResult
    from weave_core.queue import Queue

    run = start(service, good_config)
    queue = Queue(engine)
    [job] = queue.claim(Stage.provide, 1, 'parallel-worker')
    ready = threading.Event()
    backend = []
    errors = []
    cancelled = threading.Event()

    def finalize():
        try:
            with session_scope(engine) as session:
                backend.append(session.scalar(sa.select(sa.func.pg_backend_pid())))
                # Bound a broken lock-order implementation's database wait too.
                session.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
                ready.set()
                if operation == 'complete':
                    assert queue.complete_in_session(session, job, StageResult({}, []), None,
                                                     spec=SPECS[0].spec)
                else:
                    queue.fail_in_session(session, job, AdapterFailure('terminal', retryable=False))
        except BaseException as exc:
            errors.append(exc)

    def cancel():
        try:
            service.cancel(run)
            cancelled.set()
        except BaseException as exc:
            errors.append(exc)

    finalizer = threading.Thread(target=finalize)
    canceller = threading.Thread(target=cancel)
    with engine.connect() as held:
        held.execute(sa.select(jobs.c.id).where(jobs.c.id == job.id).with_for_update())
        holder_pid = held.scalar(sa.select(sa.func.pg_backend_pid()))
        finalizer.start()
        try:
            assert ready.wait(3)
            deadline = time.monotonic() + 3
            with engine.connect() as observer:
                while time.monotonic() < deadline:
                    blocking = observer.scalar(sa.select(sa.func.pg_blocking_pids(backend[0])))
                    if holder_pid in blocking:
                        break
                    threading.Event().wait(0.01)
                else:
                    pytest.fail('finalizer never waited on the held job row')
            # The finalizer is demonstrably blocked on the job. It must not
            # already hold the run row and block lifecycle operations.
            canceller.start()
            assert cancelled.wait(2), 'finalizer locked run before waiting for job'
        finally:
            held.rollback()
            finalizer.join(10)
            if canceller.ident is not None:
                canceller.join(10)
    assert not finalizer.is_alive() and not canceller.is_alive() and not errors
    assert service.status(run).state == RunState.done
