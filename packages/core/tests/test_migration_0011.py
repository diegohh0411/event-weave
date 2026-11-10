import hashlib
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import weave_core
from pg import database


def test_sites_migration_backfills_and_downgrades():
    with database('weave_migration_' + uuid.uuid4().hex[:8]) as url:
        config = Config()
        config.set_main_option('script_location', str(Path(weave_core.__file__).parent / 'migrations'))
        config.attributes['database_url'] = url
        command.upgrade(config, '0010_reruns')
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(sa.text("""insert into documents (id, provider, external_id, language, title, byline, outlet, url, text, token_offsets, sentence_offsets, content_hash, article_id) values ('a','p','a','en','  Ａ ','b','o','u','hello  world','[]','[]','h','a')"""))
        command.upgrade(config, '0011_sites')
        with engine.connect() as conn:
            assert conn.scalar(sa.text('select text_hash from documents')) == hashlib.sha256(b'A\nhello world').hexdigest()
            assert {'sites', 'domain_throttle'} <= set(sa.inspect(conn).get_table_names())
        command.downgrade(config, '0010_reruns')
        assert 'sites' not in sa.inspect(engine).get_table_names()
        engine.dispose()


def test_downgrade_rejects_versioned_documents():
    with database('weave_migration_' + uuid.uuid4().hex[:8]) as url:
        config = Config()
        config.set_main_option('script_location', str(Path(weave_core.__file__).parent / 'migrations'))
        config.attributes['database_url'] = url
        command.upgrade(config, '0011_sites')
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(sa.text("""insert into documents (id, provider, external_id, language, title, byline, outlet, url, text, token_offsets, sentence_offsets, content_hash, article_id, version) values ('a@v2','p','a','en','a','b','o','u','text','[]','[]','h','a',2)"""))
        with pytest.raises(RuntimeError, match='cannot downgrade 0011 with versioned documents'):
            command.downgrade(config, '0010_reruns')
        engine.dispose()
