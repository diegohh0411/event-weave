from datetime import date, datetime, timezone
from types import SimpleNamespace
import uuid

import sqlalchemy as sa
from weave_core.schema import sites, backfill_sitemaps, backfill_urls
from weave_providers.crawl.backfill import seed_frontier, scan_sitemaps
from weave_providers.crawl.fetch import Fetched, FixtureFetcher

NS = 'http://www.sitemaps.org/schemas/sitemap/0.9'
BASE = 'https://example.com/'

def fetched(body):
    return Fetched('x', 200, body, None)

def test_archive_frontier(database_url):
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), name='example', domain='example.com', language='en', sitemaps=[BASE+'index'], feeds=[], backfill_mode='date', backfill_from=date(2025, 2, 1), backfill_until=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name=site.name, domain=site.domain, language='en'))
    index = f'<sitemapindex xmlns="{NS}"><sitemap><loc>{BASE}old</loc><lastmod>2025-01-01</lastmod></sitemap><sitemap><loc>{BASE}new</loc></sitemap><sitemap><loc>{BASE}bad</loc></sitemap></sitemapindex>'
    urls = f'<urlset xmlns="{NS}"><url><loc>{BASE}article</loc><lastmod>2025-03-01</lastmod></url><url><loc>{BASE}undated</loc></url><url><loc>https://other.com/a</loc><lastmod>2025-03-01</lastmod></url></urlset>'
    f = FixtureFetcher({BASE+'index': fetched(index), BASE+'new': fetched(urls), BASE+'bad': fetched('<bad')})
    seed_frontier(engine, site, f)
    scan_sitemaps(engine, site, f, 10, lambda: False)
    scan_sitemaps(engine, site, f, 10, lambda: False)
    scan_sitemaps(engine, site, f, 10, lambda: False)
    with engine.connect() as c:
        statuses = dict(c.execute(sa.select(backfill_sitemaps.c.url, backfill_sitemaps.c.status)).all())
        assert statuses == {BASE+'index':'done', BASE+'old':'skipped', BASE+'new':'done', BASE+'bad':'error'}
        assert c.scalars(sa.select(backfill_urls.c.canonical_url)).all() == [BASE+'article']
    assert BASE+'old' not in f.calls
    assert len(f.calls) == 3
    engine.dispose()


def test_depth_cap_and_all_mode(database_url, monkeypatch):
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), domain='example.com', sitemaps=[BASE+'0'], feeds=[], backfill_mode='all', backfill_from=None, backfill_until=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name='x', domain=site.domain, language='en'))
    routes = {}
    for depth in range(4):
        routes[BASE+str(depth)] = fetched(f'<sitemapindex xmlns="{NS}"><sitemap><loc>{BASE}{depth+1}</loc></sitemap></sitemapindex>')
    f = FixtureFetcher(routes)
    seed_frontier(engine, site, f)
    for _ in range(5):
        scan_sitemaps(engine, site, f, 10, lambda: False)
    with engine.connect() as c:
        assert c.execute(sa.select(backfill_sitemaps.c.status).where(backfill_sitemaps.c.url == BASE+'4')).scalar_one() == 'skipped'
    assert f.calls == [BASE+str(i) for i in range(4)]
    with engine.begin() as c:
        c.execute(backfill_sitemaps.delete())
    monkeypatch.setenv('WEAVE_BACKFILL_MAX_SITEMAPS', '1')
    f.calls.clear()
    seed_frontier(engine, site, f)
    scan_sitemaps(engine, site, f, 10, lambda: False)
    with engine.connect() as c:
        assert c.execute(sa.select(backfill_sitemaps.c.status).where(backfill_sitemaps.c.url == BASE+'1')).scalar_one() == 'skipped'
    with engine.begin() as c:
        c.execute(backfill_sitemaps.delete())
    f.routes[BASE+'0'] = fetched(f'<urlset xmlns="{NS}"><url><loc>{BASE}undated</loc></url></urlset>')
    seed_frontier(engine, site, f)
    scan_sitemaps(engine, site, f, 10, lambda: False)
    with engine.connect() as c:
        assert c.scalars(sa.select(backfill_urls.c.canonical_url)).all() == [BASE+'undated']
    engine.dispose()


