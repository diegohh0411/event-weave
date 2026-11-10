import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from weave_core.textnorm import edit_hash

revision = "0011_sites"
down_revision = "0010_reruns"
branch_labels = None
depends_on = None


def upgrade():
    tz = sa.DateTime(timezone=True)
    op.create_table("sites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("domain", sa.Text, nullable=False, unique=True),
        sa.Column("language", sa.Text, nullable=False),
        sa.Column("sitemaps", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("feeds", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("per_day", sa.Integer),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("check_every_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("daily_cap", sa.Integer, nullable=False, server_default="50"),
        sa.Column("min_interval_seconds", sa.REAL, nullable=False, server_default="2"),
        sa.Column("max_parallel", sa.Integer, nullable=False, server_default="1"),
        sa.Column("next_check_at", tz, nullable=False, server_default=sa.func.now()),
        sa.Column("last_scheduled_at", tz),
        sa.Column("created_at", tz, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", tz, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("check_every_minutes >= 5", name="ck_sites_check_every_minutes"),
        sa.CheckConstraint("daily_cap >= 1", name="ck_sites_daily_cap"),
        sa.CheckConstraint("min_interval_seconds >= 0.5", name="ck_sites_min_interval_seconds"),
        sa.CheckConstraint("max_parallel BETWEEN 1 AND 4", name="ck_sites_max_parallel"))
    op.create_table("domain_throttle", sa.Column("domain", sa.Text, primary_key=True),
                    sa.Column("next_request_at", tz, nullable=False))
    op.add_column("crawl_runs", sa.Column("site_id", UUID(as_uuid=True)))
    op.create_foreign_key("fk_crawl_runs_site_id", "crawl_runs", "sites", ["site_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_crawl_runs_site_id", "crawl_runs", ["site_id"])
    op.add_column("crawl_runs", sa.Column("trigger", sa.Text, nullable=False, server_default="adhoc"))
    op.create_check_constraint("ck_crawl_runs_trigger", "crawl_runs", "trigger in ('adhoc', 'manual', 'schedule')")
    op.drop_constraint("ck_crawl_articles_status", "crawl_articles", type_="check")
    op.create_check_constraint("ck_crawl_articles_status", "crawl_articles", "status in ('stored', 'existing', 'duplicate', 'rejected', 'error', 'updated', 'unchanged', 'removed')")
    op.drop_constraint("ck_crawled_pages_status", "crawled_pages", type_="check")
    op.create_check_constraint("ck_crawled_pages_status", "crawled_pages", "status in ('stored', 'rejected', 'gone')")
    op.add_column("crawled_pages", sa.Column("gone_at", tz))
    op.add_column("documents", sa.Column("text_hash", sa.Text))
    conn = op.get_bind()
    for row in conn.execute(sa.text("SELECT id, title, text FROM documents")):
        conn.execute(sa.text("UPDATE documents SET text_hash = :hash WHERE id = :id"),
                     {"hash": edit_hash(row.title, row.text), "id": row.id})
    op.drop_constraint("documents_provider_external_id_key", "documents", type_="unique")
    op.create_unique_constraint("documents_provider_external_id_version_key", "documents", ["provider", "external_id", "version"])


def downgrade():
    op.drop_constraint("documents_provider_external_id_version_key", "documents", type_="unique")
    op.create_unique_constraint("documents_provider_external_id_key", "documents", ["provider", "external_id"])
    op.drop_column("documents", "text_hash")
    op.drop_column("crawled_pages", "gone_at")
    op.drop_constraint("ck_crawled_pages_status", "crawled_pages", type_="check")
    op.create_check_constraint("ck_crawled_pages_status", "crawled_pages", "status in ('stored', 'rejected')")
    op.drop_constraint("ck_crawl_articles_status", "crawl_articles", type_="check")
    op.create_check_constraint("ck_crawl_articles_status", "crawl_articles", "status in ('stored', 'existing', 'duplicate', 'rejected', 'error')")
    op.drop_constraint("ck_crawl_runs_trigger", "crawl_runs", type_="check")
    op.drop_column("crawl_runs", "trigger")
    op.drop_index("ix_crawl_runs_site_id", table_name="crawl_runs")
    op.drop_constraint("fk_crawl_runs_site_id", "crawl_runs", type_="foreignkey")
    op.drop_column("crawl_runs", "site_id")
    op.drop_table("domain_throttle")
    op.drop_table("sites")
