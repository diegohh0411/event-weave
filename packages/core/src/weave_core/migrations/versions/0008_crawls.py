import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0008_crawls"
down_revision = "0007_evaluations"
branch_labels = None
depends_on = None

metadata = sa.MetaData()
sa.Table("documents", metadata, sa.Column("id", sa.Text, primary_key=True))
crawl_runs = sa.Table("crawl_runs", metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("request", JSONB, nullable=False), sa.Column("status", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("claim_token", UUID(as_uuid=True)), sa.Column("lease_until", sa.DateTime(timezone=True)),
    sa.Column("progress", JSONB, nullable=False), sa.Column("error", sa.Text),
    sa.CheckConstraint("status in ('queued', 'running', 'completed', 'failed')", name="ck_crawl_runs_status"))
crawl_articles = sa.Table("crawl_articles", metadata,
    sa.Column("crawl_id", UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("canonical_url", sa.Text, primary_key=True), sa.Column("outlet", sa.Text, nullable=False),
    sa.Column("url", sa.Text, nullable=False), sa.Column("status", sa.Text, nullable=False), sa.Column("reason", sa.Text),
    sa.Column("document_id", sa.Text, sa.ForeignKey("documents.id")), sa.Column("fetched", sa.Boolean, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status in ('stored', 'existing', 'duplicate', 'rejected', 'error')", name="ck_crawl_articles_status"))
crawl_sources = sa.Table("crawl_sources", metadata,
    sa.Column("crawl_id", UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("url", sa.Text, primary_key=True), sa.Column("site", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False), sa.Column("status", sa.Text, nullable=False), sa.Column("error", sa.Text),
    sa.CheckConstraint("kind in ('feed', 'sitemap')", name="ck_crawl_sources_kind"))
NEW = [crawl_runs, crawl_articles, crawl_sources]


def upgrade():
    metadata.create_all(bind=op.get_bind(), tables=NEW)


def downgrade():
    metadata.drop_all(bind=op.get_bind(), tables=list(reversed(NEW)))
