import os
import uuid

import pytest
import sqlalchemy as sa

ADMIN_URL = os.environ.get("WEAVE_TEST_DATABASE_URL", "postgresql+psycopg://weave:weave@127.0.0.1:5432/postgres")


@pytest.fixture
def database_url():
    name = "weave_api_" + uuid.uuid4().hex[:8]
    admin = sa.create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        yield sa.engine.make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture
def engine(database_url):
    from weave_core.db import make_engine, migrate

    migrate(database_url)
    eng = make_engine(database_url)
    yield eng
    eng.dispose()


@pytest.fixture
def service(engine):
    from weave_core.config import StaticCatalog
    from weave_core.registry import Registry
    from weave_core.runs import RunService

    return RunService(engine, Registry(), StaticCatalog({}))
