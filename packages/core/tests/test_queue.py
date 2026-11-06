from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from threading import Barrier

import pytest
import sqlalchemy as sa
from conftest import later, doc_unit
from fakes import SPECS
from weave_core.adapters import AdapterFailure, DocumentRecord, StageResult, UnitRef
from weave_core.schema import jobs, stage_outputs
from weave_core.stages import JobStatus, RunState, Stage, Track, UnitKind


@pytest.fixture
def queue(service):
    from weave_core.queue import Queue
    return Queue(service.engine)


@pytest.fixture
def provider_spec():
    return SPECS[0].spec


def finish(queue, job, result, key="k1", spec=None):
    spec = spec or next(cls.spec for cls in SPECS if cls.spec.stage == job.stage)
    return queue.complete(job, result, key, spec=spec)


def db_clock(service):
    # The queue stamps available_at from Postgres now(), so bound it with the DB clock, not the host's.
    with service.engine.connect() as conn:
        return conn.scalar(sa.text("select clock_timestamp()"))


def record():
    return DocumentRecord("fake", "d1", "en", "title", "", "", "", None, "text", [], [], "hash")


def test_claim_skips_locked_and_cancelled(queue, started_run, service):
    a = queue.claim(Stage.provide, 10, "w1")
    assert len(a) == 1 and queue.claim(Stage.provide, 10, "w2") == []
    queue.fail(a[0], AdapterFailure("boom"))
    service.cancel(started_run)
    assert queue.claim(Stage.provide, 10, "w2", now=later(minutes=1)) == []


def test_retry_then_fail(queue, started_run, job_row, service):
    from weave_core.queue import MAX_ATTEMPTS
    for attempt in range(MAX_ATTEMPTS):
        [job] = queue.claim(Stage.provide, 1, "w", now=later(minutes=attempt * 5))
        before = db_clock(service)
        queue.fail(job, AdapterFailure("flaky"))
        after = db_clock(service)
        row = job_row(started_run, Stage.provide)
        assert row.attempts == attempt + 1
        if attempt < 2:
            assert before + timedelta(seconds=2 * 2**attempt) <= row.available_at <= after + timedelta(seconds=2 * 2**attempt)
            assert queue.claim(Stage.provide, 1, "early", now=before) == []
    assert row.status == JobStatus.failed
    assert row.error == "flaky"
    assert row.attempts == 3


def test_non_retryable_fails_at_once(queue, started_run, job_row, service):
    [job] = queue.claim(Stage.provide, 1, "w")
    queue.fail(job, AdapterFailure("bad input", retryable=False))
    assert job_row(started_run, Stage.provide).status == JobStatus.failed
    assert service.status(started_run).state == RunState.done


def test_complete_enqueues_children_once(queue, started_run, count_jobs, count_rows):
    [job] = queue.claim(Stage.provide, 1, "w")
    result = StageResult({}, [doc_unit("d1", 0, "t1"), doc_unit("d1", 0, "t1")], [record(), record()])
    assert finish(queue, job, result)
    assert count_jobs(started_run, Stage.extract) == 1
    assert count_rows("documents") == 1
    assert not finish(queue, job, result)


def test_lost_claim_writes_nothing(queue, started_run, count_jobs, count_rows, job_row):
    [job] = queue.claim(Stage.provide, 1, "dead-worker")
    assert queue.release_stale(now=later(seconds=61)) == 1
    [again] = queue.claim(Stage.provide, 1, "w2", now=later(seconds=62))
    assert again.claim_token != job.claim_token and again.attempts == 1
    result = StageResult({}, [doc_unit("d1", 0, "t1")], [record()])
    assert not finish(queue, job, result)
    queue.fail(job, AdapterFailure("obsolete", retryable=False))
    assert job_row(started_run, Stage.provide).claim_token == again.claim_token
    assert count_rows("documents") == count_rows("stage_outputs") == 0
    assert finish(queue, again, result)
    assert count_jobs(started_run, Stage.extract) == 1
    assert count_rows("stage_outputs") == 1


def test_heartbeat_keeps_claim(queue, started_run):
    queue.claim(Stage.provide, 1, "w")
    queue.heartbeat("other", now=later(seconds=50))
    queue.heartbeat("w", now=later(seconds=50))
    assert queue.release_stale(now=later(seconds=100)) == 0


def test_run_done_when_nothing_left(queue, service, started_run):
    [job] = queue.claim(Stage.provide, 1, "w")
    finish(queue, job, StageResult({}, []))
    assert service.status(started_run).state == RunState.done


