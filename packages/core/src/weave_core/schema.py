import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from .vector import Vector

metadata = sa.MetaData()
T = sa.Text
TZ = sa.DateTime(timezone=True)
runs = sa.Table("runs", metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True), sa.Column("config", JSONB, nullable=False),
    sa.Column("config_hash", T, nullable=False), sa.Column("state", T, nullable=False),
    sa.Column("created_at", TZ, nullable=False), sa.Column("started_at", TZ), sa.Column("finished_at", TZ),
    sa.Column("snapshot_at", TZ),
    sa.Column("source_run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id")),
    sa.Column("rerun_mode", T), sa.CheckConstraint("rerun_mode IN ('snapshot', 'current')"))
documents = sa.Table("documents", metadata,
    sa.Column("id", T, primary_key=True), *[sa.Column(n, T, nullable=False) for n in ("provider", "external_id", "language", "title", "byline", "outlet", "url")],
    sa.Column("published_at", TZ), sa.Column("text", T, nullable=False),
    sa.Column("token_offsets", JSONB, nullable=False), sa.Column("sentence_offsets", JSONB, nullable=False),
    sa.Column("content_hash", T, nullable=False), sa.Column("text_hash", T),
    sa.Column("article_id", T, nullable=False), sa.Column("version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("superseded_at", TZ),
    sa.Column("order_key", ARRAY(sa.BigInteger), nullable=False, server_default="{}"),
    sa.UniqueConstraint("provider", "external_id", "version"))
sa.Index("ix_documents_article_id", documents.c.article_id)
sa.Index("ix_documents_latest", documents.c.article_id, postgresql_where=documents.c.superseded_at.is_(None))
run_documents = sa.Table("run_documents", metadata,
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("position", sa.Integer, primary_key=True),
    sa.Column("document_id", T, sa.ForeignKey("documents.id"), nullable=False),
    sa.UniqueConstraint("run_id", "document_id"))
jobs = sa.Table("jobs", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
    *[sa.Column(n, T, nullable=False) for n in ("stage", "unit_kind", "unit_id", "unit_content_hash")],
    sa.Column("parent_job_id", sa.BigInteger, sa.ForeignKey("jobs.id")),
    sa.Column("partition", T, nullable=False), sa.Column("order_key", ARRAY(sa.BigInteger), nullable=False),
    sa.Column("input_hash", T, nullable=False), sa.Column("status", T, nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"), sa.Column("available_at", TZ, nullable=False),
    sa.Column("claimed_by", T), sa.Column("claim_token", UUID(as_uuid=True)), sa.Column("heartbeat_at", TZ),
    sa.Column("error", T), sa.Column("cache_key", T),
    sa.Column("cache_hit", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.UniqueConstraint("run_id", "stage", "unit_id"))
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
    sa.Column("status", T, nullable=False), sa.Column("reason", T), sa.Column("raw_html_gz", sa.LargeBinary), sa.Column("gone_at", TZ),
    sa.Column("document_id", T, sa.ForeignKey("documents.id")),
    sa.Column("first_fetched_at", TZ), sa.Column("recheck_stage", sa.Integer, nullable=False, server_default="0"),
    sa.Column("recheck_due_at", TZ), sa.Column("recheck_attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("gone_reason", T),
    sa.Index("ix_crawled_pages_document_id", "document_id"),
    sa.Index("ix_crawled_pages_recheck_due", "outlet", "recheck_due_at", postgresql_where=sa.text("recheck_due_at IS NOT NULL")),
    sa.CheckConstraint("status in ('stored', 'rejected', 'gone')", name="ck_crawled_pages_status"))
page_rechecks = sa.Table("page_rechecks", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("canonical_url", T, sa.ForeignKey("crawled_pages.canonical_url", ondelete="CASCADE"), nullable=False),
    sa.Column("crawl_id", UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id", ondelete="SET NULL")),
    sa.Column("stage", sa.Integer, nullable=False), sa.Column("checked_at", TZ, nullable=False, server_default=sa.func.now()),
    sa.Column("outcome", T, nullable=False), sa.Column("http_status", sa.Integer),
    sa.Column("final_url", T), sa.Column("reason", T), sa.Column("document_id", T, sa.ForeignKey("documents.id")),
    sa.Index("ix_page_rechecks_url_checked", "canonical_url", sa.text("checked_at DESC")),
    sa.CheckConstraint("outcome in ('unchanged', 'edited', 'gone', 'redirected', 'error')", name="ck_page_rechecks_outcome"))

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
evaluations = sa.Table("evaluations", metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
    *[sa.Column(n, T, nullable=False) for n in ("config_hash", "gold_hash", "scorer_version", "split")],
    sa.Column("seed", sa.BigInteger, nullable=False), sa.Column("resamples", sa.Integer, nullable=False),
    sa.Column("created_at", TZ, nullable=False), sa.Column("report", JSONB, nullable=False))
sa.Index("ix_evaluations_run_id_created_at", evaluations.c.run_id, evaluations.c.created_at)
evaluation_scores = sa.Table("evaluation_scores", metadata,
    sa.Column("evaluation_id", UUID(as_uuid=True), sa.ForeignKey("evaluations.id"), primary_key=True),
    sa.Column("track", T, primary_key=True), sa.Column("setting", T, primary_key=True),
    sa.Column("language", T, primary_key=True), sa.Column("metric", T, primary_key=True),
    sa.Column("precision", sa.Float), sa.Column("recall", sa.Float), sa.Column("f1", sa.Float, nullable=False),
    sa.Column("f1_low", sa.Float), sa.Column("f1_high", sa.Float),
    sa.CheckConstraint("track in ('paper', 'realistic')", name="ck_evaluation_scores_track"),
    sa.CheckConstraint("setting in ('within', 'cross', 'pooled')", name="ck_evaluation_scores_setting"),
    sa.CheckConstraint("metric in ('muc', 'b3', 'ceafe', 'blanc', 'conll')", name="ck_evaluation_scores_metric"))

sites = sa.Table("sites", metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("name", T, nullable=False, unique=True), sa.Column("domain", T, nullable=False, unique=True),
    sa.Column("language", T, nullable=False),
    sa.Column("sitemaps", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("feeds", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("per_day", sa.Integer), sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("check_every_minutes", sa.Integer, nullable=False, server_default="60"),
    sa.Column("daily_cap", sa.Integer, nullable=False, server_default="50"),
    sa.Column("min_interval_seconds", sa.REAL, nullable=False, server_default="2"),
    sa.Column("max_parallel", sa.Integer, nullable=False, server_default="1"),
    sa.Column("next_check_at", TZ, nullable=False, server_default=sa.func.now()),
    sa.Column("last_scheduled_at", TZ),
    sa.Column("backfill_frontier_mode", T),
    sa.Column("backfill_frontier_from", sa.Date),
    sa.Column("backfill_mode", T, nullable=False, server_default="off"),
    sa.Column("backfill_from", sa.Date), sa.Column("backfill_until", TZ),
    sa.Column("backfill_per_hour", sa.Integer, nullable=False, server_default="60"),
    sa.Column("backfill_state", T, nullable=False, server_default="off"),
    sa.Column("backfill_started_at", TZ), sa.Column("backfill_finished_at", TZ), sa.Column("backfill_error", T),
    sa.Column("recheck_backfilled", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.CheckConstraint("backfill_mode in ('off', 'date', 'all')", name="ck_sites_backfill_mode"),
    sa.CheckConstraint("backfill_mode != 'date' or backfill_from is not null", name="ck_sites_backfill_from"),
    sa.CheckConstraint("backfill_per_hour between 1 and 1000", name="ck_sites_backfill_per_hour"),
    sa.CheckConstraint("backfill_state in ('off', 'running', 'paused', 'done', 'failed')", name="ck_sites_backfill_state"),
    sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("check_every_minutes >= 5", name="ck_sites_check_every_minutes"),
    sa.CheckConstraint("daily_cap >= 1", name="ck_sites_daily_cap"),
    sa.CheckConstraint("min_interval_seconds >= 0.5", name="ck_sites_min_interval_seconds"),
    sa.CheckConstraint("max_parallel BETWEEN 1 AND 4", name="ck_sites_max_parallel"))
domain_throttle = sa.Table("domain_throttle", metadata,
    sa.Column("domain", T, primary_key=True), sa.Column("next_request_at", TZ, nullable=False))

crawl_runs = sa.Table("crawl_runs", metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("site_id", UUID(as_uuid=True), sa.ForeignKey("sites.id", ondelete="SET NULL"), index=True),
    sa.Column("trigger", T, nullable=False, server_default="adhoc"),
    sa.CheckConstraint("trigger in ('adhoc', 'manual', 'schedule', 'backfill', 'recheck')", name="ck_crawl_runs_trigger"),
    sa.Column("request", JSONB, nullable=False), sa.Column("status", T, nullable=False),
    sa.Column("created_at", TZ, nullable=False), sa.Column("started_at", TZ), sa.Column("finished_at", TZ),
    sa.Column("claim_token", UUID(as_uuid=True)), sa.Column("lease_until", TZ),
    sa.Column("progress", JSONB, nullable=False), sa.Column("error", T),
    sa.CheckConstraint("status in ('queued', 'running', 'completed', 'failed')", name="ck_crawl_runs_status"))
crawl_articles = sa.Table("crawl_articles", metadata,
    sa.Column("crawl_id", UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("canonical_url", T, primary_key=True), sa.Column("outlet", T, nullable=False),
    sa.Column("url", T, nullable=False), sa.Column("status", T, nullable=False), sa.Column("reason", T),
    sa.Column("document_id", T, sa.ForeignKey("documents.id")), sa.Column("fetched", sa.Boolean, nullable=False),
    sa.Column("updated_at", TZ, nullable=False),
    sa.CheckConstraint("status in ('stored', 'existing', 'duplicate', 'rejected', 'error', 'updated', 'unchanged', 'removed', 'redirected')", name="ck_crawl_articles_status"))
crawl_sources = sa.Table("crawl_sources", metadata,
    sa.Column("crawl_id", UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("url", T, primary_key=True), sa.Column("site", T, nullable=False),
    sa.Column("kind", T, nullable=False), sa.Column("status", T, nullable=False), sa.Column("error", T),
    sa.CheckConstraint("kind in ('feed', 'sitemap')", name="ck_crawl_sources_kind"))

backfill_sitemaps = sa.Table("backfill_sitemaps", metadata,
    sa.Column("site_id", UUID(as_uuid=True), sa.ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("url", T, primary_key=True), sa.Column("depth", sa.Integer, nullable=False),
    sa.Column("lastmod", TZ), sa.Column("status", T, nullable=False), sa.Column("error", T),
    sa.Column("urls_found", sa.Integer, nullable=False, server_default="0"), sa.Column("scanned_at", TZ),
    sa.CheckConstraint("status in ('pending', 'done', 'skipped', 'error')", name="ck_backfill_sitemaps_status"))
sa.Index("ix_backfill_sitemaps_site_status", backfill_sitemaps.c.site_id, backfill_sitemaps.c.status)
backfill_urls = sa.Table("backfill_urls", metadata,
    sa.Column("site_id", UUID(as_uuid=True), sa.ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("canonical_url", T, primary_key=True), sa.Column("published_at", TZ), sa.Column("updated_at", TZ),
    sa.Column("title", T), sa.Column("sitemap_url", T), sa.Column("status", T, nullable=False), sa.Column("outcome", T),
    sa.Column("document_id", T, sa.ForeignKey("documents.id")),
    sa.Column("crawl_id", UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id")), sa.Column("done_at", TZ),
    sa.CheckConstraint("status in ('pending', 'done')", name="ck_backfill_urls_status"))
sa.Index("ix_backfill_urls_site_status_published", backfill_urls.c.site_id, backfill_urls.c.status, backfill_urls.c.published_at.desc())
