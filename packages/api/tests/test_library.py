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
    assert body == {'outlets': [{'value': 'Alpha', 'count': 1}, {'value': 'Beta', 'count': 1}], 'languages': [{'value': 'es', 'count': 2}], 'changes': [], 'total': 2}
    assert api.get('/library/facets', params={'search': 'missing'}).json() == {'outlets': [], 'languages': [], 'changes': [], 'total': 0}


def test_library_unknown_document_404(api):
    assert api.get('/library/documents/missing').status_code == 404


def test_library_provider_filter_applies_to_listing_and_facets(api, engine):
    with engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == 'other').values(provider='crawl'))
    assert [d['id'] for d in api.get('/library/documents', params={'providers': 'crawl'}).json()['items']] == ['other']
    assert api.get('/library/facets', params={'providers': 'crawl'}).json()['total'] == 1


@pytest.fixture
def marked(api, engine):
    from weave_core.schema import crawled_pages, page_rechecks
    gone_at = datetime(2024, 1, 14, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(crawled_pages.insert().values(canonical_url='https://example.com/other', url='https://example.com/other',
            outlet='Beta', fetched_at=gone_at, status='gone', gone_at=gone_at, gone_reason='HTTP 404', document_id='other'))
        conn.execute(crawled_pages.insert().values(canonical_url='https://example.com/a', url='https://example.com/a',
            outlet='Alpha', fetched_at=gone_at, status='stored', document_id='new',
            recheck_due_at=datetime(2024, 2, 1, tzinfo=timezone.utc), recheck_stage=1))
        for day, outcome, doc in ((6, 'edited', 'new'), (8, 'unchanged', 'new')):
            conn.execute(page_rechecks.insert().values(canonical_url='https://example.com/a', stage=day // 7, outcome=outcome,
                checked_at=datetime(2024, 1, day, tzinfo=timezone.utc), http_status=200, document_id=doc))
    return api


def ids(api, *change):
    return [d['id'] for d in api.get('/library/documents', params=[('change', c) for c in change]).json()['items']]


def test_change_filter_edited(marked):
    assert ids(marked, 'edited') == ['new']


def test_change_filter_removed(marked):
    assert ids(marked, 'removed') == ['other']
    assert ids(marked, 'unchanged') == ['third']


def test_change_filter_or(marked):
    assert ids(marked, 'edited', 'removed') == ['other', 'new']


def test_removed_documents_listed_by_default(marked):
    items = {d['id']: d for d in marked.get('/library/documents').json()['items']}
    assert set(items) == {'other', 'new', 'third'}
    assert (items['other']['removed_at'], items['other']['removed_reason']) == ('2024-01-14T00:00:00Z', 'HTTP 404')
    assert items['new']['removed_at'] is None
    assert items['new']['last_checked_at'] == '2024-01-08T00:00:00Z'
    assert items['new']['next_check_at'] == '2024-02-01T00:00:00Z'


def test_facets_changes(marked):
    assert marked.get('/library/facets').json()['changes'] == [{'value': 'edited', 'count': 1}, {'value': 'removed', 'count': 1}]
    assert marked.get('/library/facets', params={'languages': 'en'}).json()['changes'] == [{'value': 'edited', 'count': 1}]


def test_detail_recheck_history_order(marked):
    body = marked.get('/library/documents/new').json()
    assert [(r['outcome'], r['checked_at']) for r in body['rechecks']] == [
        ('unchanged', '2024-01-08T00:00:00Z'), ('edited', '2024-01-06T00:00:00Z')]
    assert marked.get('/library/documents/old').json()['rechecks'] == body['rechecks']


def test_exclude_filters_match_run_selection(marked):
    assert [d['id'] for d in marked.get('/library/documents', params={'exclude_edited': True}).json()['items']] == ['other', 'third']
    assert marked.get('/library/facets', params={'exclude_edited': True, 'exclude_removed': True}).json()['total'] == 1