def test_complete_records_executed_metadata(queue, started_run, service, provider_spec):
    [job] = queue.claim(Stage.provide, 1, "w")
    assert finish(queue, job, StageResult({"n": 1}, []), spec=replace(provider_spec, version="executed-2"))
    with service.engine.connect() as conn:
        row = conn.execute(sa.select(stage_outputs)).one()
    assert (row.stage, row.adapter, row.adapter_version, row.output, row.emits) == ("provide", "fake-provider", "executed-2", {"n": 1}, [])


@pytest.mark.parametrize("mismatch", ["stage", "name"])
def test_spec_mismatch_rolls_back(queue, started_run, provider_spec, mismatch, count_rows, job_row, service):
    [job] = queue.claim(Stage.provide, 1, "w")
    spec = replace(provider_spec, **({"stage": Stage.extract} if mismatch == "stage" else {"name": "wrong"}))
    with pytest.raises(ValueError):
        finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1")], [record()]), spec=spec)
    assert count_rows("documents") == count_rows("stage_outputs") == 0
    assert count_rows("jobs") == 1
    assert job_row(started_run, Stage.provide).status == JobStatus.claimed
    assert service.status(started_run).state == RunState.running


def test_missing_topic_rolls_back_all_writes(queue, started_run, count_rows, job_row):
    [job] = queue.claim(Stage.provide, 1, "w")
    with pytest.raises(AdapterFailure, match="paper track needs a topic for document 'fake:d1'") as exc:
        finish(queue, job, StageResult({}, [doc_unit("d1", 0, None)], [record()]))
    assert not exc.value.retryable
    assert count_rows("documents") == count_rows("stage_outputs") == 0
    assert job_row(started_run, Stage.provide).status == JobStatus.claimed


def test_pipeline_order_partitions_hash_and_upstream(queue, started_run, service):
    [provide] = queue.claim(Stage.provide, 1, "w")
    finish(queue, provide, StageResult({"provider": 1}, [doc_unit("d1", 7, "t1"), doc_unit("d2", 3, "t2")]))
    docs = queue.claim(Stage.extract, 10, "w")
    first = next(j for j in docs if j.unit.id == "fake:d1")
    assert first.partition == "t1" and first.order_key == (7,)
    assert first.input_hash == sha256((first.unit.content_hash + ":k1").encode()).hexdigest()
    assert queue.upstream(first) == {Stage.provide: {"provider": 1}}
    mentions = [UnitRef(UnitKind.mention, "m1", "mh", (999,), "wrong"), UnitRef(UnitKind.mention, "m2", "mh2")]
    finish(queue, first, StageResult({"extract": "d1"}, mentions), "extract1")
    second = next(j for j in docs if j.unit.id == "fake:d2")
    finish(queue, second, StageResult({"extract": "d2"}, []), "extract2")
    children = queue.claim(Stage.tag, 10, "w")
    assert [j.order_key for j in children] == [(7, 0), (7, 1)]
    assert all(j.partition == "t1" and j.parent_job_id == first.id for j in children)
    assert queue.upstream(children[0]) == {Stage.provide: {"provider": 1}, Stage.extract: {"extract": "d1"}}
    finish(queue, children[0], StageResult({"tag": 1}), "tag1")
    [enrich] = queue.claim(Stage.enrich, 1, "w")
    assert enrich.unit.id == "m1" and enrich.order_key == (7, 0)


def test_skip_optional_tag_and_uncached_output(queue, service, good_config, count_rows):
    del good_config.stages[Stage.tag]
    good_config.track = Track.realistic
    run = service.create(good_config).run_id
    service.start(run)
    [provide] = queue.claim(Stage.provide, 1, "w")
    finish(queue, provide, StageResult({}, [doc_unit("d1", 2, None)]), None)
    [extract] = queue.claim(Stage.extract, 1, "w")
    assert extract.partition == "all"
    assert extract.input_hash == sha256((extract.unit.content_hash + ":").encode()).hexdigest()
    assert queue.upstream(extract) == {} and count_rows("stage_outputs") == 0
    finish(queue, extract, StageResult({}, [UnitRef(UnitKind.mention, "m", "h")]), "ex")
    [enrich] = queue.claim(Stage.enrich, 1, "w")
    assert enrich.order_key == (2, 0)


def test_terminal_stage_and_next_stage(good_config):
    from weave_core.pipeline import next_stage
    assert next_stage(good_config, Stage.cluster) is None
    assert next_stage(good_config, Stage.extract) == Stage.tag
    del good_config.stages[Stage.tag]
    assert next_stage(good_config, Stage.extract) == Stage.enrich


def test_cancelled_claim_can_finish_but_children_not_claimed(queue, started_run, service):
    [job] = queue.claim(Stage.provide, 1, "w")
    service.cancel(started_run)
    assert finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1")]))
    assert queue.claim(Stage.extract, 1, "w") == []
    assert service.status(started_run).state == RunState.cancelled


