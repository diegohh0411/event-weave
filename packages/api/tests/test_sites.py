from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from weave_api.main import create_app


@pytest.fixture
def client(service):
    with TestClient(create_app(service)) as c:
        yield c


def test_site_lifecycle_and_checks(client):
    missing = str(uuid4())
    for suffix in ('', '/crawls', '/check'):
        response = client.post(f'/sites/{missing}{suffix}') if suffix == '/check' else client.get(f'/sites/{missing}{suffix}')
        assert response.status_code == 404
    assert client.patch(f'/sites/{missing}', json={'enabled': False}).status_code == 404
    assert client.delete(f'/sites/{missing}').status_code == 404
    body = {'name': 'Example', 'domain': ' Example.COM. ', 'language': 'en'}
    response = client.post('/sites', json=body)
    assert response.status_code == 201
    site = response.json()
    assert site['domain'] == 'example.com'
    sid = site['id']
    assert client.post('/sites', json=body).status_code == 409
    assert client.get(f'/sites/{sid}').json()['id'] == sid
    assert client.get('/sites').json()['items'][0]['id'] == sid
    assert client.patch(f'/sites/{sid}', json={'check_every_minutes': 10}).json()['check_every_minutes'] == 10
    assert client.patch(f'/sites/{sid}', json={'max_parallel': 5}).status_code == 422
    check = client.post(f'/sites/{sid}/check')
    assert check.status_code == 202 and check.json()['trigger'] == 'manual'
    assert client.post(f'/sites/{sid}/check').status_code == 409
    assert client.get(f'/sites/{sid}/crawls').json()['total'] == 1
    assert client.delete(f'/sites/{sid}').status_code == 409
    other = client.post('/sites', json={'domain': 'other.com', 'language': 'en'}).json()
    assert client.delete(f"/sites/{other['id']}").status_code == 204


@pytest.mark.parametrize('payload', [
    {'domain': '127.0.0.1', 'language': 'en'},
    {'domain': 'example.com', 'language': 'en', 'feeds': ['http://localhost/x']},
    {'domain': 'example.com', 'language': 'en', 'check_every_minutes': 4},
    {'domain': 'example.com', 'language': 'en', 'daily_cap': 0},
    {'domain': 'example.com', 'language': 'en', 'min_interval_seconds': 0.1},
])
def test_invalid_site(client, payload):
    assert client.post('/sites', json=payload).status_code == 422


def test_check_handles_deleted_queued_crawl(client, monkeypatch):
    monkeypatch.setattr('weave_api.routers.sites.queue_check', lambda engine, site_id: uuid4())
    assert client.post(f'/sites/{uuid4()}/check').status_code == 404


def test_patch_per_day_bounds(client):
    sid = client.post('/sites', json={'domain': 'bounds.com', 'language': 'en'}).json()['id']
    for value in (0, 201):
        assert client.patch(f'/sites/{sid}', json={'per_day': value}).status_code == 422
    for value in (1, 200, None):
        assert client.patch(f'/sites/{sid}', json={'per_day': value}).status_code == 200


def test_list_sites_batches_queries(client, engine):
    import sqlalchemy as sa
    for i in range(3):
        sid = client.post('/sites', json={'domain': f'site{i}.com', 'language': 'en'}).json()['id']
        assert client.post(f'/sites/{sid}/check').status_code == 202
    statements = []
    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    sa.event.listen(engine, 'before_cursor_execute', count)
    try:
        response = client.get('/sites')
    finally:
        sa.event.remove(engine, 'before_cursor_execute', count)
    assert response.status_code == 200
    assert len(response.json()['items']) == 3
    assert all(site['last_crawl'] for site in response.json()['items'])
    assert len(statements) <= 5