def test_configured_roots_obey_sitemap_cap(database_url, monkeypatch):
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), domain='example.com', sitemaps=[BASE+'a', BASE+'b', BASE+'c'], feeds=[], backfill_mode='all', backfill_from=None, backfill_until=None)
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name='x', domain=site.domain, language='en'))
    monkeypatch.setenv('WEAVE_BACKFILL_MAX_SITEMAPS', '2')
    f = FixtureFetcher({u: fetched(f'<urlset xmlns="{NS}"/>') for u in site.sitemaps})
    seed_frontier(engine, site, f)
    scan_sitemaps(engine, site, f, 10, lambda: False)
    assert f.calls == site.sitemaps[:2]
    with engine.connect() as c:
        assert c.scalar(sa.select(backfill_sitemaps.c.status).where(backfill_sitemaps.c.url == BASE+'c')) == 'skipped'
    engine.dispose()


def test_three_month_archive_skips_old_child(database_url):
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), domain='example.com', sitemaps=[BASE+'index'], feeds=[], backfill_mode='date', backfill_from=date(2025, 2, 1), backfill_until=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name='x', domain=site.domain, language='en'))
    children = ''.join(f'<sitemap><loc>{BASE}{m}</loc><lastmod>2025-0{m}-28</lastmod></sitemap>' for m in range(1, 4))
    routes = {BASE+'index': fetched(f'<sitemapindex xmlns="{NS}">{children}</sitemapindex>')}
    for m in (2, 3):
        routes[BASE+str(m)] = fetched(f'<urlset xmlns="{NS}"><url><loc>{BASE}article{m}</loc><lastmod>2025-0{m}-15</lastmod></url></urlset>')
    f = FixtureFetcher(routes)
    seed_frontier(engine, site, f)
    scan_sitemaps(engine, site, f, 10, lambda: False)
    scan_sitemaps(engine, site, f, 10, lambda: False)
    assert BASE+'1' not in f.calls
    with engine.connect() as c:
        assert c.scalar(sa.select(backfill_sitemaps.c.status).where(backfill_sitemaps.c.url == BASE+'1')) == 'skipped'
        assert set(c.scalars(sa.select(backfill_urls.c.canonical_url))) == {BASE+'article2', BASE+'article3'}
    engine.dispose()


def test_fetch_batch_records_http_error_and_does_not_retry(database_url):
    from weave_providers.crawl.backfill import fetch_batch
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), name='example', domain='example.com', language='en')
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name=site.name, domain=site.domain, language='en', backfill_state='running'))
        c.execute(backfill_urls.insert().values(site_id=site.id, canonical_url=BASE+'missing', status='pending'))
    f = FixtureFetcher({BASE+'missing': Fetched(BASE+'missing', 404, '', None)})
    for _ in range(2):
        fetch_batch(engine, site, f, 10, datetime.now(timezone.utc), lambda *args: None, lambda: False, None)
    with engine.connect() as c:
        assert c.execute(sa.select(backfill_urls.c.status, backfill_urls.c.outcome).where(backfill_urls.c.site_id == site.id)).one() == ('done', 'error')
    assert f.calls == [BASE+'missing']
    engine.dispose()


