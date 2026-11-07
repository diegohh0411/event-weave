from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from weave_api.main import create_app
from weave_core.schema import documents, jobs, mentions, runs, stage_outputs

NOW = datetime.now(timezone.utc)
TEXT = "Rebels seized the town."


@pytest.fixture
def run(engine):
    run, other = uuid4(), uuid4()
    with engine.begin() as conn:
        for r in (run, other):
            conn.execute(runs.insert().values(id=r, config={}, config_hash="h", state="done", created_at=NOW))
        conn.execute(documents.insert().values(id="d", provider="p", external_id="d", language="en", title="t", byline="", outlet="",
                                               url="", text=TEXT, token_offsets=[], sentence_offsets=[[0, len(TEXT)]], content_hash="h"))
        for i in range(12):
            enricher = "enr-a" if i % 3 else "enr-b"
            conn.execute(mentions.insert().values(id=f"m{i}", document_id="d", start=0, end=6, tok_start=0, tok_end=1, sentence=0,
                                                  text="Rebels", extractor="x", extractor_version="1"))
            conn.execute(stage_outputs.insert().values(
                cache_key=f"k{i}", stage="enrich", adapter=enricher, adapter_version="1", emits={}, created_at=NOW,
                output={"enrichment": {"action": {"text": "seize"}}, "discarded": {}}))
            conn.execute(jobs.insert().values(run_id=run, stage="enrich", unit_kind="mention", unit_id=f"m{i}", unit_content_hash="u",
                                              partition="p", order_key=[i], input_hash="i", status="done", available_at=NOW, cache_key=f"k{i}"))
        conn.execute(jobs.insert().values(run_id=other, stage="enrich", unit_kind="mention", unit_id="m0", unit_content_hash="u",
                                          partition="p", order_key=[0], input_hash="i", status="done", available_at=NOW, cache_key="k0"))
    return run


@pytest.fixture
def api(service):
    with TestClient(create_app(service)) as c:
        yield c


def keys(resp):
    assert resp.status_code == 200, resp.text
    return [i["id"] for i in resp.json()["items"]]


def test_sample_is_seeded_and_filtered(api, run):
    url = f"/runs/{run}/enrichments/sample"
    first = keys(api.get(url, params={"limit": 5, "seed": 1}))
    assert len(first) == 5 and first == keys(api.get(url, params={"limit": 5, "seed": 1}))
    assert first != keys(api.get(url, params={"limit": 5, "seed": 2}))
    # offset slices the same seeded order
    assert keys(api.get(url, params={"limit": 3, "offset": 2, "seed": 1})) == keys(api.get(url, params={"limit": 12, "seed": 1}))[2:5]
    only_b = api.get(url, params={"limit": 50, "enricher": "enr-b"}).json()
    assert only_b["total"] == len(only_b["items"]) == 4 and {i["enricher"] for i in only_b["items"]} == {"enr-b"}
    item = only_b["items"][0]
    assert "[[" in item["sentence"] or "Rebels" in item["sentence"]
    assert set(item) == {"id", "mention_id", "document_id", "enricher", "enricher_version", "sentence", "enrichment", "discarded"}


def test_sample_validation_and_unknown_run(api, run):
    assert api.get(f"/runs/{run}/enrichments/sample", params={"limit": 500}).status_code == 422
    assert api.get(f"/runs/{uuid4()}/enrichments/sample").status_code == 404
