import os
import uuid
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from weave_core.db import migrate

ADMIN_URL = os.environ.get(
    "WEAVE_TEST_DATABASE_URL", "postgresql+psycopg://weave:weave@127.0.0.1:5432/postgres")


@contextmanager
def database(prefix="weave_clu_"):
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
    """Inference wired to stub deployments: stub-emb (embeddings, model embeddinggemma-2)."""
    from weave_core.stages import Capability
    from weave_inference.catalog import Catalog
    from weave_inference.clients.stub import clear_stub_scripts
    from weave_inference.gateway import Inference
    from weave_inference.probe import Readiness
    from weave_inference.types import Deployment, Model, Provider

    catalog = Catalog(
        models={"embeddinggemma-2": Model(id="embeddinggemma-2", capability=Capability.embeddings)},
        providers={"stub": Provider(id="stub", kind="stub", base_url="stub://")},
        deployments={"stub-emb": Deployment(id="stub-emb", model="embeddinggemma-2", provider="stub")})
    catalog.readiness = {k: Readiness(ready=True) for k in catalog.deployments}
    Inference.configure(catalog, db)
    yield catalog
    clear_stub_scripts()

