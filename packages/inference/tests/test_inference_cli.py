import json

from typer.testing import CliRunner
import sqlalchemy as sa
from weave_core.schema import ledger

from weave_inference.cli import inference_app

CATALOG = """
models:
  m-s1: {capability: system_one}
  m-llm: {capability: llm}
providers:
  stub: {kind: stub, base_url: "stub://"}
  or: {kind: openrouter, base_url: "https://openrouter.ai/api/v1", credential_env: OPENROUTER_API_KEY}
deployments:
  stub-s1: {model: m-s1, provider: stub}
  or-llm: {model: m-llm, provider: or}
"""


def test_probe_cli_prints_missing_credential(tmp_path, monkeypatch):
    f = tmp_path / "c.yaml"
    f.write_text(CATALOG)
    monkeypatch.setenv("WEAVE_INFERENCE_FILE", str(f))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    r = CliRunner().invoke(inference_app, ["probe"])
    assert r.exit_code == 0
    assert "or-llm\tllm\tnot ready: credential missing: OPENROUTER_API_KEY" in r.stdout
    assert "stub-s1\tsystem_one\tready" in r.stdout


def test_smoke_with_stub_writes_null_run_ledger_row(tmp_path, monkeypatch, engine):
    f = tmp_path / "c.yaml"
    f.write_text(CATALOG)
    monkeypatch.setenv("WEAVE_INFERENCE_FILE", str(f))
    monkeypatch.setenv("WEAVE_DATABASE_URL", engine.url.render_as_string(hide_password=False))
    r = CliRunner().invoke(inference_app, ["smoke", "stub-s1"])
    assert r.exit_code == 0, r.output
    assert "cost" in r.stdout
    with engine.connect() as conn:
        row = conn.execute(sa.select(ledger)).one()
    assert row.run_id is None and row.deployment == "stub-s1" and row.adapter == "smoke" and row.status == "ok"


def test_smoke_unknown_deployment_exits_1(tmp_path, monkeypatch, engine):
    f = tmp_path / "c.yaml"
    f.write_text(CATALOG)
    monkeypatch.setenv("WEAVE_INFERENCE_FILE", str(f))
    monkeypatch.setenv("WEAVE_DATABASE_URL", engine.url.render_as_string(hide_password=False))
    assert CliRunner().invoke(inference_app, ["smoke", "nope"]).exit_code == 1


def test_core_cli_still_works_without_inference_installed(monkeypatch, tmp_path):
    import sys
    from weave_core import cli as core_cli
    monkeypatch.setitem(sys.modules, "weave_inference.cli", None)
    monkeypatch.setitem(sys.modules, "weave_inference.estimate", None)
    monkeypatch.setenv("WEAVE_INFERENCE_FILE", str(tmp_path / "x.yaml"))
    monkeypatch.setenv("WEAVE_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:5432/none")
    service = core_cli._service()
    from weave_core.runs import NoEstimate
    from weave_core.config import StaticCatalog
    assert isinstance(service.estimator, NoEstimate) and isinstance(service.catalog, StaticCatalog)
    service.engine.dispose()
