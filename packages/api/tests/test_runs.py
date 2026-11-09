import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from weave_api.main import create_app
from weave_core.cache import cache_key
from weave_core.config import RunConfig, StaticCatalog, validate
from weave_core.registry import Registry
from weave_core.runs import RunService
from weave_core.schema import jobs, ledger, runs, stage_outputs
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.estimate import LedgerEstimator

FIXTURE = Path(__file__).resolve().parents[3] / "web" / "tests" / "fixtures" / "builder_run_config.json"
DEPLOYMENTS = {"stub-extraction": (Capability.extraction, True), "stub-embeddings": (Capability.embeddings, True)}


def config_json(**overrides) -> dict:
    cfg = json.loads(FIXTURE.read_text())
    cfg.update(overrides)
    return cfg


def make_service(engine, deployments=DEPLOYMENTS):
    registry = Registry()
    registry.load_entry_points()
    return RunService(engine, registry, StaticCatalog(deployments), LedgerEstimator(engine, registry))


@pytest.fixture
def client(engine):
    with TestClient(create_app(make_service(engine))) as c:
        yield c


def create(client, **overrides) -> str:
    r = client.post("/runs", json=config_json(**overrides))
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def seed_jobs(engine, run_id, stage, status, n, error=None):
    with engine.begin() as conn:
        for i in range(n):
            conn.execute(jobs.insert().values(
                run_id=run_id, stage=stage, unit_kind=UnitKind.mention, unit_id=f"{stage}-{status}-{i}", unit_content_hash="h",
                partition="p", order_key=[i], input_hash=f"in-{stage}-{status}-{i}", status=status, available_at=sa.func.now(),
                error=error, attempts=2 if error else 0))


def test_validate_returns_normalized_config_and_hash(client):
    r = client.post("/runs/validate", json=config_json())
    assert r.status_code == 200
    body = r.json()
    assert body["config"]["stages"]["cluster"]["version"] == "1"
    assert len(body["config_hash"]) == 64


def test_builder_hash_matches_cli(client, engine, database_url, tmp_path, monkeypatch):
    from weave_core.cli import app

    response = client.post("/runs", json=config_json())
    assert response.status_code == 201
    config_file = tmp_path / "run.yaml"
    config_file.write_text(yaml.safe_dump(config_json()))
    deployments_file = tmp_path / "deployments.yaml"
    deployments_file.write_text(yaml.safe_dump({k: [cap.value, ready] for k, (cap, ready) in DEPLOYMENTS.items()}))
    monkeypatch.setenv("WEAVE_DATABASE_URL", database_url)
    monkeypatch.setenv("WEAVE_DEPLOYMENTS_FILE", str(deployments_file))
    monkeypatch.delenv("WEAVE_INFERENCE_FILE", raising=False)
    result = CliRunner().invoke(app, ["run", "create", str(config_file)])
    assert result.exit_code == 0, result.output
    printed = next(line.split()[1] for line in result.output.splitlines() if line.startswith("hash "))
    assert printed == response.json()["config_hash"]


def test_create_with_unready_deployment_is_422(engine):
    unready = {**DEPLOYMENTS, "stub-embeddings": (Capability.embeddings, False)}
    with TestClient(create_app(make_service(engine, unready))) as c:
        r = c.post("/runs", json=config_json())
    assert r.status_code == 422
    assert any("is not ready" in p for p in r.json()["problems"])


def test_cancel_created_run_is_409(client):
    run_id = create(client)
    r = client.post(f"/runs/{run_id}/cancel")
    assert r.status_code == 409 and "detail" in r.json()


def test_unknown_run_is_404(client):
    assert client.get(f"/runs/{uuid4()}").status_code == 404
    assert client.get(f"/runs/{uuid4()}/failures").status_code == 404


def test_start_cancel_resume_return_run(client):
    run_id = create(client)
    assert client.post(f"/runs/{run_id}/start").json()["state"] == "running"
    assert client.post(f"/runs/{run_id}/cancel").json()["state"] == "cancelled"
    assert client.post(f"/runs/{run_id}/resume").json()["state"] == "running"
    assert client.post(f"/runs/{run_id}/start").status_code == 409


def test_list_is_newest_first_and_paged(client):
    first, second = create(client, name="first"), create(client, name="second")
    body = client.get("/runs", params={"limit": 1}).json()
    assert [i["id"] for i in body["items"]] == [second] and body["total"] == 2
    assert body["items"][0]["name"] == "second" and body["items"][0]["track"] == "paper"
    assert set(body["items"][0]) == {"id", "name", "track", "state", "config_hash", "created_at", "started_at", "finished_at"}
    assert client.get("/runs", params={"limit": 500}).status_code == 422
    assert first in [i["id"] for i in client.get("/runs").json()["items"]]


