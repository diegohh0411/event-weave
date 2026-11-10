import uuid
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import weave_core
from pg import database


def test_upgrade_backfills_versions_and_legacy_snapshots():
    with database('weave_migration_' + uuid.uuid4().hex[:8]) as url:
        config = Config()
        config.set_main_option('script_location', str(Path(weave_core.__file__).parent / 'migrations'))
        config.attributes['database_url'] = url
        command.upgrade(config, '0008_crawls')
        engine = sa.create_engine(url)
        run_id = uuid.uuid4()
        started = datetime(2025, 1, 2, tzinfo=timezone.utc)
        with engine.begin() as conn:
            conn.execute(sa.text("""insert into documents
                (id, provider, external_id, language, title, byline, outlet, url, text,
                 token_offsets, sentence_offsets, content_hash)
                values (:id, 'p', :id, 'en', 't', 'b', 'o', 'u', 'x', '[]', '[]', 'h')"""),
                [{'id': 'a'}, {'id': 'b'}])
            conn.execute(sa.text("""insert into runs (id, config, config_hash, state, created_at, started_at)
                values (:id, '{}', 'h', 'running', :started, :started)"""), {'id': run_id, 'started': started})
            conn.execute(sa.text("""insert into jobs
                (run_id, stage, unit_kind, unit_id, unit_content_hash, partition, order_key, input_hash, status, available_at)
                values (:run, :stage, :kind, :id, 'h', 'p', :ordering, 'h', 'queued', :started)"""), [
                    {'run': run_id, 'stage': 'extract', 'kind': 'document', 'id': 'a', 'ordering': [2], 'started': started},
                    {'run': run_id, 'stage': 'extract', 'kind': 'document', 'id': 'b', 'ordering': [1], 'started': started},
                    {'run': run_id, 'stage': 'extract', 'kind': 'document', 'id': 'missing', 'ordering': [0], 'started': started},
                ])
        command.upgrade(config, '0009_library')
        with engine.connect() as conn:
            assert conn.execute(sa.text('select id, article_id, version, superseded_at from documents order by id')).all() == [
                ('a', 'a', 1, None), ('b', 'b', 1, None)]
            assert conn.execute(sa.text('select position, document_id from run_documents where run_id = :id order by position'),
                                {'id': run_id}).all() == [(0, 'b'), (1, 'a')]
            assert conn.scalar(sa.text('select snapshot_at from runs where id = :id'), {'id': run_id}) == started
        command.downgrade(config, '0008_crawls')
        inspector = sa.inspect(engine)
        assert 'run_documents' not in inspector.get_table_names()
        assert 'article_id' not in {column['name'] for column in inspector.get_columns('documents')}
        assert 'snapshot_at' not in {column['name'] for column in inspector.get_columns('runs')}
        engine.dispose()
