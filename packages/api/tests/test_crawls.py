from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from weave_api.main import create_app
from weave_core.schema import documents
from weave_providers.crawl.jobs import claim_crawl, finish_crawl, record_article, record_source


@pytest.fixture
def client(service):
    with TestClient(create_app(service)) as c:
        yield c


def create(client):
    response = client.post('/crawls', json={'sites': [{'domain': ' Example.COM. ', 'language': 'en'}]})
    assert response.status_code == 202, response.text
    return response.json()


def test_create_enqueues_normalized_request_without_running_crawl(client, engine):
    body = create(client)
    assert body['request'] == {'sites': [{'domain': 'example.com', 'name': 'example.com', 'language': 'en',
                                           'per_day': 3, 'feeds': [], 'sitemaps': []}], 'days': 1}
    assert body['status'] == 'queued'
    assert body['started_at'] is None and body['finished_at'] is None and body['error'] is None
    assert body['counters'] == {'discovered': 0, 'fetched': 0, 'stored': 0, 'existing': 0,
                                'duplicates': 0, 'rejected': 0, 'errors': 0, 'reasons': {}}
    assert client.get(f"/crawls/{body['id']}").json() == body
    assert client.get('/crawls').json()['items'] == [body]


def test_create_response_stays_queued_even_if_worker_claims_immediately(client, engine, monkeypatch):
    from weave_api.routers import crawls

    enqueue = crawls.create_crawl

    def enqueue_and_claim(*args, **kwargs):
        id = enqueue(*args, **kwargs)
        assert claim_crawl(engine, datetime.now(timezone.utc)).id == id
        return id

    monkeypatch.setattr(crawls, 'create_crawl', enqueue_and_claim)
    body = create(client)
    assert body['status'] == 'queued' and body['started_at'] is None
    assert client.get(f"/crawls/{body['id']}").json()['status'] == 'running'


@pytest.mark.parametrize('payload', [
    {}, {'sites': []}, {'sites': [{'domain': 'example.com', 'language': 'en'}] * 21},
    {'sites': [{'domain': 'example.com', 'language': 'en'}], 'days': 0},
    {'sites': [{'domain': 'example.com', 'language': 'en'}], 'days': 31},
    {'sites': [{'domain': 'EXAMPLE.com', 'language': 'en'}, {'domain': 'example.com', 'language': 'en'}]},
    *[{'sites': [site]} for site in [
        {'domain': '127.0.0.1', 'language': 'en'}, {'domain': 'bad_domain', 'language': 'en'},
        {'domain': 'user@example.com', 'language': 'en'}, {'domain': 'example.com:8000', 'language': 'en'},
        {'domain': 'example.com', 'language': 'xx'}, {'domain': 'example.com', 'language': 'en', 'per_day': 0},
        {'domain': 'example.com', 'language': 'en', 'per_day': 201},
        {'domain': 'example.com', 'language': 'en', 'feeds': ['https://user:pass@example.com/feed']},
        {'domain': 'example.com', 'language': 'en', 'feeds': ['https://example.com:8080/feed']},
        {'domain': 'example.com', 'language': 'en', 'sitemaps': ['https://127.0.0.1/sitemap']},
        {'domain': 'example.com', 'language': 'en', 'feeds': ['https://example.com/feed'] * 21},
    ]],
])
def test_invalid_requests_rejected(client, payload):
    assert client.post('/crawls', json=payload).status_code == 422
    assert client.get('/crawls').json()['total'] == 0


def test_paging_and_unknown_ids(client):
    ids = [create(client)['id'] for _ in range(3)]
    page = client.get('/crawls?offset=1&limit=1').json()
    assert page['total'] == 3 and len(page['items']) == 1
    assert (page['offset'], page['limit']) == (1, 1)
    for path in ['/crawls', f'/crawls/{ids[0]}/articles', f'/crawls/{ids[0]}/sources']:
        for query in ('limit=201', 'limit=0', 'offset=-1'):
            assert client.get(f'{path}?{query}').status_code == 422
    missing = uuid4()
    for path in (f'/crawls/{missing}', f'/crawls/{missing}/articles', f'/crawls/{missing}/sources',
                 f'/crawls/{missing}/documents/d'):
        assert client.get(path).status_code == 404