def test_progress_maps_labels(client, engine):
    run_id = create(client)
    for status, n in (("pending", 2), ("claimed", 1), ("done", 3), ("failed", 1)):
        seed_jobs(engine, run_id, Stage.embed, status, n)
    body = client.get(f"/runs/{run_id}").json()
    assert body["progress"] == {"embed": {"queued": 2, "running": 1, "done": 3, "failed": 1}}
    assert body["dollars"] == "0" or Decimal(body["dollars"]) == 0
    assert body["dollars_estimated"] is False and body["mentions_per_hour"] is None


def test_dollars_sum_ledger_and_flag_estimated(client, engine):
    run_id = create(client)
    with engine.begin() as conn:
        for cost, estimated in (("0.5", False), ("0.25", True)):
            conn.execute(ledger.insert().values(run_id=run_id, stage="enrich", adapter="clairic", deployment="stub-extraction", request_hash=uuid4().hex,
                                                cost=Decimal(cost), estimated=estimated, latency_ms=1, attempts=1, status="ok", created_at=sa.func.now()))
    body = client.get(f"/runs/{run_id}").json()
    assert Decimal(body["dollars"]) == Decimal("0.75") and body["dollars_estimated"] is True


def test_mentions_per_hour_uses_first_stage_after_extract(client, engine):
    run_id = create(client)
    seed_jobs(engine, run_id, Stage.extract, "done", 50)  # must not count
    seed_jobs(engine, run_id, Stage.enrich, "done", 6)  # enrich is the first stage after extract in this config
    seed_jobs(engine, run_id, Stage.embed, "done", 9)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(runs.update().where(runs.c.id == run_id).values(started_at=now - timedelta(hours=3), finished_at=now - timedelta(hours=1)))
    assert client.get(f"/runs/{run_id}").json()["mentions_per_hour"] == pytest.approx(3.0)


def test_failures_list_errors(client, engine):
    run_id = create(client)
    seed_jobs(engine, run_id, Stage.cluster, "failed", 2, error="boom")
    seed_jobs(engine, run_id, Stage.enrich, "failed", 1, error="bad json")
    seed_jobs(engine, run_id, Stage.enrich, "done", 4)
    everything = client.get(f"/runs/{run_id}/failures").json()
    assert everything["total"] == 3
    assert {"stage": "enrich", "unit_kind": "mention", "unit_id": "enrich-failed-0", "error": "bad json", "attempts": 2} in everything["items"]
    only = client.get(f"/runs/{run_id}/failures", params={"stage": "cluster", "limit": 1}).json()
    assert only["total"] == 2 and len(only["items"]) == 1 and only["items"][0]["error"] == "boom"


def test_estimate_without_prior_run_has_no_cache_counts(client):
    body = client.post("/runs/estimate", json=config_json()).json()
    assert body["amount"] is None and "no prior run" in body["reason"] and body["cached"] is None


def test_cache_coverage_counts_cached_keys(client, engine):
    prior = create(client)
    client.post(f"/runs/{prior}/start")
    service = make_service(engine)
    cfg = validate(RunConfig.model_validate(config_json()), service.registry, service.catalog)
    enrich = cfg.stages[Stage.enrich]
    spec = service.registry.get(Stage.enrich, enrich.adapter).spec
    seed_jobs(engine, prior, Stage.enrich, "done", 3)
    with engine.begin() as conn:
        conn.execute(stage_outputs.insert().values(
            cache_key=cache_key("in-enrich-done-1", spec, enrich.settings, enrich.deployments), stage="enrich", adapter=spec.name,
            adapter_version="1", output={}, emits=[], created_at=sa.func.now()))
    body = client.post("/runs/estimate", json=config_json()).json()
    assert body["cached"]["enrich"] == {"cached": 1, "total": 3}
    created = client.post("/runs", json=config_json()).json()
    assert created["estimate"]["cached"]["enrich"] == {"cached": 1, "total": 3}


def test_transitions_on_unknown_run_are_404(client):
    for action in ("start", "cancel", "resume"):
        assert client.post(f"/runs/{uuid4()}/{action}").status_code == 404


def test_coverage_reports_every_configured_stage(client, engine):
    prior = create(client)
    client.post(f"/runs/{prior}/start")
    seed_jobs(engine, prior, Stage.cluster, "done", 2)  # threshold clusterer is not cacheable
    cached = client.post("/runs/estimate", json=config_json()).json()["cached"]
    assert cached["cluster"] == {"cached": 0, "total": 2}
    assert cached["enrich"] == {"cached": 0, "total": 0}
    assert set(cached) == {"provide", "extract", "enrich", "embed", "cluster"}
