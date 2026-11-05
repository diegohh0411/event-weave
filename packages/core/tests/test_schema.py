import sqlalchemy as sa
from weave_core.db import make_engine, migrate


def test_migrate_creates_core_tables(database_url):
    engine = make_engine(database_url)
    names = set(sa.inspect(engine).get_table_names())
    assert {"runs", "documents", "jobs", "stage_outputs"} <= names


def test_vector_extension_installed(database_url):
    with make_engine(database_url).connect() as c:
        assert c.execute(sa.text("select 1 from pg_extension where extname='vector'")).scalar() == 1


def test_migrate_is_idempotent(database_url):
    assert migrate(database_url) == migrate(database_url)
