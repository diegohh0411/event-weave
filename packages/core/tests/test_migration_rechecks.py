import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import weave_core
from pg import database


def _page(conn, url, age, status='stored'):
    conn.execute(sa.text("insert into crawled_pages (canonical_url,url,outlet,fetched_at,status) "
                         "values (:u,:u,'site',now() - cast(:age as interval),:s)"), {'u': url, 'age': age, 's': status})


def test_rechecks_migration_initializes_schedule_and_roundtrips():
    with database('weave_migration_' + uuid.uuid4().hex[:8]) as url:
        config = Config()
        config.set_main_option('script_location', str(Path(weave_core.__file__).parent / 'migrations'))
        config.attributes['database_url'] = url
        command.upgrade(config, '0013_backfill_frontier_settings')
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            _page(conn, 'https://e.com/fresh', '2 hours')
            _page(conn, 'https://e.com/mid', '3 days')
            _page(conn, 'https://e.com/old', '40 days')
            _page(conn, 'https://e.com/rej', '2 hours', 'rejected')
        command.upgrade(config, '0014_rechecks')
        with engine.connect() as conn:
            rows = {r.canonical_url.rsplit('/', 1)[1]: r for r in conn.execute(sa.text(
                'select canonical_url, fetched_at, first_fetched_at, recheck_stage, recheck_due_at from crawled_pages'))}
        fresh, mid = rows['fresh'], rows['mid']
        assert fresh.first_fetched_at == fresh.fetched_at
        assert (fresh.recheck_stage, fresh.recheck_due_at) == (0, fresh.fetched_at + timedelta(days=1))
        assert (mid.recheck_stage, mid.recheck_due_at) == (1, mid.fetched_at + timedelta(days=7))
        assert rows['old'].recheck_due_at is None
        assert rows['rej'].recheck_due_at is None
        with engine.connect() as conn:
            assert conn.scalar(sa.text('select recheck_backfilled from sites limit 1')) is None  # no sites; column exists
            assert 'page_rechecks' in sa.inspect(conn).get_table_names()
        insert = "insert into crawl_runs (id,trigger,request,status,created_at,progress) values (:id,:t,'{}','queued',now(),'{}')"
        with engine.begin() as conn:
            conn.execute(sa.text(insert), {'id': uuid.uuid4(), 't': 'recheck'})
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
            conn.execute(sa.text(insert), {'id': uuid.uuid4(), 't': 'bogus'})
        with engine.connect() as conn:
            assert 'ix_crawled_pages_document_id' in {i['name'] for i in sa.inspect(conn).get_indexes('crawled_pages')}
        command.downgrade(config, '0013_backfill_frontier_settings')
        assert 'page_rechecks' not in sa.inspect(engine).get_table_names()
        command.upgrade(config, '0014_rechecks')
        engine.dispose()
