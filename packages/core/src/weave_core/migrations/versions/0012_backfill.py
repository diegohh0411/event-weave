import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0012_backfill"
down_revision = "0011_sites"
branch_labels = None
depends_on = None


def upgrade():
    tz = sa.DateTime(timezone=True)
    for column in (
        sa.Column('backfill_mode', sa.Text, nullable=False, server_default='off'),
        sa.Column('backfill_from', sa.Date), sa.Column('backfill_until', tz),
        sa.Column('backfill_per_hour', sa.Integer, nullable=False, server_default='60'),
        sa.Column('backfill_state', sa.Text, nullable=False, server_default='off'),
        sa.Column('backfill_started_at', tz), sa.Column('backfill_finished_at', tz), sa.Column('backfill_error', sa.Text),
    ):
        op.add_column('sites', column)
    for name, expression in (
        ('mode', "backfill_mode in ('off', 'date', 'all')"),
        ('from', "backfill_mode != 'date' or backfill_from is not null"),
        ('per_hour', 'backfill_per_hour between 1 and 1000'),
        ('state', "backfill_state in ('off', 'running', 'paused', 'done', 'failed')"),
    ):
        op.create_check_constraint('ck_sites_backfill_' + name, 'sites', expression)
    op.drop_constraint('ck_crawl_runs_trigger', 'crawl_runs', type_='check')
    op.create_check_constraint('ck_crawl_runs_trigger', 'crawl_runs', "trigger in ('adhoc', 'manual', 'schedule', 'backfill')")
    op.create_table('backfill_sitemaps',
        sa.Column('site_id', UUID(as_uuid=True), sa.ForeignKey('sites.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('url', sa.Text, primary_key=True), sa.Column('depth', sa.Integer, nullable=False),
        sa.Column('lastmod', tz), sa.Column('status', sa.Text, nullable=False), sa.Column('error', sa.Text),
        sa.Column('urls_found', sa.Integer, nullable=False, server_default='0'), sa.Column('scanned_at', tz),
        sa.CheckConstraint("status in ('pending', 'done', 'skipped', 'error')", name='ck_backfill_sitemaps_status'))
    op.create_index('ix_backfill_sitemaps_site_status', 'backfill_sitemaps', ['site_id', 'status'])
    op.create_table('backfill_urls',
        sa.Column('site_id', UUID(as_uuid=True), sa.ForeignKey('sites.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('canonical_url', sa.Text, primary_key=True), sa.Column('published_at', tz), sa.Column('updated_at', tz),
        sa.Column('title', sa.Text), sa.Column('sitemap_url', sa.Text), sa.Column('status', sa.Text, nullable=False),
        sa.Column('outcome', sa.Text), sa.Column('document_id', sa.Text, sa.ForeignKey('documents.id')),
        sa.Column('crawl_id', UUID(as_uuid=True), sa.ForeignKey('crawl_runs.id')), sa.Column('done_at', tz),
        sa.CheckConstraint("status in ('pending', 'done')", name='ck_backfill_urls_status'))
    op.create_index('ix_backfill_urls_site_status_published', 'backfill_urls', ['site_id', 'status', sa.text('published_at DESC')])


def downgrade():
    op.drop_table('backfill_urls')
    op.drop_table('backfill_sitemaps')
    op.drop_constraint('ck_crawl_runs_trigger', 'crawl_runs', type_='check')
    op.create_check_constraint('ck_crawl_runs_trigger', 'crawl_runs', "trigger in ('adhoc', 'manual', 'schedule')")
    for name in ('mode', 'from', 'per_hour', 'state'):
        op.drop_constraint('ck_sites_backfill_' + name, 'sites', type_='check')
    for name in ('backfill_mode', 'backfill_from', 'backfill_until', 'backfill_per_hour', 'backfill_state', 'backfill_started_at', 'backfill_finished_at', 'backfill_error'):
        op.drop_column('sites', name)
