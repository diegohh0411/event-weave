"""Startup contract: a cold app must not block on (or run) an external inference probe."""
from fastapi.testclient import TestClient

import weave_inference.probe as probe_module
from weave_api.routers import deployments as deployments_router

from weave_api.main import create_app


def _unreachable_catalog(tmp_path):
    """A catalog whose only default provider is openrouter with no credential: not ready, unreachable."""
    path = tmp_path / "inference.yaml"
    path.write_text(
        "models:\n"
        "  m-llm: {capability: llm}\n"
        "providers:\n"
        "  or: {kind: openrouter, base_url: https://openrouter.ai/api/v1, credential_env: OPENROUTER_API_KEY}\n"
        "deployments:\n"
        "  lfm2-d1@or: {model: m-llm, provider: or}\n"
        "defaults:\n"
        "  llm: lfm2-d1@or\n"
    )
    return path


def test_cold_start_does_not_probe_and_reports_not_ready_until_explicit_probe(database_url, tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVE_DATABASE_URL", database_url)
    monkeypatch.setenv("WEAVE_INFERENCE_FILE", str(_unreachable_catalog(tmp_path)))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    calls = []
    real_probe = probe_module.probe

    def recording_probe(catalog, env=None, transport=None, timeout=5.0):
        import os as _os
        result = real_probe(catalog, env=env if env is not None else _os.environ, transport=transport, timeout=timeout)
        calls.append(result)
        return result

    monkeypatch.setattr(probe_module, "probe", recording_probe)
    monkeypatch.setattr(deployments_router, "probe", recording_probe)

    with TestClient(create_app()) as client:
        # Health and the crawl routes answer without any blocking external probe.
        assert client.get("/health").json() == {"status": "ok", "schema": "0012_backfill"}
        created = client.post("/crawls", json={"sites": [{"domain": "example.com", "language": "en"}]}).json()
        assert client.get(f"/crawls/{created['id']}").json()["status"] == "queued"
        assert client.get("/crawls").json()["items"][0]["id"] == created["id"]

    # Constructing and starting the app performed no external probe at all.
    assert calls == []

    with TestClient(create_app()) as client:
        before = client.get("/deployments").json()["items"]
        assert before and all(not item["ready"] for item in before)
        # Until an explicit probe, a catalog deployment is reported not ready with a reason.
        assert all(item["reason"] == "not probed" for item in before), before

        after = client.post("/deployments/probe").json()["items"]
        assert len(after) == 1 and not after[0]["ready"]
        assert after[0]["reason"] == "credential missing: OPENROUTER_API_KEY", after
    assert len(calls) == 1


def test_cold_start_with_stub_catalog_is_ready_for_valid_inference(database_url, tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVE_DATABASE_URL", database_url)
    stub = tmp_path / "stub.yaml"
    stub.write_text(
        "models:\n"
        "  m-llm: {capability: llm}\n"
        "providers:\n"
        "  stub: {kind: stub, base_url: \"stub:\"}\n"
        "deployments:\n"
        "  stub-llm: {model: m-llm, provider: stub}\n"
    )
    monkeypatch.setenv("WEAVE_INFERENCE_FILE", str(stub))

    with TestClient(create_app()) as client:
        # No probe at startup: an explicit probe marks the stub deployment ready, and valid inference is permitted.
        assert client.post("/deployments/probe").status_code == 200
        assert client.get("/deployments").json()["items"][0]["ready"] is True
