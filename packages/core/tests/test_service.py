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
