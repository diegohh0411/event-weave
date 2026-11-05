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
    assert created.estimate is None

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
