from weave_providers.crawl.requests import CrawlSite
from weave_providers.crawl.fetch import Fetched, FixtureFetcher
from weave_providers.crawl.site_sources import resolve_sources


def test_site_discovers_robots_sitemap_and_homepage_feed():
    f = FixtureFetcher({'https://example.com/robots.txt': Fetched('x', 200, 'Sitemap: https://example.com/news.xml', None),
                        'https://example.com/': Fetched('x', 200, '<link rel="alternate" type="application/rss+xml" href="/feed.xml">', None)})
    result = resolve_sources(CrawlSite(domain='example.com', language='en'), f)
    assert {(s.kind, s.url) for s in result.sources} == {('sitemap', 'https://example.com/news.xml'), ('feed', 'https://example.com/feed.xml')}


def test_explicit_sources_take_precedence():
    f = FixtureFetcher({})
    result = resolve_sources(CrawlSite(domain='example.com', language='en', feeds=['https://example.com/rss']), f)
    assert [(s.kind, s.url) for s in result.sources] == [('feed', 'https://example.com/rss')]
    assert not f.calls


def test_fallback_sources_when_no_declarations():
    f = FixtureFetcher({'https://example.com/robots.txt': Fetched('x', 404, '', None),
                        'https://example.com/': Fetched('x', 200, '<html></html>', None)})
    result = resolve_sources(CrawlSite(domain='example.com', language='en'), f)
    assert [(s.kind, s.url) for s in result.sources] == [
        ('sitemap', 'https://example.com/sitemap.xml'), ('sitemap', 'https://example.com/sitemap_index.xml'),
        ('feed', 'https://example.com/feed'), ('feed', 'https://example.com/rss')]


def test_sitemap_index_traversal_is_bounded():
    from datetime import datetime, timezone
    from weave_providers.crawl.discover import discover
    from weave_providers.crawl.outlets import Outlet
    ns = 'http://www.sitemaps.org/schemas/sitemap/0.9'
    def index(child): return f'<sitemapindex xmlns="{ns}"><sitemap><loc>{child}</loc></sitemap></sitemapindex>'
    routes = {f'https://example.com/{i}': Fetched('x', 200, index(f'https://example.com/{i+1}'), None) for i in range(4)}
    f = FixtureFetcher(routes)
    discover(Outlet('x', 'example.com', 'en', sitemaps=['https://example.com/0']), f,
             (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)))
    assert f.calls == ['https://example.com/0', 'https://example.com/1', 'https://example.com/2']


def test_source_budget_shared_across_sitemap_and_feeds():
    from datetime import datetime, timezone
    from weave_providers.crawl.discover import discover
    from weave_providers.crawl.outlets import Outlet
    ns = 'http://www.sitemaps.org/schemas/sitemap/0.9'
    children = ''.join(f'<sitemap><loc>https://example.com/{i}</loc></sitemap>' for i in range(120))
    index = f'<sitemapindex xmlns="{ns}">{children}</sitemapindex>'
    empty = f'<urlset xmlns="{ns}"/>'
    routes = {'https://example.com/index': Fetched('x', 200, index, None)}
    routes.update({f'https://example.com/{i}': Fetched('x', 200, empty, None) for i in range(120)})
    f = FixtureFetcher(routes)
    seen = set()
    outlet = Outlet('x', 'example.com', 'en', sitemaps=['https://example.com/index'], feeds=['https://example.com/feed'])
    discover(outlet, f, (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)), source_budget=seen)
    assert len(f.calls) == len(seen) == 100
    assert 'https://example.com/feed' not in f.calls


def test_duplicate_explicit_url_is_resolved_only_once():
    site = CrawlSite(domain='example.com', language='en', feeds=['https://example.com/feed'],
                     sitemaps=['https://example.com/feed'])
    result = resolve_sources(site, FixtureFetcher({}))
    assert len(result.sources) == 1
