import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import weave_core
from pg import database


def test_backfill_migration_roundtrip_and_constraints():
    with database('weave_migration_' + uuid.uuid4().hex[:8]) as url:
        config = Config()
        config.set_main_option('script_location', str(Path(weave_core.__file__).parent / 'migrations'))
        config.attributes['database_url'] = url
        command.upgrade(config, '0011_sites')
        engine = sa.create_engine(url)
        site_id = uuid.uuid4()
        crawl_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(sa.text("insert into sites (id,name,domain,language) values (:id,'site','example.com','en')"), {'id': site_id})
            conn.execute(sa.text("insert into crawl_runs (id,site_id,trigger,request,status,created_at,progress) values (:id,:site,'manual','{}','completed',now(),'{}')"), {'id': crawl_id, 'site': site_id})
            conn.execute(sa.text("insert into crawl_articles (crawl_id,canonical_url,outlet,url,status,fetched,updated_at) values (:id,'https://example.com/a','site','https://example.com/a','existing',false,now())"), {'id': crawl_id})
        command.upgrade(config, '0012_backfill')
        with engine.connect() as conn:
            assert conn.execute(sa.text('select backfill_mode, backfill_per_hour from sites')).one() == ('off', 60)
            assert {'backfill_sitemaps', 'backfill_urls'} <= set(sa.inspect(conn).get_table_names())
        for values in ("backfill_mode='date'", 'backfill_per_hour=0', 'backfill_per_hour=1001'):
            with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
                conn.execute(sa.text(f'update sites set {values}'))
        command.downgrade(config, '0011_sites')
        assert 'backfill_urls' not in sa.inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.scalar(sa.text('select count(*) from sites')) == 1
            assert conn.scalar(sa.text('select count(*) from crawl_runs')) == 1
            assert conn.scalar(sa.text('select count(*) from crawl_articles')) == 1
        engine.dispose()
