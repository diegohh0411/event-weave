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
