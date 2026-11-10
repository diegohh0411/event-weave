from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from weave_api.main import create_app
from weave_core.schema import documents


@pytest.fixture
def api(service, engine):
    with engine.begin() as conn:
        for did, article, version, superseded, title, outlet, language, day, order in [
            ('old', 'a', 1, True, 'Old story', 'Alpha', 'en', 1, 0),
            ('new', 'a', 2, False, 'New Story', 'Alpha', 'en', 2, 2),
            ('other', 'b', 1, False, 'Another story', 'Beta', 'es', 3, 1),
            ('third', 'c', 1, False, 'Elsewhere', 'Alpha', 'es', 4, 3),
        ]:
            conn.execute(documents.insert().values(
                id=did, article_id=article, version=version,
                superseded_at=datetime(2024, 1, 5, tzinfo=timezone.utc) if superseded else None,
                provider='p', external_id=did, title=title, outlet=outlet, language=language,
                published_at=datetime(2024, 1, day, tzinfo=timezone.utc), order_key=[order],
                url=f'https://example.com/{did}', byline='Author', text='Body',
                token_offsets=[], sentence_offsets=[], content_hash='hash'))
    with TestClient(create_app(service)) as client:
        yield client


def test_library_lists_latest_versions_with_filters(api):
    body = api.get('/library/documents').json()
    assert [(d['id'], d['versions']) for d in body['items']] == [('other', 1), ('new', 2), ('third', 1)]
    assert body['total'] == 3
    assert [d['id'] for d in api.get('/library/documents', params=[('outlets', 'Alpha'), ('outlets', 'Beta'), ('languages', 'es'), ('search', 'STORY')]).json()['items']] == ['other']
    assert [d['id'] for d in api.get('/library/documents', params={'published_from': '2024-01-02T00:00:00Z', 'published_to': '2024-01-04T00:00:00Z'}).json()['items']] == ['other', 'new']
    page = api.get('/library/documents', params={'offset': 1, 'limit': 1}).json()
    assert [d['id'] for d in page['items']] == ['new'] and page['total'] == 3


def test_library_detail_includes_history(api):
    body = api.get('/library/documents/old').json()
    assert (body['id'], body['article_id'], body['version'], body['versions'], body['byline'], body['text']) == ('old', 'a', 1, 2, 'Author', 'Body')
    assert [(h['id'], h['version'], h['superseded_at'] is None) for h in body['history']] == [('old', 1, False), ('new', 2, True)]


def test_library_facets_counts_match_filter(api):
    body = api.get('/library/facets', params={'languages': 'es'}).json()
    assert body == {'outlets': [{'value': 'Alpha', 'count': 1}, {'value': 'Beta', 'count': 1}], 'languages': [{'value': 'es', 'count': 2}], 'total': 2}
    assert api.get('/library/facets', params={'search': 'missing'}).json() == {'outlets': [], 'languages': [], 'total': 0}


def test_library_unknown_document_404(api):
    assert api.get('/library/documents/missing').status_code == 404


def test_library_provider_filter_applies_to_listing_and_facets(api, engine):
    with engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == 'other').values(provider='crawl'))
    assert [d['id'] for d in api.get('/library/documents', params={'providers': 'crawl'}).json()['items']] == ['other']
    assert api.get('/library/facets', params={'providers': 'crawl'}).json()['total'] == 1
