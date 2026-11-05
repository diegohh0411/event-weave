from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

metadata = sa.MetaData()
T = sa.Text
TZ = sa.DateTime(timezone=True)
sa.Table("runs", metadata, sa.Column("id", UUID(as_uuid=True), primary_key=True))
ledger = sa.Table("ledger", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id")),
    sa.Column("stage", T), sa.Column("adapter", T),
    sa.Column("deployment", T, nullable=False), sa.Column("request_hash", T, nullable=False),
    sa.Column("input_tokens", sa.Integer), sa.Column("output_tokens", sa.Integer), sa.Column("cached_tokens", sa.Integer),
    sa.Column("cost", sa.Numeric(14, 8), nullable=False), sa.Column("estimated", sa.Boolean, nullable=False),
    sa.Column("latency_ms", sa.Integer, nullable=False), sa.Column("attempts", sa.Integer, nullable=False),
    sa.Column("status", T, nullable=False), sa.Column("error", T), sa.Column("created_at", TZ, nullable=False),
    sa.CheckConstraint("status in ('ok', 'error', 'refused')", name="ck_ledger_status"))
sa.Index("ix_ledger_run_id_stage", ledger.c.run_id, ledger.c.stage)
sa.Index("ix_ledger_adapter_deployment", ledger.c.adapter, ledger.c.deployment)
run_deployments = sa.Table("run_deployments", metadata,
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), primary_key=True),
    sa.Column("deployment", T, primary_key=True), sa.Column("ready", sa.Boolean, nullable=False),
    sa.Column("reason", T), sa.Column("server_version", T), sa.Column("reported_model_id", T),
    sa.Column("probed_at", TZ, nullable=False))

revision = "0002_inference"
down_revision = "0001_core"
branch_labels = None
depends_on = None

def upgrade():
    metadata.create_all(bind=op.get_bind(), tables=[ledger, run_deployments])

def downgrade():
    metadata.drop_all(bind=op.get_bind(), tables=[run_deployments, ledger])