def test_real_row_lock_is_skipped(queue, started_run, service, good_config):
    other = service.create(good_config).run_id
    service.start(other)
    with service.engine.begin() as conn:
        conn.execute(sa.select(jobs).where(jobs.c.run_id == started_run).with_for_update()).all()
        with ThreadPoolExecutor(1) as pool:
            claimed = pool.submit(queue.claim, Stage.provide, 10, "w").result(timeout=5)
        assert [j.run_id for j in claimed] == [other]
    assert [j.run_id for j in queue.claim(Stage.provide, 10, "w")] == [started_run]


def test_concurrent_completion_exactly_once(queue, started_run, count_rows, count_jobs):
    [job] = queue.claim(Stage.provide, 1, "w")
    barrier = Barrier(2)
    def complete():
        barrier.wait(timeout=5)
        return finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1")], [record()]))
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(complete) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [False, True]
    assert count_rows("documents") == count_rows("stage_outputs") == 1
    assert count_jobs(started_run, Stage.extract) == 1


def test_concurrent_last_jobs_finish_run(queue, started_run, service):
    [job] = queue.claim(Stage.provide, 1, "w")
    finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1"), doc_unit("d2", 1, "t2")]))
    claimed = queue.claim(Stage.extract, 2, "w")
    barrier = Barrier(2)
    def complete(job):
        barrier.wait(timeout=5)
        return finish(queue, job, StageResult({}, []), job.unit.id)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(complete, job) for job in claimed]
        assert all(f.result(timeout=10) for f in futures)
    assert service.status(started_run).state == RunState.done


def test_output_write_rollback_then_stale_retry(queue, started_run, service, count_rows, count_jobs, job_row):
    # Force a real database error after documents/output insertion but before
    # the job's done update. The whole completion must roll back.
    [job] = queue.claim(Stage.provide, 1, "dead")
    with service.engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE jobs ADD CONSTRAINT reject_done CHECK (status <> 'done')")
    result = StageResult({"ok": True}, [doc_unit("d1", 0, "t1")], [record()])
    with pytest.raises(sa.exc.IntegrityError):
        finish(queue, job, result)
    assert count_rows("documents") == count_rows("stage_outputs") == 0
    assert count_jobs(started_run, Stage.extract) == 0
    assert job_row(started_run, Stage.provide).status == JobStatus.claimed
    with service.engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE jobs DROP CONSTRAINT reject_done")
    queue.release_stale(now=later(seconds=61))
    [retry] = queue.claim(Stage.provide, 1, "new", now=later(seconds=62))
    assert not finish(queue, job, result)
    assert finish(queue, retry, result)
    assert count_rows("documents") == count_rows("stage_outputs") == 1
    assert count_jobs(started_run, Stage.extract) == 1


def test_stale_reclaim_respects_retry_limit(queue, started_run, job_row, service):
    from weave_core.queue import MAX_ATTEMPTS
    for attempt in range(MAX_ATTEMPTS):
        clock = later(minutes=attempt * 5)
        [job] = queue.claim(Stage.provide, 1, "dying", now=clock)
        assert queue.release_stale(now=clock + timedelta(seconds=61)) == 1
        row = job_row(started_run, Stage.provide)
        assert row.attempts == attempt + 1
        if attempt < MAX_ATTEMPTS - 1:
            assert row.status == JobStatus.pending
    assert row.status == JobStatus.failed
    assert row.error == "worker lost"
    assert row.attempts == MAX_ATTEMPTS
    assert row.claim_token is None and row.claimed_by is None and row.heartbeat_at is None
    assert queue.claim(Stage.provide, 1, "w", now=later(hours=1)) == []
    assert service.status(started_run).state == RunState.done


def test_stale_boundary_and_worker_heartbeat_scope(queue, started_run, job_row):
    clock = later()
    [job] = queue.claim(Stage.provide, 1, "owner", now=clock)
    queue.heartbeat("stranger", now=clock + timedelta(seconds=59))
    assert queue.release_stale(now=clock + timedelta(seconds=60)) == 0
    assert queue.release_stale(now=clock + timedelta(seconds=61)) == 1
    row = job_row(started_run, Stage.provide)
    assert row.status == JobStatus.pending and row.attempts == 1
    assert row.claim_token is None
    assert not finish(queue, job, StageResult({}, []), spec=replace(SPECS[0].spec, name="wrong"))


def test_shared_output_key_does_not_duplicate_or_overwrite(queue, started_run, service, good_config, count_rows):
    [first] = queue.claim(Stage.provide, 1, "w")
    assert finish(queue, first, StageResult({"original": True}, []))
    other = service.create(good_config).run_id
    service.start(other)
    [second] = queue.claim(Stage.provide, 1, "w")
    assert finish(queue, second, StageResult({"replacement": True}, []))
    assert count_rows("stage_outputs") == 1
    with service.engine.connect() as conn:
        assert conn.scalar(sa.select(stage_outputs.c.output)) == {"original": True}


