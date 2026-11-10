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
