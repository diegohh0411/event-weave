import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from .vector import Vector

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
    sa.Column("content_hash", T, nullable=False),
    sa.Column("order_key", ARRAY(sa.BigInteger), nullable=False, server_default="{}"),
    sa.UniqueConstraint("provider", "external_id"))
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

gold_documents = sa.Table("gold_documents", metadata,
    sa.Column("document_id", T, sa.ForeignKey("documents.id"), primary_key=True),
    sa.Column("language", T, nullable=False), sa.Column("topic", T, nullable=False),
    sa.Column("split", T), sa.Column("doc_index", sa.Integer, nullable=False))
gold_mentions = sa.Table("gold_mentions", metadata,
    sa.Column("document_id", T, sa.ForeignKey("documents.id"), primary_key=True),
    sa.Column("mention_id", T, primary_key=True), sa.Column("start", sa.Integer, nullable=False),
    sa.Column("end", sa.Integer, nullable=False), sa.Column("text", T, nullable=False))
gold_pairs = sa.Table("gold_pairs", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    *[sa.Column(n, T, nullable=False) for n in ("language", "kind", "split", "topic", "source_document_id",
                                                 "source_mention_id", "target_document_id", "target_mention_id")],
    sa.Column("label", sa.SmallInteger, nullable=False),
    sa.CheckConstraint("kind in ('within', 'cross')", name="ck_gold_pairs_kind"),
    sa.UniqueConstraint("kind", "source_document_id", "source_mention_id", "target_document_id", "target_mention_id"))
crawled_pages = sa.Table("crawled_pages", metadata,
    sa.Column("canonical_url", T, primary_key=True), sa.Column("url", T, nullable=False),
    sa.Column("outlet", T, nullable=False), sa.Column("fetched_at", TZ, nullable=False),
    sa.Column("status", T, nullable=False), sa.Column("reason", T), sa.Column("raw_html_gz", sa.LargeBinary),
    sa.Column("document_id", T, sa.ForeignKey("documents.id")),
    sa.CheckConstraint("status in ('stored', 'rejected')", name="ck_crawled_pages_status"))

mentions = sa.Table("mentions", metadata,
    sa.Column("id", T, primary_key=True),
    sa.Column("document_id", T, sa.ForeignKey("documents.id"), nullable=False, index=True),
    *[sa.Column(n, sa.Integer, nullable=False) for n in ("start", "end", "tok_start", "tok_end", "sentence")],
    *[sa.Column(n, T, nullable=False) for n in ("text", "extractor", "extractor_version")],
    sa.Column("deployment", T), sa.Column("probability", sa.Float))
enrichments = sa.Table("enrichments", metadata,
    sa.Column("id", T, primary_key=True),
    sa.Column("mention_id", T, sa.ForeignKey("mentions.id"), nullable=False, index=True),
    sa.Column("enricher", T), sa.Column("enricher_version", T), sa.Column("deployment", T),
    *[sa.Column(n, JSONB) for n in ("action", "participants", "location", "time", "discarded")])
mention_tags = sa.Table("mention_tags", metadata,
    sa.Column("mention_id", T, sa.ForeignKey("mentions.id"), primary_key=True),
    sa.Column("tagger", T, primary_key=True), sa.Column("tagger_version", T, primary_key=True),
    sa.Column("deployment", T, primary_key=True), sa.Column("significance", sa.Float),
    sa.Column("event_type", T), sa.Column("event_type_probs", JSONB))
member_vectors = sa.Table("member_vectors", metadata,
    sa.Column("id", T, primary_key=True),
    sa.Column("mention_id", T, sa.ForeignKey("mentions.id"), nullable=False),
    sa.Column("enrichment_id", T, sa.ForeignKey("enrichments.id"), nullable=False),
    sa.Column("field", T, nullable=False), sa.Column("kind", T, nullable=False),
    sa.Column("deployment", T, nullable=False), sa.Column("dimension", sa.Integer, nullable=False),
    sa.Column("vector", Vector(), nullable=False),
    sa.CheckConstraint("kind in ('document', 'query')", name="ck_member_vectors_kind"))
sa.Index("ix_member_vectors_mention_id_field_kind", member_vectors.c.mention_id, member_vectors.c.field, member_vectors.c.kind)
clusters = sa.Table("clusters", metadata,
    sa.Column("id", T, primary_key=True), sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id")),
    sa.Column("partition", T), sa.Column("card", JSONB), sa.Column("card_hash", T), sa.Column("size", sa.Integer),
    sa.Column("created_order", ARRAY(sa.BigInteger)))
sa.Index("ix_clusters_run_id_partition", clusters.c.run_id, clusters.c.partition)
cluster_members = sa.Table("cluster_members", metadata,
    sa.Column("run_id", UUID(as_uuid=True), primary_key=True), sa.Column("mention_id", T, primary_key=True),
    sa.Column("cluster_id", T, sa.ForeignKey("clusters.id")), sa.Column("partition", T),
    sa.Column("order_key", ARRAY(sa.BigInteger)))
cluster_decisions = sa.Table("cluster_decisions", metadata,
    sa.Column("run_id", UUID(as_uuid=True), primary_key=True), sa.Column("mention_id", T, primary_key=True),
    sa.Column("adapter", T), sa.Column("adapter_version", T), sa.Column("deployment", T),
    sa.Column("candidates", JSONB), sa.Column("noul", sa.Float), sa.Column("probabilities", JSONB),
    sa.Column("outcome", T), sa.Column("reason", T), sa.Column("cluster_id", T),
    sa.CheckConstraint("outcome in ('join', 'new')", name="ck_cluster_decisions_outcome"))
decision_cache = sa.Table("decision_cache", metadata,
    sa.Column("key", T, primary_key=True), sa.Column("deployment", T), sa.Column("response", JSONB),
    sa.Column("created_at", TZ, server_default=sa.func.now()))
