import sqlalchemy as sa
from weave_core.db import make_engine, migrate


def test_migrate_creates_core_tables(database_url):
    engine = make_engine(database_url)
    names = set(sa.inspect(engine).get_table_names())
    assert {"runs", "documents", "jobs", "stage_outputs"} <= names


def test_vector_extension_installed(database_url):
    with make_engine(database_url).connect() as c:
        assert c.execute(sa.text("select 1 from pg_extension where extname='vector'")).scalar() == 1


def test_migrate_is_idempotent(database_url):
    assert migrate(database_url) == migrate(database_url)


def test_migration_creates_ledger_and_run_deployments(database_url):
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import weave_core
    engine = make_engine(database_url)
    insp = sa.inspect(engine)
    ledger = {c["name"]: c for c in insp.get_columns("ledger")}
    assert set(ledger) == {"id", "run_id", "stage", "adapter", "deployment", "request_hash",
                           "input_tokens", "output_tokens", "cached_tokens", "cost", "estimated",
                           "latency_ms", "attempts", "status", "error", "created_at"}
    assert ledger["run_id"]["nullable"] and not ledger["deployment"]["nullable"]
    assert not ledger["cost"]["nullable"] and ledger["cost"]["type"].precision == 14 and ledger["cost"]["type"].scale == 8
    idx = {tuple(i["column_names"]) for i in insp.get_indexes("ledger")}
    assert {("run_id", "stage"), ("adapter", "deployment")} <= idx
    rd = {c["name"] for c in insp.get_columns("run_deployments")}
    assert rd == {"run_id", "deployment", "ready", "reason", "server_version", "reported_model_id", "probed_at"}
    assert insp.get_pk_constraint("run_deployments")["constrained_columns"] == ["run_id", "deployment"]
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(weave_core.__file__).parent / "migrations"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0005_enrichments"]


def test_documents_have_order_key_and_no_gold_columns(database_url):
    cols = {c["name"] for c in sa.inspect(make_engine(database_url)).get_columns("documents")}
    assert "order_key" in cols
    assert not cols & {"topic", "split", "gold", "label"}


def test_gold_and_crawl_tables_exist(database_url):
    insp = sa.inspect(make_engine(database_url))
    assert {c["name"] for c in insp.get_columns("gold_documents")} == {"document_id", "language", "topic", "split", "doc_index"}
    assert {c["name"] for c in insp.get_columns("gold_mentions")} == {"document_id", "mention_id", "start", "end", "text"}
    assert insp.get_pk_constraint("gold_mentions")["constrained_columns"] == ["document_id", "mention_id"]
    assert {c["name"] for c in insp.get_columns("gold_pairs")} == {
        "id", "language", "kind", "split", "topic", "source_document_id", "source_mention_id",
        "target_document_id", "target_mention_id", "label"}
    assert {c["name"] for c in insp.get_columns("crawled_pages")} == {
        "canonical_url", "url", "outlet", "fetched_at", "status", "reason", "raw_html_gz", "document_id"}


def test_mentions_tables_exist(database_url):
    insp = sa.inspect(make_engine(database_url))
    assert {c["name"] for c in insp.get_columns("mentions")} == {
        "id", "document_id", "start", "end", "tok_start", "tok_end", "sentence", "text",
        "extractor", "extractor_version", "deployment", "probability"}
    assert ("document_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("mentions")}
    assert {c["name"] for c in insp.get_columns("mention_tags")} == {
        "mention_id", "tagger", "tagger_version", "deployment", "significance", "event_type", "event_type_probs"}
    assert insp.get_pk_constraint("mention_tags")["constrained_columns"] == [
        "mention_id", "tagger", "tagger_version", "deployment"]


def test_enrichments_table_exists(database_url):
    insp = sa.inspect(make_engine(database_url))
    assert {c["name"] for c in insp.get_columns("enrichments")} == {
        "id", "mention_id", "enricher", "enricher_version", "deployment",
        "action", "participants", "location", "time", "discarded"}
    assert ("mention_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("enrichments")}
    assert {c["name"]: c["nullable"] for c in insp.get_columns("enrichments")}["mention_id"] is False
    assert insp.get_foreign_keys("enrichments")[0]["referred_table"] == "mentions"
