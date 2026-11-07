import pytest
from fastapi.testclient import TestClient

from weave_api.main import create_app


@pytest.mark.parametrize("origin", ["http://127.0.0.1:3000", "http://localhost:3000"])
def test_preflight_from_the_web_app_is_allowed(database_url, monkeypatch, origin):
    monkeypatch.setenv("WEAVE_DATABASE_URL", database_url)
    with TestClient(create_app()) as client:
        r = client.options("/runs", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin


def test_other_origins_get_no_allow_header(database_url, monkeypatch):
    monkeypatch.setenv("WEAVE_DATABASE_URL", database_url)
    with TestClient(create_app()) as client:
        r = client.options("/runs", headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
