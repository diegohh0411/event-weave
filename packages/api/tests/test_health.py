from fastapi.testclient import TestClient

from weave_api.main import create_app


def test_health_reports_schema(database_url, monkeypatch):
    monkeypatch.setenv("WEAVE_DATABASE_URL", database_url)
    with TestClient(create_app()) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok" and body["schema"] == "0003_providers"
