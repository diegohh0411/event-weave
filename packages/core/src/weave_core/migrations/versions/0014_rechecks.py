"""Decaying rechecks: per-page schedule, outcome log, per-site backfill toggle."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = '0014_rechecks'
down_revision = '0013_backfill_frontier_settings'
branch_labels = None
depends_on = None

TRIGGERS_OLD = "trigger in ('adhoc', 'manual', 'schedule', 'backfill')"
TRIGGERS_NEW = "trigger in ('adhoc', 'manual', 'schedule', 'backfill', 'recheck')"
ARTICLES_OLD = "status in ('stored', 'existing', 'duplicate', 'rejected', 'error', 'updated', 'unchanged', 'removed')"
ARTICLES_NEW = "status in ('stored', 'existing', 'duplicate', 'rejected', 'error', 'updated', 'unchanged', 'removed', 'redirected')"
OUTCOMES = "outcome in ('unchanged', 'edited', 'gone', 'redirected', 'error')"


def _swap_check(name, table, expression):
    op.drop_constraint(name, table, type_='check')
    op.create_check_constraint(name, table, expression)


def upgrade():
    tz = sa.DateTime(timezone=True)
    op.add_column('crawled_pages', sa.Column('first_fetched_at', tz))
    op.add_column('crawled_pages', sa.Column('recheck_stage', sa.Integer, nullable=False, server_default='0'))
    op.add_column('crawled_pages', sa.Column('recheck_due_at', tz))
    op.add_column('crawled_pages', sa.Column('recheck_attempts', sa.Integer, nullable=False, server_default='0'))
    op.add_column('crawled_pages', sa.Column('gone_reason', sa.Text))
    op.create_index('ix_crawled_pages_recheck_due', 'crawled_pages', ['outlet', 'recheck_due_at'],
                    postgresql_where=sa.text('recheck_due_at IS NOT NULL'))
    op.add_column('sites', sa.Column('recheck_backfilled', sa.Boolean, nullable=False, server_default=sa.true()))
    op.create_table('page_rechecks',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('canonical_url', sa.Text, sa.ForeignKey('crawled_pages.canonical_url', ondelete='CASCADE'), nullable=False),
        sa.Column('crawl_id', UUID(as_uuid=True), sa.ForeignKey('crawl_runs.id', ondelete='SET NULL')),
        sa.Column('stage', sa.Integer, nullable=False),
        sa.Column('checked_at', tz, nullable=False, server_default=sa.func.now()),
        sa.Column('outcome', sa.Text, nullable=False), sa.Column('http_status', sa.Integer),
        sa.Column('final_url', sa.Text), sa.Column('reason', sa.Text),
        sa.Column('document_id', sa.Text, sa.ForeignKey('documents.id')),
        sa.CheckConstraint(OUTCOMES, name='ck_page_rechecks_outcome'))
    op.create_index('ix_page_rechecks_url_checked', 'page_rechecks', ['canonical_url', sa.text('checked_at DESC')])
    _swap_check('ck_crawl_runs_trigger', 'crawl_runs', TRIGGERS_NEW)
    _swap_check('ck_crawl_articles_status', 'crawl_articles', ARTICLES_NEW)
    # Existing pages join the schedule at their next future stage (fixed 1/7/30 days).
    op.execute("""
        UPDATE crawled_pages p SET first_fetched_at = p.fetched_at, recheck_stage = s.stage,
               recheck_due_at = p.fetched_at + make_interval(days => s.days)
        FROM crawled_pages q CROSS JOIN LATERAL (
            SELECT stage, days FROM (VALUES (0, 1), (1, 7), (2, 30)) v(stage, days)
            WHERE q.fetched_at + make_interval(days => v.days) > now() ORDER BY stage LIMIT 1) s
        WHERE p.canonical_url = q.canonical_url AND p.status = 'stored'""")
    op.execute("UPDATE crawled_pages SET first_fetched_at = fetched_at WHERE first_fetched_at IS NULL")


def downgrade():
    op.execute("DELETE FROM crawl_articles WHERE status = 'redirected'")
    _swap_check('ck_crawl_articles_status', 'crawl_articles', ARTICLES_OLD)
    _swap_check('ck_crawl_runs_trigger', 'crawl_runs', TRIGGERS_OLD)
    op.drop_table('page_rechecks')
    op.drop_column('sites', 'recheck_backfilled')
    op.drop_index('ix_crawled_pages_recheck_due', table_name='crawled_pages')
    for name in ('gone_reason', 'recheck_attempts', 'recheck_due_at', 'recheck_stage', 'first_fetched_at'):
        op.drop_column('crawled_pages', name)
