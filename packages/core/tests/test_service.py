import json

from weave_core.service import build_service


def test_build_service_static_catalog(monkeypatch, database_url, tmp_path):
    deployments = tmp_path / 'deployments.yaml'
    deployments.write_text(json.dumps({'x@stub': ['extraction', True]}))
    monkeypatch.setenv('WEAVE_DATABASE_URL', database_url)
    monkeypatch.setenv('WEAVE_DEPLOYMENTS_FILE', str(deployments))
    monkeypatch.delenv('WEAVE_INFERENCE_FILE', raising=False)
    service = build_service(probe=False)
    try:
        assert service.catalog.is_ready('x@stub')
    finally:
        service.engine.dispose()


def _write_inference(tmp_path, text):
    path = tmp_path / 'inference.yaml'
    path.write_text(text)
    return path


def test_worker_ready_probe_is_bounded_and_nonfatal(monkeypatch, database_url, tmp_path):
    # A worker probes at startup with a bounded, nonfatal probe: an unreachable provider must not crash it.
    monkeypatch.setenv('WEAVE_DATABASE_URL', database_url)
    monkeypatch.setenv('WEAVE_INFERENCE_FILE', str(_write_inference(tmp_path,
        'models:\n  m-llm: {capability: llm}\n'
        'providers:\n  or: {kind: openrouter, base_url: https://openrouter.ai/api/v1, credential_env: OPENROUTER_API_KEY}\n'
        'deployments:\n  lfm2-d1@or: {model: m-llm, provider: or}\n'
        'defaults:\n  llm: lfm2-d1@or\n')))
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    service = build_service()
    try:
        assert service.catalog.is_ready('lfm2-d1@or') is False
    finally:
        service.engine.dispose()


def test_worker_ready_probe_permits_valid_inference(monkeypatch, database_url, tmp_path):
    monkeypatch.setenv('WEAVE_DATABASE_URL', database_url)
    monkeypatch.setenv('WEAVE_INFERENCE_FILE', str(_write_inference(tmp_path,
        'models:\n  m-llm: {capability: llm}\n'
        'providers:\n  stub: {kind: stub, base_url: "stub:"}\n'
        'deployments:\n  stub-llm: {model: m-llm, provider: stub}\n')))
    service = build_service()
    try:
        assert service.catalog.is_ready('stub-llm')
    finally:
        service.engine.dispose()
