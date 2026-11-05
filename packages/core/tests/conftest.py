import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from pg import database
from test_config import registry, catalog, good_config
from weave_core.adapters import UnitRef
from weave_core.db import make_engine, migrate
from weave_core.runs import RunService
from weave_core.schema import jobs, metadata
from weave_core.stages import UnitKind


@pytest.fixture(scope="session")
def template_database():
    with database("weave_template") as url:
        migrate(url)
        yield "weave_template"

@pytest.fixture
def database_url(template_database):
    with database("weave_test_" + uuid.uuid4().hex[:8], template_database) as url:
        yield url


def later(**kwargs):
    return datetime.now(timezone.utc) + timedelta(**kwargs)


def doc_unit(external_id, rank, topic):
    return UnitRef(UnitKind.document, f"fake:{external_id}", sha256(external_id.encode()).hexdigest(), (rank,), topic)


@pytest.fixture
def service(database_url, registry, catalog):
    engine = make_engine(database_url)
    yield RunService(engine, registry, catalog)
    engine.dispose()


@pytest.fixture
def started_run(service, good_config):
    run = service.create(good_config).run_id
    service.start(run)
    return run


@pytest.fixture
def job_row(service):
    def read(run, stage):
        with service.engine.connect() as conn:
            return SimpleNamespace(**conn.execute(sa.select(jobs).where(jobs.c.run_id == run, jobs.c.stage == stage)).mappings().one())
    return read


@pytest.fixture
def count_jobs(service):
    def count(run, stage):
        with service.engine.connect() as conn:
            return conn.scalar(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.run_id == run, jobs.c.stage == stage))
    return count


@pytest.fixture
def count_rows(service):
    def count(table):
        with service.engine.connect() as conn:
            return conn.scalar(sa.select(sa.func.count()).select_from(metadata.tables[table]))
    return count


@pytest.fixture
def force_claim(service):
    from weave_core.stages import JobStatus

    def claim(run, stage, unit_id):
        with service.engine.begin() as conn:
            conn.execute(jobs.update().where(
                jobs.c.run_id == run, jobs.c.stage == stage, jobs.c.unit_id == unit_id
            ).values(status=JobStatus.claimed, claimed_by="other-worker",
                     claim_token=uuid.uuid4(), heartbeat_at=datetime.now(timezone.utc)))
    return claim
