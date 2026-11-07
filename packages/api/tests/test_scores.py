from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from weave_api.deps import get_paper
from weave_api.main import create_app
from weave_core.config import RunConfig
from weave_eval.compare import ScoreRow
from weave_eval.evaluate import Evaluation
from weave_eval.paper import load_paper
from weave_eval.store import save

from test_runs import config_json, make_service


@pytest.fixture
def client(engine):
    app = create_app(make_service(engine))
    with TestClient(app) as c:
        c.app = app
        yield c


def make_run(client, name, track="paper") -> str:
    cfg = config_json(name=name, track=track)
    if track == "realistic":
        cfg["stages"]["extract"] = {"adapter": "gold", "settings": {}, "deployments": {}}
    r = client.post("/runs", json=cfg)
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def evaluate(engine, run_id, track, rows, report=None, split="test"):
    base = {"config_hash": "c", "gold_hash": "g", "scorer_version": "sv1", "seed": 1, "resamples": 10, "track": track}
    save(engine, Evaluation(uuid4(), run_id, track, split, rows, {**base, **(report or {})}))


EN_WITHIN = ScoreRow("paper", "within", "en", "conll", 80.0, 70.0, 74.5, 70.0, 79.0)
POOLED = ScoreRow("realistic", "pooled", "all", "conll", 60.0, 50.0, 54.5, 50.0, 58.0)


def test_scores_404_when_not_evaluated(client):
    run_id = make_run(client, "a")
    r = client.get(f"/runs/{run_id}/scores")
    assert r.status_code == 404
    assert r.json()["detail"] == f"run {run_id} has no test evaluation; run weave eval {run_id}"


def test_scores_returns_latest(client, engine):
    run_id = make_run(client, "a")
    evaluate(engine, run_id, "paper", [EN_WITHIN], {"extra": 1})
    body = client.get(f"/runs/{run_id}/scores").json()
    assert body["split"] == "test" and body["track"] == "paper" and body["scorer_version"] == "sv1"
    assert body["scores"][0]["f1"] == 74.5 and body["scores"][0]["f1_low"] == 70.0
    assert body["report"]["extra"] == 1 and body["created_at"] and body["evaluation_id"]
    assert client.get(f"/runs/{run_id}/scores?split=dev").status_code == 404


def test_compare_paper_rows_only_with_track1(client, engine):
    real = make_run(client, "real", "realistic")
    paper = make_run(client, "pap")
    evaluate(engine, real, "realistic", [POOLED], {"track2": {"trigger": {"f1": 1.0}}})
    evaluate(engine, paper, "paper", [EN_WITHIN])
    only = client.get("/compare", params={"run_id": [real]}).json()
    assert only["paper"] is None
    assert only["runs"][0]["trigger"] == {"f1": 1.0}
    assert set(only["runs"][0]["cost"]) == {"dollars_per_1000_mentions", "mentions_per_hour", "total_dollars", "any_estimated"}
    mixed = client.get("/compare", params={"run_id": [real, paper]}).json()
    assert [r["track"] for r in mixed["runs"]] == ["realistic", "paper"]
    rows = mixed["paper"]
    assert rows and len({(r["language"], r["setting"], r["metric"], r["regime"]) for r in rows}) == len(rows)
    mine = [r for r in rows if r["language"] == "en" and r["setting"] == "within" and r["metric"] == "conll" and r["regime"] == "monolingual"]
    assert mine[0]["ours"]["f1"] == 74.5 and mine[0]["ours_ci"] == [70.0, 79.0]
    assert all("realistic" not in str(r) for r in rows)


def test_compare_unevaluated_run_marked(client):
    run_id = make_run(client, "a")
    body = client.get("/compare", params={"run_id": [run_id]}).json()
    assert body["runs"][0]["evaluated"] is False and body["runs"][0]["scores"] == []
    assert body["runs"][0]["trigger"] is None


def test_hindi_cross_skipped_reason(client, engine):
    run_id = make_run(client, "a")
    evaluate(engine, run_id, "paper", [ScoreRow("paper", "within", "hi", "conll", 1.0, 1.0, 1.0)])
    body = client.get("/compare", params={"run_id": [run_id]}).json()
    reason = body["runs"][0]["skipped"]["hi/cross"]
    assert "Hindi" in reason
    hi = [r for r in body["paper"] if r["language"] == "hi" and r["setting"] == "cross"]
    assert all(r["skipped"] == reason and r["ours"] is None for r in hi)


def test_paper_checked_flag(client, engine, tmp_path):
    run_id = make_run(client, "a")
    data = yaml.safe_load(open(load_paper.__globals__["DEFAULT_PATH"]))
    assert len(set(data["checked_by"])) == 1
    path = tmp_path / "paper.yaml"
    path.write_text(yaml.safe_dump(data))
    client.app.dependency_overrides[get_paper] = lambda: load_paper(path)
    body = client.get("/compare", params={"run_id": [run_id]}).json()
    assert body["paper_checked"] is False and body["paper_source"]["anthology_id"]
    data["checked_by"] = ["a", "b"]
    path.write_text(yaml.safe_dump(data))
    assert client.get("/compare", params={"run_id": [run_id]}).json()["paper_checked"] is True


def test_compare_limits_and_unknown(client):
    ids = [str(uuid4()) for _ in range(9)]
    assert client.get("/compare", params={"run_id": ids}).status_code == 422
    assert client.get("/compare").status_code == 422
    assert client.get("/compare", params={"run_id": ids[:1]}).status_code == 404
