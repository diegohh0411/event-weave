import pytest
from fastapi.testclient import TestClient
from weave_api.main import create_app


@pytest.fixture
def client(service):
    with TestClient(create_app(service)) as client:
        yield client


def test_backfill_lifecycle_and_progress(client):
    site = client.post('/sites', json={'domain': 'example.com', 'language': 'en', 'backfill_mode': 'all'}).json()
    sid = site['id']
    assert site['backfill_mode'] == 'all'
    assert client.post(f'/sites/{sid}/backfill', json={'action': 'start'}).status_code == 200
    assert client.post(f'/sites/{sid}/backfill', json={'action': 'start'}).status_code == 409
    progress = client.get(f'/sites/{sid}/backfill').json()
    assert progress['state'] == 'running'
    assert progress['urls']['found'] == 0
    assert client.get('/sites').json()['items'][0]['backfill_progress'] == {'done': 0, 'found': 0}
    assert client.post(f'/sites/{sid}/backfill', json={'action': 'pause'}).status_code == 200
    assert client.post(f'/sites/{sid}/backfill', json={'action': 'resume'}).status_code == 200
    assert client.post(f'/sites/{sid}/backfill', json={'action': 'cancel'}).status_code == 200


def test_backfill_validation(client):
    base = {'domain': 'example.com', 'language': 'en'}
    assert client.post('/sites', json={**base, 'backfill_mode': 'date'}).status_code == 422
    assert client.post('/sites', json={**base, 'backfill_mode': 'date', 'backfill_from': '2999-01-01'}).status_code == 422
    sid = client.post('/sites', json=base).json()['id']
    assert client.post(f'/sites/{sid}/backfill', json={'action': 'start'}).status_code == 422
    assert client.patch(f'/sites/{sid}', json={'backfill_mode': 'date'}).status_code == 422


def test_backfill_disabled_and_crawl_filter(client):
    sid = client.post('/sites', json={'domain': 'example.com', 'language': 'en', 'backfill_mode': 'all'}).json()['id']
    client.patch(f'/sites/{sid}', json={'enabled': False})
    assert client.post(f'/sites/{sid}/backfill', json={'action': 'start'}).status_code == 409
    client.patch(f'/sites/{sid}', json={'enabled': True})
    assert client.post(f'/sites/{sid}/check').status_code == 202
    assert client.get(f'/sites/{sid}/crawls', params={'trigger': 'manual'}).json()['total'] == 1
    assert client.get(f'/sites/{sid}/crawls', params={'trigger': 'backfill'}).json()['total'] == 0
    assert client.get(f'/sites/{sid}/crawls', params={'trigger': 'invalid'}).status_code == 422


def test_settings_changes_preserve_frontier_until_start(client, engine):
    import sqlalchemy as sa
    from weave_core.schema import backfill_sitemaps, sites

    sid = client.post('/sites', json={'domain': 'example.com', 'language': 'en', 'backfill_mode': 'all'}).json()['id']
    client.post(f'/sites/{sid}/backfill', json={'action': 'start'})
    assert client.patch(f'/sites/{sid}', json={'backfill_mode': 'date', 'backfill_from': '2020-01-01'}).status_code == 409
    client.post(f'/sites/{sid}/backfill', json={'action': 'pause'})
    assert client.patch(f'/sites/{sid}', json={'backfill_mode': 'date', 'backfill_from': '2020-01-01'}).status_code == 409
    with engine.begin() as conn:
        conn.execute(sites.update().where(sites.c.id == sid).values(backfill_state='done'))
        conn.execute(backfill_sitemaps.insert().values(site_id=sid, url='https://example.com/sitemap.xml', depth=0, status='done'))
    assert client.patch(f'/sites/{sid}', json={'backfill_mode': 'date', 'backfill_from': '2020-01-01'}).status_code == 200
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(backfill_sitemaps)) == 1
    assert client.post(f'/sites/{sid}/backfill', json={'action': 'start'}).status_code == 200
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(backfill_sitemaps)) == 0
