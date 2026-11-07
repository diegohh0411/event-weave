from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from weave_api.main import create_app
from weave_core.schema import cluster_members, clusters, documents, jobs, mentions, runs, stage_outputs

NOW = datetime.now(timezone.utc)
CONFIG = {"stages": {"provide": {}, "extract": {}, "enrich": {}, "cluster": {}}}
TEXT = "Rebels seized the town. Troops retook it."


def _job(conn, run, stage, kind, unit, status="done", cache_key=None):
    conn.execute(jobs.insert().values(run_id=run, stage=stage, unit_kind=kind, unit_id=unit, unit_content_hash="u", partition="p",
                                      order_key=[0], input_hash="i", status=status, available_at=NOW, cache_key=cache_key))


def _doc(conn, did, language="en", title=None):
    conn.execute(documents.insert().values(id=did, provider="p", external_id=did, language=language, title=title or f"title {did}",
                                           byline="by", outlet="out", url="u", text=TEXT, token_offsets=[], sentence_offsets=[],
                                           content_hash="h"))


def _mention(conn, mid, did, start, word):
    conn.execute(mentions.insert().values(id=mid, document_id=did, start=start, end=start + len(word), tok_start=0, tok_end=1,
                                          sentence=0, text=word, extractor="x", extractor_version="1"))


def _enrich(conn, run, mid, enricher, action):
    key = f"k-{run}-{mid}"
    conn.execute(stage_outputs.insert().values(cache_key=key, stage="enrich", adapter=enricher, adapter_version="1", emits={},
                                               created_at=NOW, output={"enrichment": {"action": {"text": action}, "participants": [],
                                                                                      "location": None, "time": None}, "discarded": {}}))
    _job(conn, run, "enrich", "mention", mid, cache_key=key)


@pytest.fixture
def seeded(engine):
    run, other = uuid4(), uuid4()
    with engine.begin() as conn:
        for r in (run, other):
            conn.execute(runs.insert().values(id=r, config=CONFIG, config_hash="h", state="done", created_at=NOW))
        _doc(conn, "d1")
        _doc(conn, "d2", "es", "otro")
        _doc(conn, "d3")  # not in the run
        _mention(conn, "m-late", "d1", 24, "Troops")
        _mention(conn, "m-early", "d1", 0, "Rebels")
        _mention(conn, "m-other", "d1", 7, "seized")  # only in the other run
        for r in (run, other):
            _job(conn, r, "extract", "document", "d1")
        _job(conn, run, "extract", "document", "d2")
        _job(conn, run, "enrich", "mention", "m-late", status="failed")
        _job(conn, other, "enrich", "mention", "m-other")
        clusters_ = [(run, "c1", ["m-early"])]
        for r, cid, members in clusters_:
            conn.execute(clusters.insert().values(id=cid, run_id=r, partition="p", card={}, card_hash="x", size=3, created_order=[0]))
            for i, m in enumerate(members):
                conn.execute(cluster_members.insert().values(run_id=r, mention_id=m, cluster_id=cid, partition="p", order_key=[0, i]))
        _enrich(conn, run, "m-early", "enr-a", "seize")
        _enrich(conn, other, "m-early", "enr-b", "other-run")
    return run, other


@pytest.fixture
def api(service):
    with TestClient(create_app(service)) as c:
        yield c


def test_document_list_scoped_and_filtered(api, seeded):
    run, _ = seeded
    body = api.get(f"/runs/{run}/documents").json()
    assert sorted(d["id"] for d in body["items"]) == ["d1", "d2"] and body["total"] == 2
    d1 = next(d for d in body["items"] if d["id"] == "d1")
    assert d1["mentions"] == 2 and d1["outlet"] == "out" and d1["title"] == "title d1"
    assert [d["id"] for d in api.get(f"/runs/{run}/documents", params={"language": "es"}).json()["items"]] == ["d2"]
    assert [d["id"] for d in api.get(f"/runs/{run}/documents", params={"q": "OTRO"}).json()["items"]] == ["d2"]
    assert api.get(f"/runs/{run}/documents", params={"limit": 500}).status_code == 422


def test_document_mentions_scoped_to_run(api, seeded):
    run, _ = seeded
    body = api.get(f"/runs/{run}/documents/d1").json()
    assert body["text"] == TEXT and body["byline"] == "by"
    assert [(m["id"], m["start"], m["end"]) for m in body["mentions"]] == [("m-early", 0, 6), ("m-late", 24, 30)]


def test_unclustered_mention_has_null_cluster(api, seeded):
    run, _ = seeded
    early, late = api.get(f"/runs/{run}/documents/d1").json()["mentions"]
    assert early["cluster_id"] == "c1" and early["cluster_size"] == 3
    assert late["cluster_id"] is None and late["cluster_size"] is None and late["enrichment"] is None


def test_enrichment_is_the_runs_own(api, seeded):
    run, other = seeded
    mine = api.get(f"/runs/{run}/documents/d1").json()["mentions"][0]["enrichment"]
    assert mine["action"] == {"text": "seize"} and set(mine) >= {"action", "participants", "location", "time"}
    theirs = api.get(f"/runs/{other}/documents/d1").json()["mentions"]
    assert [m["id"] for m in theirs] == ["m-early", "m-other"]
    assert theirs[0]["enrichment"]["action"] == {"text": "other-run"} and theirs[0]["cluster_id"] is None


def test_unknown_document_404(api, seeded):
    run, _ = seeded
    assert api.get(f"/runs/{run}/documents/nope").status_code == 404
    assert api.get(f"/runs/{run}/documents/d3").status_code == 404
    assert api.get(f"/runs/{uuid4()}/documents/d1").status_code == 404
    assert api.get(f"/runs/{uuid4()}/documents").status_code == 404
