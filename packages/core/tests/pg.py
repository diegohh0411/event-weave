import os
import uuid
from contextlib import contextmanager
import sqlalchemy as sa
from weave_core.db import migrate

ADMIN_URL = os.environ.get("WEAVE_TEST_DATABASE_URL", "postgresql+psycopg://weave:weave@127.0.0.1:5432/postgres")

@contextmanager
def database(name, template=None):
    admin = sa.create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    url = sa.engine.make_url(ADMIN_URL).set(database=name)
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else ''))
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.dispose()
