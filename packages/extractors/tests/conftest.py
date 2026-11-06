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