def test_batch_budget_pause_and_guard(database_url):
    import pytest
    from weave_providers.crawl.backfill import fetch_batch, budget_left
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), name='example', domain='example.com', language='en')
    urls = [BASE+'missing1', BASE+'missing2']
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name=site.name, domain=site.domain, language='en', backfill_state='running', backfill_per_hour=1))
        c.execute(backfill_urls.insert(), [dict(site_id=site.id, canonical_url=u, status='pending') for u in urls])
        assert budget_left(c, site.id) == 1
    f = FixtureFetcher({u: Fetched(u, 404, '', None) for u in urls})
    fetch_batch(engine, site, f, 10, datetime.now(timezone.utc), lambda *args: None, lambda: False, None)
    assert len(f.calls) == 1
    with engine.begin() as c:
        c.execute(sites.update().where(sites.c.id == site.id).values(backfill_state='paused'))
    fetch_batch(engine, site, f, 10, datetime.now(timezone.utc), lambda *args: None, lambda: False, None)
    assert len(f.calls) == 1
    with engine.begin() as c:
        c.execute(sites.update().where(sites.c.id == site.id).values(backfill_state='running'))
    def lost(conn):
        raise RuntimeError('lease lost')
    with pytest.raises(RuntimeError, match='lease lost'):
        fetch_batch(engine, site, f, 10, datetime.now(timezone.utc), lambda *args: None, lambda: False, lost)
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(backfill_urls).where(backfill_urls.c.site_id == site.id, backfill_urls.c.status == 'pending')) == 1
    engine.dispose()


def test_batch_stores_then_recognizes_known_url(database_url):
    from pathlib import Path
    from weave_providers.crawl.backfill import fetch_batch
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), name='example', domain='example.com', language='en')
    url = BASE+'article'
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name=site.name, domain=site.domain, language='en', backfill_state='running'))
        c.execute(backfill_urls.insert().values(site_id=site.id, canonical_url=url, status='pending'))
    html = (Path(__file__).parent / 'fixtures' / 'reuters' / 'article.html').read_text()
    f = FixtureFetcher({url: fetched(html)})
    fetch_batch(engine, site, f, 10, datetime.now(timezone.utc), lambda *args: None, lambda: False, None)
    with engine.begin() as c:
        row = c.execute(sa.select(backfill_urls.c.outcome, backfill_urls.c.document_id).where(backfill_urls.c.site_id == site.id)).one()
        assert row.outcome == 'stored' and row.document_id
        c.execute(backfill_urls.update().where(backfill_urls.c.site_id == site.id).values(status='pending'))
    fetch_batch(engine, site, f, 10, datetime.now(timezone.utc), lambda *args: None, lambda: False, None)
    with engine.connect() as c:
        assert c.scalar(sa.select(backfill_urls.c.outcome).where(backfill_urls.c.site_id == site.id)) == 'existing'
    assert f.calls == [url]
    engine.dispose()


def test_batch_stops_when_paused_after_first_url(database_url):
    from weave_providers.crawl.backfill import fetch_batch
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), name='example', domain='example.com', language='en')
    urls = [BASE+'one', BASE+'two']
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name=site.name, domain=site.domain, language='en', backfill_state='running'))
        c.execute(backfill_urls.insert(), [dict(site_id=site.id, canonical_url=u, status='pending') for u in urls])
    f = FixtureFetcher({u: Fetched(u, 404, '', None) for u in urls})
    def pause(kind, outcome):
        with engine.begin() as c:
            c.execute(sites.update().where(sites.c.id == site.id).values(backfill_state='paused'))
    fetch_batch(engine, site, f, 10, datetime.now(timezone.utc), pause, lambda: False, None)
    assert len(f.calls) == 1
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(backfill_urls).where(backfill_urls.c.site_id == site.id, backfill_urls.c.status == 'pending')) == 1
    engine.dispose()


def test_sitemap_fetch_does_not_lock_site_and_stops_on_pause(database_url):
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), domain='example.com', sitemaps=[BASE+'a', BASE+'b'], feeds=[], backfill_mode='all', backfill_from=None, backfill_until=None)
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name='x', domain=site.domain, language='en', backfill_state='running'))
    class PausingFetcher(FixtureFetcher):
        def fetch(self, url):
            with engine.begin() as c:
                c.execute(sa.text("SET LOCAL lock_timeout = '500ms'"))
                c.execute(sites.update().where(sites.c.id == site.id).values(backfill_state='paused'))
            return super().fetch(url)
    f = PausingFetcher({u: fetched(f'<urlset xmlns="{NS}"/>') for u in site.sitemaps})
    seed_frontier(engine, site, f)
    def running():
        with engine.connect() as c:
            return c.scalar(sa.select(sites.c.backfill_state).where(sites.c.id == site.id)) == 'running'
    scan_sitemaps(engine, site, f, 10, lambda: False, None, running)
    assert f.calls == [BASE+'a']
    engine.dispose()


