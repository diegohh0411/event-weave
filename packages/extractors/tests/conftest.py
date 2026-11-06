import os
import uuid
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from weave_core.db import migrate

ADMIN_URL = os.environ.get(
    "WEAVE_TEST_DATABASE_URL", "postgresql+psycopg://weave:weave@127.0.0.1:5432/postgres")


@contextmanager
def database(prefix="weave_ext_"):
    name = prefix + uuid.uuid4().hex[:8]
    admin = sa.create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    url = sa.engine.make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}"')
    try:
        migrate(url)
        yield url
    finally:
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture
def database_url():
    with database() as url:
        yield url


@pytest.fixture
def db(database_url, monkeypatch):
    """A migrated database that adapters reach through WEAVE_DATABASE_URL."""
    monkeypatch.setenv("WEAVE_DATABASE_URL", database_url)
    engine = sa.create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def stub_inference(db):
    """Inference wired to stub deployments: stub-s1 (system_one), stub-llm (llm), stub-s1-small (tiny context)."""
    from weave_core.stages import Capability
    from weave_inference.catalog import Catalog
    from weave_inference.clients.stub import clear_stub_scripts
    from weave_inference.gateway import Inference
    from weave_inference.probe import Readiness
    from weave_inference.types import Deployment, Limits, Model, Provider

    def make(context_tokens=None):
        catalog = Catalog(
            models={"m-s1": Model(id="m-s1", capability=Capability.system_one),
                    "m-llm": Model(id="m-llm", capability=Capability.llm)},
            providers={"stub": Provider(id="stub", kind="stub", base_url="stub://")},
            deployments={"stub-s1": Deployment(id="stub-s1", model="m-s1", provider="stub"),
                         "stub-llm": Deployment(id="stub-llm", model="m-llm", provider="stub"),
                         "stub-s1-small": Deployment(id="stub-s1-small", model="m-s1", provider="stub",
                                                     limits=Limits(context_tokens=context_tokens))})
        catalog.readiness = {k: Readiness(ready=True) for k in catalog.deployments}
        Inference.configure(catalog, db)
        return catalog

    make()
    yield make
    clear_stub_scripts()