def test_upstream_excludes_other_runs_and_sibling_partitions(queue, started_run, service, good_config):
    [first] = queue.claim(Stage.provide, 1, "w")
    finish(queue, first, StageResult({"own": True}, [doc_unit("d1", 0, "t1")]), "own")
    [child] = queue.claim(Stage.extract, 1, "w")
    other = service.create(good_config).run_id
    service.start(other)
    [foreign] = queue.claim(Stage.provide, 1, "w")
    finish(queue, foreign, StageResult({"foreign": True}, [doc_unit("d2", 0, "t2")]), "foreign")
    assert queue.upstream(child) == {Stage.provide: {"own": True}}


def test_completed_provide_job_writes_order_key_and_gold_rows(queue, started_run, service):
    [job] = queue.claim(Stage.provide, 1, "w")
    doc = replace(record(), order_key=(7,))
    rows = {"gold_documents": [{"document_id": "fake:d1", "language": "en", "topic": "t1", "split": None, "doc_index": 0}],
            "gold_mentions": [{"document_id": "fake:d1", "mention_id": "m1", "start": 0, "end": 4, "text": "text"}]}
    assert finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1")], [doc], rows))
    with service.engine.connect() as conn:
        assert conn.scalar(sa.text("select order_key from documents")) == [7]
        assert conn.scalar(sa.text("select topic from gold_documents")) == "t1"
        assert conn.scalar(sa.text("select count(*) from gold_mentions")) == 1


def test_gold_rows_conflict_does_nothing(queue, started_run, service, count_rows):
    [job] = queue.claim(Stage.provide, 1, "w")
    row = {"document_id": "fake:d1", "language": "en", "topic": "t1", "split": None, "doc_index": 0}
    assert finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1")], [record()], {"gold_documents": [row, row]}))
    assert count_rows("gold_documents") == 1


def test_rows_for_non_gold_table_are_refused(queue, started_run, count_rows, job_row):
    [job] = queue.claim(Stage.provide, 1, "w")
    with pytest.raises(ValueError, match="gold_.*enrichments.*mention_tags.*mentions"):
        finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1")], [record()], {"documents": [{}]}))
    assert count_rows("documents") == 0
    assert job_row(started_run, Stage.provide).status == JobStatus.claimed


def test_complete_writes_enrichment_rows(queue, started_run, count_rows):
    mention = {"id": "m1", "document_id": "fake:d1", "start": 0, "end": 4, "tok_start": 0, "tok_end": 1,
               "sentence": 0, "text": "text", "extractor": "x", "extractor_version": "1",
               "deployment": None, "probability": None}
    enrichment = {"id": "e1", "mention_id": "m1", "enricher": "clairic", "enricher_version": "1", "deployment": None,
                  "action": {"text": "text", "spans": [[0, 4]], "confidence": None},
                  "participants": {}, "location": {}, "time": {}, "discarded": {}}
    rows = {"mentions": [mention, mention], "enrichments": [enrichment, enrichment]}  # duplicates: on conflict do nothing
    [job] = queue.claim(Stage.provide, 1, "w")
    assert finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1")], [record()], rows))
    assert count_rows("enrichments") == 1
    assert count_rows("mentions") == 1


def test_large_gold_pairs_batch_exceeds_bind_parameter_cap(queue, started_run, count_rows):
    [job] = queue.claim(Stage.provide, 1, "w")
    pairs = [{"language": "en", "kind": "within", "split": "train", "topic": "t1",
              "source_document_id": "fake:d1", "source_mention_id": f"m{i}",
              "target_document_id": "fake:d1", "target_mention_id": f"n{i}", "label": 1}
             for i in range(8000)]
    assert finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1")], [record()], {"gold_pairs": pairs}))
    assert count_rows("gold_pairs") == 8000


def mention_row(doc_id="fake:d1"):
    return {"id": "m" * 32, "document_id": doc_id, "start": 0, "end": 4, "tok_start": 0, "tok_end": 1,
            "sentence": 0, "text": "text", "extractor": "gold", "extractor_version": "1", "deployment": None,
            "probability": None}


def test_complete_writes_mention_rows_once(queue, started_run, count_rows):
    [job] = queue.claim(Stage.provide, 1, "w")
    rows = {"mentions": [mention_row(), mention_row()]}
    assert finish(queue, job, StageResult({}, [doc_unit("d1", 0, "t1")], [record()], rows))
    assert count_rows("mentions") == 1
