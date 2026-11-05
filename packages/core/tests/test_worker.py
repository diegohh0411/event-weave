import threading
from datetime import timedelta
import pytest
import weave_core.queue
from weave_core.stages import Stage, JobStatus, Track
from weave_core.worker import run_worker
from fakes import SPECS
FakeExtractor, FakeEnricher, FakeEmbedder = SPECS[1], SPECS[3], SPECS[4]
@pytest.fixture
def engine(service):
    return service.engine
@pytest.fixture(autouse=True)
def realistic_config(good_config):
    good_config.track = Track.realistic
def start(service, cfg):
    run = service.create(cfg).run_id
    service.start(run)
    return run
def drain(engine, registry, stages):
    for _ in range(10):
        for st in stages:
            run_worker(engine, registry, st, "w", stop=threading.Event(), once=True)

PARALLEL = [Stage.provide, Stage.extract, Stage.tag, Stage.enrich, Stage.embed]

def test_pipeline_reaches_cluster_stage(engine, registry, service, good_config):
    run = start(service, good_config)
    drain(engine, registry, PARALLEL)
    s = service.status(run).stages
    assert s[Stage.embed] == {JobStatus.done: 6}
    assert s[Stage.cluster] == {JobStatus.pending: 6}

def test_failing_adapter_does_not_stop_run(engine, registry, service, good_config, monkeypatch):
    monkeypatch.setattr(weave_core.queue, "BACKOFF_BASE", timedelta(0))
    good_config.stages[Stage.enrich].settings = {"fail_on": ["fake:d0#0"]}
    run = start(service, good_config)
    drain(engine, registry, PARALLEL)
    s = service.status(run).stages
    assert s[Stage.enrich] == {JobStatus.done: 5, JobStatus.failed: 1}
    assert s[Stage.cluster] == {JobStatus.pending: 5}

def test_second_run_reuses_upstream(engine, registry, service, good_config):   # spec acceptance
    start(service, good_config); drain(engine, registry, PARALLEL)
    before = (FakeExtractor.calls, FakeEnricher.calls, FakeEmbedder.calls)
    other = good_config.model_copy(deep=True); other.stages[Stage.cluster].settings = {"k": 9}
    run2 = start(service, other); drain(engine, registry, PARALLEL)
    assert (FakeExtractor.calls, FakeEnricher.calls, FakeEmbedder.calls) == before
    assert service.status(run2).stages[Stage.cluster] == {JobStatus.pending: 6}

def test_duplicate_document_kept_once(engine, registry, service, good_config, count_rows):   # Review Focus 5
    good_config.stages[Stage.provide].settings = {"docs": 2, "duplicate_first": True}
    run = start(service, good_config); drain(engine, registry, [Stage.provide])
    assert count_rows("documents") == 2 and service.status(run).stages[Stage.extract] == {JobStatus.pending: 2}

def test_paper_track_needs_topic(engine, registry, service, good_config, job_row):        # Review Focus 5
    good_config.stages[Stage.provide].settings = {"docs": 1, "no_topic": True}
    run = start(service, good_config.model_copy(update={"track": Track.paper}))
    drain(engine, registry, [Stage.provide])
    assert job_row(run, Stage.provide).error == "paper track needs a topic for document 'fake:d0'"


@pytest.mark.parametrize("failures", [1, 3])
def test_heartbeat_failure_recovers_or_stops_claiming(
    engine, registry, service, good_config, monkeypatch, caplog, failures
):
    import time
    from weave_core.queue import Queue

    first = start(service, good_config)
    other = good_config.model_copy(deep=True)
    other.stages[Stage.provide].settings = {"docs": 4}
    second = start(service, other)
    processing = threading.Event()
    observed = threading.Event()
    attempts = []
    processed = []
    original_heartbeat = Queue.heartbeat
    provider = SPECS[0]
    original_process = provider.process
    interval = 0.04
    monkeypatch.setattr(weave_core.queue, "HEARTBEAT_EVERY", timedelta(seconds=interval))

    def broken_heartbeat(self, worker_id, now=None):
        assert processing.wait(2)
        attempts.append(time.monotonic())
        if len(attempts) <= failures:
            if len(attempts) == 3:
                observed.set()
            raise RuntimeError("heartbeat database unavailable")
        original_heartbeat(self, worker_id, now)
        observed.set()

    def slow_process(self, unit, upstream):
        processed.append(self.ctx.run_id)
        processing.set()
        # Bound the test even when the original heartbeat thread dies.
        recovered_or_failed = observed.wait(1)
        if failures == 3 and recovered_or_failed:
            time.sleep(interval * 2)  # let the heartbeat publish its terminal state
        return original_process(self, unit, upstream)

    monkeypatch.setattr(Queue, "heartbeat", broken_heartbeat)
    monkeypatch.setattr(provider, "process", slow_process)
    if failures == 3:
        with pytest.raises(RuntimeError, match="heartbeat"):
            run_worker(engine, registry, Stage.provide, "heartbeat-test",
                       threads=1, stop=threading.Event(), once=True)
        assert processed == [first]
        assert service.status(second).stages[Stage.provide] == {JobStatus.pending: 1}
        assert len(attempts) == 3
    else:
        run_worker(engine, registry, Stage.provide, "heartbeat-test",
                   threads=1, stop=threading.Event(), once=True)
        assert processed == [first, second]
        assert len(attempts) >= 2
        assert service.status(second).stages[Stage.provide] == {JobStatus.done: 1}
    assert service.status(first).stages[Stage.provide] == {JobStatus.done: 1}
    assert "heartbeat database unavailable" in caplog.text
    assert all(b - a >= interval * 0.8 for a, b in zip(attempts, attempts[1:]))
