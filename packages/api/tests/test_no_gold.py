"""Contract guard: gold data only leaves the API through score reports."""
import ast
import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from weave_api.main import create_app
from weave_core.schema import (cluster_decisions, cluster_members, clusters, documents, gold_documents, gold_mentions,
                               gold_pairs, jobs, mention_tags, mentions, runs, stage_outputs)

MARKER = "GOLD_MARKER_7f3a"
FORBIDDEN_KEYS = {"gold", "topic", "split"}
NOW = datetime.now(timezone.utc)
TEXT = "Rebels seized the town. Troops retook it."
EXEMPT = ("/runs/{run_id}/scores", "/compare")  # score reports are the one place gold may appear


def _walk_keys(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}/{key}", key
            yield from _walk_keys(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_keys(value, f"{path}/{i}")


@pytest.fixture
def seeded(engine):
    run = uuid4()
    with engine.begin() as conn:
        conn.execute(runs.insert().values(id=run, config={"name": "r", "track": "realistic", "seed": 0, "stages": {}}, config_hash="h", state="done", created_at=NOW))
        conn.execute(documents.insert().values(id="d1", provider="p", external_id="d1", language="en", title="t", byline="b",
                                               outlet="o", url="u", text=TEXT, token_offsets=[], sentence_offsets=[[0, 23], [24, 40]],
                                               content_hash="h"))
        for mid, start, word in (("m1", 0, "Rebels"), ("m2", 24, "Troops")):
            conn.execute(mentions.insert().values(id=mid, document_id="d1", start=start, end=start + len(word), tok_start=0, tok_end=1,
                                                  sentence=0 if start == 0 else 1, text=word, extractor="x", extractor_version="1"))
            conn.execute(mention_tags.insert().values(mention_id=mid, tagger="t", tagger_version="1", deployment="d",
                                                      significance=0.5, event_type="attack"))
        conn.execute(jobs.insert().values(run_id=run, stage="extract", unit_kind="document", unit_id="d1", unit_content_hash="u",
                                          partition="p", order_key=[0], input_hash="i", status="done", available_at=NOW))
        conn.execute(jobs.insert().values(run_id=run, stage="enrich", unit_kind="mention", unit_id="m1", unit_content_hash="u",
                                          partition="p", order_key=[0], input_hash="i", status="done", available_at=NOW, cache_key="k1"))
        conn.execute(jobs.insert().values(run_id=run, stage="enrich", unit_kind="mention", unit_id="m2", unit_content_hash="u",
                                          partition="p", order_key=[1], input_hash="i", status="failed", available_at=NOW, error="boom"))
        conn.execute(stage_outputs.insert().values(
            cache_key="k1", stage="enrich", adapter="enr", adapter_version="1", emits={}, created_at=NOW,
            output={"enrichment": {"action": {"text": "seize"}, "participants": [], "location": None, "time": None}, "discarded": {}}))
        conn.execute(clusters.insert().values(id="c1", run_id=run, partition="p", card={"action": "seize"}, card_hash="x", size=1,
                                              created_order=[0]))
        conn.execute(cluster_members.insert().values(run_id=run, mention_id="m1", cluster_id="c1", partition="p", order_key=[0, 0]))
        conn.execute(cluster_decisions.insert().values(
            run_id=run, mention_id="m1", adapter="a", adapter_version="1", deployment="dep",
            candidates=[], noul=0.1, probabilities={"A": 0.9}, outcome="new", reason="r", cluster_id="c1"))
        conn.execute(gold_documents.insert().values(document_id="d1", language="en", topic=MARKER, split=MARKER, doc_index=0))
        conn.execute(gold_mentions.insert().values(document_id="d1", mention_id=f"{MARKER}-m", start=0, end=6, text=f"{MARKER} text"))
        conn.execute(gold_pairs.insert().values(language="en", kind="within", split=MARKER, topic=MARKER, source_document_id="d1",
                                                source_mention_id=f"{MARKER}-a", target_document_id="d1",
                                                target_mention_id=f"{MARKER}-b", label=1))
    return run


def test_no_gold_outside_scores(service, seeded):
    app = create_app(service)
    fill = {"run_id": str(seeded), "cluster_id": "c1", "document_id": "d1"}
    called = []
    with TestClient(app) as client:
        for path, item in app.openapi()["paths"].items():
            if "get" not in item or path.startswith(EXEMPT):
                continue
            url = path.format(**fill)
            response = client.get(url)
            assert response.status_code == 200, (url, response.text)
            assert MARKER not in response.text, f"gold marker leaked from {url}"
            for where, key in _walk_keys(response.json()):
                assert key not in FORBIDDEN_KEYS, f"forbidden key {key!r} at {url} {where}"
            called.append(path)
    for expected in ("/runs", "/runs/{run_id}/clusters", "/runs/{run_id}/documents/{document_id}", "/runs/{run_id}/enrichments/sample",
                     "/adapters", "/deployments", "/runs/{run_id}/clusters/{cluster_id}/members"):
        assert expected in called


def test_no_weave_api_module_imports_gold_tables():
    root = Path(next(iter(importlib.util.find_spec("weave_api").submodule_search_locations)))
    offenders = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names] + [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            if any(re.search(r"\bgold_", n) for n in names):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    # scores.py reads score reports via weave_eval; it must still not touch gold tables.
    assert offenders == []