def test_lease_loss_after_page_write_recovers_pending_url(database_url):
    import pytest
    from pathlib import Path
    from weave_providers.crawl.backfill import fetch_batch
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), name='example', domain='example.com', language='en')
    url = BASE+'article'
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name=site.name, domain=site.domain, language='en', backfill_state='running'))
        c.execute(backfill_urls.insert().values(site_id=site.id, canonical_url=url, status='pending'))
    f = FixtureFetcher({url: fetched((Path(__file__).parent / 'fixtures' / 'reuters' / 'article.html').read_text())})
    def lost_on_emit(kind, outcome):
        raise RuntimeError('lease lost')
    with pytest.raises(RuntimeError, match='lease lost'):
        fetch_batch(engine, site, f, 1, datetime.now(timezone.utc), lost_on_emit, lambda: False, None)
    fetch_batch(engine, site, f, 1, datetime.now(timezone.utc), lambda *args: None, lambda: False, None)
    with engine.connect() as c:
        assert c.execute(sa.select(backfill_urls.c.status, backfill_urls.c.outcome).where(backfill_urls.c.site_id == site.id)).one() == ('done', 'existing')
    assert f.calls == [url]
    engine.dispose()


def test_concurrent_batches_recheck_budget_without_locking_site(database_url, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from weave_providers.crawl import backfill
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), name='example', domain='example.com', language='en')
    urls = [BASE+'one', BASE+'two']
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name=site.name, domain=site.domain, language='en', backfill_state='running'))
        c.execute(backfill_urls.insert(), [dict(site_id=site.id, canonical_url=u, status='pending') for u in urls])
    entered, release = Event(), Event()
    calls = []
    monkeypatch.setattr(backfill, 'budget_left', lambda conn, site_id: 1 - len(calls))
    class BlockingFetcher(FixtureFetcher):
        def fetch(self, url):
            with engine.begin() as c:
                c.execute(sa.text("SET LOCAL lock_timeout = '500ms'"))
                c.execute(sites.update().where(sites.c.id == site.id).values(name='example'))
            calls.append(url)
            entered.set()
            assert release.wait(5)
            return super().fetch(url)
    f = BlockingFetcher({u: Fetched(u, 404, '', None) for u in urls})
    def run():
        backfill.fetch_batch(engine, site, f, 1, datetime.now(timezone.utc), lambda *args: None, lambda: False, None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run)
        assert entered.wait(5)
        second = pool.submit(run)
        release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert len(calls) == 1
    engine.dispose()


def test_scan_continues_into_children_found_in_same_job(database_url):
    engine = sa.create_engine(database_url)
    site = SimpleNamespace(id=uuid.uuid4(), name='example', domain='example.com', language='en', sitemaps=[BASE+'index'], feeds=[], backfill_mode='all', backfill_from=None, backfill_until=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with engine.begin() as c:
        c.execute(sites.insert().values(id=site.id, name=site.name, domain=site.domain, language='en'))
    index = f'<sitemapindex xmlns="{NS}"><sitemap><loc>{BASE}a</loc></sitemap><sitemap><loc>{BASE}b</loc></sitemap></sitemapindex>'
    empty = f'<urlset xmlns="{NS}"></urlset>'
    f = FixtureFetcher({BASE+'index': fetched(index), BASE+'a': fetched(empty), BASE+'b': fetched(empty)})
    seed_frontier(engine, site, f)
    scan_sitemaps(engine, site, f, 5, lambda: False)
    assert f.calls == [BASE+'index', BASE+'a', BASE+'b']
    engine.dispose()