def test_progress_and_all_paywalled_survive_refresh_with_source_errors(client, engine):
    crawl = create(client)
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    assert str(claim.id) == crawl['id']
    assert client.get(f"/crawls/{crawl['id']}").json()['status'] == 'running'
    record_article(engine, claim.id, claim.token, dict(canonical_url='https://example.com/a', url='https://example.com/a',
                   outlet='example.com', status='rejected', reason='paywall', fetched=True))
    record_source(engine, claim.id, claim.token, dict(url='https://example.com/feed', site='example.com', kind='feed',
                  status='error', error='discovery failed'))
    progress = client.get(f"/crawls/{crawl['id']}").json()
    assert progress['counters']['reasons'] == {'paywall': 1}
    assert progress['counters']['fetched'] == 1 and progress['counters']['rejected'] == 1
    assert finish_crawl(engine, claim.id, claim.token)
    detail = client.get(f"/crawls/{crawl['id']}").json()
    assert detail['status'] == 'completed' and detail['counters']['stored'] == 0
    assert detail['counters']['reasons'] == {'paywall': 1}
    assert client.get(f"/crawls/{crawl['id']}/articles").json()['items'][0]['reason'] == 'paywall'
    sources = client.get(f"/crawls/{crawl['id']}/sources").json()
    assert sources['total'] == 1 and sources['items'][0]['error'] == 'discovery failed'


@pytest.mark.parametrize('outcome', ['empty', 'robots', 'discovery'])
def test_zero_document_outcomes_are_distinguishable_after_refresh(client, engine, outcome):
    crawl = create(client)
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    if outcome == 'robots':
        record_article(engine, claim.id, claim.token, dict(canonical_url='https://example.com/a',
            url='https://example.com/a', outlet='example.com', status='rejected', reason='robots disallowed', fetched=False))
    if outcome == 'discovery':
        record_source(engine, claim.id, claim.token, dict(url='https://example.com/feed', site='example.com',
            kind='feed', status='error', error='feed unavailable'))
    assert finish_crawl(engine, claim.id, claim.token)
    detail = client.get(f"/crawls/{crawl['id']}").json()
    sources = client.get(f"/crawls/{crawl['id']}/sources").json()
    assert detail['status'] == 'completed' and detail['counters']['stored'] == 0
    assert detail['counters']['reasons'] == ({'robots disallowed': 1} if outcome == 'robots' else {})
    assert [s['error'] for s in sources['items']] == (['feed unavailable'] if outcome == 'discovery' else [])


def test_only_member_documents_are_returned_including_existing(client, engine):
    crawl = create(client)
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    with engine.begin() as conn:
        for id in ('member', 'unrelated'):
            conn.execute(documents.insert().values(id=id, provider='p', external_id=id, language='en', title='Title',
                byline='Author', outlet='Outlet', url=f'https://example.com/{id}', text='Article body',
                token_offsets=[], sentence_offsets=[], content_hash=id))
    record_article(engine, claim.id, claim.token, dict(canonical_url='https://example.com/member',
        url='https://example.com/member', outlet='example.com', status='existing', document_id='member', fetched=False))
    assert client.get(f"/crawls/{crawl['id']}/documents/unrelated").status_code == 404
    response = client.get(f"/crawls/{crawl['id']}/documents/member")
    assert response.status_code == 200
    assert response.json() == {'id': 'member', 'title': 'Title', 'byline': 'Author', 'outlet': 'Outlet',
        'url': 'https://example.com/member', 'language': 'en', 'published_at': None, 'text': 'Article body'}
    assert client.get(f"/crawls/{crawl['id']}").json()['counters']['existing'] == 1
