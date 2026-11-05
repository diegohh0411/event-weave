from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

metadata = sa.MetaData()
T = sa.Text
TZ = sa.DateTime(timezone=True)
runs = sa.Table("runs", metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True), sa.Column("config", JSONB, nullable=False),
    sa.Column("config_hash", T, nullable=False), sa.Column("state", T, nullable=False),
    sa.Column("created_at", TZ, nullable=False), sa.Column("started_at", TZ), sa.Column("finished_at", TZ))
documents = sa.Table("documents", metadata,
    sa.Column("id", T, primary_key=True), *[sa.Column(n, T, nullable=False) for n in ("provider", "external_id", "language", "title", "byline", "outlet", "url")],
    sa.Column("published_at", TZ), sa.Column("text", T, nullable=False),
    sa.Column("token_offsets", JSONB, nullable=False), sa.Column("sentence_offsets", JSONB, nullable=False),
    sa.Column("content_hash", T, nullable=False), sa.UniqueConstraint("provider", "external_id"))
jobs = sa.Table("jobs", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
    *[sa.Column(n, T, nullable=False) for n in ("stage", "unit_kind", "unit_id", "unit_content_hash")],
    sa.Column("parent_job_id", sa.BigInteger, sa.ForeignKey("jobs.id")),
    sa.Column("partition", T, nullable=False), sa.Column("order_key", ARRAY(sa.BigInteger), nullable=False),
    sa.Column("input_hash", T, nullable=False), sa.Column("status", T, nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"), sa.Column("available_at", TZ, nullable=False),
    sa.Column("claimed_by", T), sa.Column("claim_token", UUID(as_uuid=True)), sa.Column("heartbeat_at", TZ),
    sa.Column("error", T), sa.Column("cache_key", T), sa.UniqueConstraint("run_id", "stage", "unit_id"))
sa.Index("ix_jobs_stage_status_available_at", jobs.c.stage, jobs.c.status, jobs.c.available_at)
sa.Index("ix_jobs_run_id_partition_order_key", jobs.c.run_id, jobs.c.partition, jobs.c.order_key)
stage_outputs = sa.Table("stage_outputs", metadata,
    sa.Column("cache_key", T, primary_key=True), *[sa.Column(n, T, nullable=False) for n in ("stage", "adapter", "adapter_version")],
    sa.Column("output", JSONB, nullable=False), sa.Column("emits", JSONB, nullable=False), sa.Column("created_at", TZ, nullable=False))

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    metadata.create_all(bind=op.get_bind())

def downgrade():
    metadata.drop_all(bind=op.get_bind())
