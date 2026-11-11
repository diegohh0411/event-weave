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


def test_site_recheck_backfilled_roundtrip(client):
    site = client.post('/sites', json={'domain': 'toggle.com', 'language': 'en'}).json()
    assert site['recheck_backfilled'] is True
    assert client.patch(f"/sites/{site['id']}", json={'recheck_backfilled': False}).json()['recheck_backfilled'] is False
    assert client.get(f"/sites/{site['id']}").json()['recheck_backfilled'] is False
    assert client.post('/sites', json={'domain': 'off.com', 'language': 'en', 'recheck_backfilled': False}).json()['recheck_backfilled'] is False


def test_site_rechecks_summary(client, engine):
    from datetime import datetime, timedelta, timezone
    from weave_core.schema import crawled_pages, page_rechecks
    site = client.post('/sites', json={'name': 'Sum', 'domain': 'sum.com', 'language': 'en'}).json()
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        for name, due, status in (('due', now - timedelta(hours=1), 'stored'), ('later', now + timedelta(days=3), 'stored'),
                                  ('done', None, 'stored'), ('gone', None, 'gone')):
            conn.execute(crawled_pages.insert().values(canonical_url=f'https://sum.com/{name}', url=f'https://sum.com/{name}',
                outlet='Sum', fetched_at=now, status=status, recheck_due_at=due))
        for name, outcome, age in (('done', 'unchanged', 1), ('done', 'edited', 2), ('gone', 'gone', 3), ('done', 'unchanged', 40)):
            conn.execute(page_rechecks.insert().values(canonical_url=f'https://sum.com/{name}', stage=0, outcome=outcome,
                checked_at=now - timedelta(days=age)))
    body = client.get(f"/sites/{site['id']}/rechecks").json()
    assert (body['due_now'], body['scheduled'], body['done']) == (1, 2, 2)
    assert body['by_outcome'] == {'unchanged': 1, 'edited': 1, 'gone': 1, 'redirected': 0, 'error': 0}
    assert body['last_checked_at'] is not None


def test_crawls_trigger_recheck_filter(client, engine):
    from datetime import datetime, timezone
    from uuid import UUID
    from weave_core.schema import crawl_runs
    site = client.post('/sites', json={'domain': 'trig.com', 'language': 'en'}).json()
    with engine.begin() as conn:
        conn.execute(crawl_runs.insert().values(id=uuid4(), site_id=UUID(site['id']), trigger='recheck', request={'sites': [{'domain': 'trig.com', 'language': 'en'}]},
                                                status='completed', created_at=datetime.now(timezone.utc), progress={}))
    body = client.get(f"/sites/{site['id']}/crawls", params={'trigger': 'recheck'}).json()
    assert [c['trigger'] for c in body['items']] == ['recheck']
