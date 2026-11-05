import pytest
from test_config import registry, catalog, good_config
from weave_core.config import ConfigError, config_hash, validate
from weave_core.db import make_engine
from weave_core.runs import RunService, RunStateError
from weave_core.stages import Stage, JobStatus, RunState

@pytest.fixture
def service(database_url, registry, catalog):
    engine = make_engine(database_url)
    yield RunService(engine, registry, catalog)
    engine.dispose()

def test_create_stores_hash_and_estimate(service, good_config):
    created = service.create(good_config)
    assert service.status(created.run_id).state == RunState.created
    assert created.config_hash == config_hash(validate(good_config, service.registry, service.catalog))
    assert created.estimate.amount is None

def test_create_refuses_invalid_config(service, good_config):
    del good_config.stages[Stage.cluster]
    with pytest.raises(ConfigError): service.create(good_config)

def test_start_enqueues_one_provide_job(service, good_config):
    run = service.create(good_config).run_id
    service.start(run)
    assert service.status(run).stages[Stage.provide] == {JobStatus.pending: 1}

def test_illegal_transitions(service, good_config):
    run = service.create(good_config).run_id
    with pytest.raises(RunStateError): service.resume(run)
    service.start(run)
    with pytest.raises(RunStateError): service.start(run)

def test_cancel_then_resume(service, good_config):
    run = service.create(good_config).run_id
    service.start(run); service.cancel(run)
    assert service.status(run).state == RunState.cancelled
    service.resume(run)
    assert service.status(run).state == RunState.running
    assert service.status(run).stages[Stage.provide] == {JobStatus.pending: 1}

def test_mark_done_without_active_jobs(service, good_config):
    from weave_core.db import session_scope
    from weave_core.runs import mark_done_if_finished
    run = service.create(good_config).run_id
    with session_scope(service.engine) as session:
        assert mark_done_if_finished(session, run)
    assert service.status(run).state == RunState.done

@pytest.mark.parametrize("changed_stage,equal", [(Stage.cluster, True), (Stage.provide, False)])
def test_provider_hash_uses_only_normalized_provider_choice(service, good_config, changed_stage, equal):
    import hashlib
    import sqlalchemy as sa
    from weave_core.schema import jobs, runs
    other = good_config.model_copy(deep=True)
    other.stages[changed_stage].settings = {"window": 9}
    ids = [service.create(cfg).run_id for cfg in (good_config, other)]
    for run in ids:
        service.start(run)
    with service.engine.connect() as conn:
        rows = [conn.execute(sa.select(jobs).where(jobs.c.run_id == run)).one() for run in ids]
        configs = [conn.execute(sa.select(runs).where(runs.c.id == run)).one() for run in ids]
    assert configs[0].config_hash != configs[1].config_hash
    expected = hashlib.sha256(b'{"adapter":"fake-provider","deployments":{},"settings":{"docs":3,"duplicate_first":false,"no_topic":false,"window":1}}').hexdigest()
    assert rows[0].unit_content_hash == rows[0].input_hash == expected
    assert (rows[0].input_hash == rows[1].input_hash) == equal
    assert rows[1].unit_content_hash == rows[1].input_hash
    assert configs[0].config_hash == config_hash(validate(good_config, service.registry, service.catalog))

def test_start_refuses_deployment_that_went_not_ready(service, good_config):
    run = service.create(good_config).run_id
    service.catalog["good-extractor"] = (service.catalog["good-extractor"][0], False)
    with pytest.raises(ConfigError, match="not ready"):
        service.start(run)
    status = service.status(run)
    assert status.state == RunState.created and status.stages == {}
