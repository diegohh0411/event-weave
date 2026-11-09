import pytest
from weave_providers.crawl.public_urls import validate_public_url


def resolve(host, port):
    return {'public.test': ['8.8.8.8'], 'mixed.test': ['8.8.8.8', '10.0.0.1']}[host]


@pytest.mark.parametrize('url', ['http://localhost/', 'http://169.254.169.254/', 'https://mixed.test/'])
def test_non_public_targets_rejected(url):
    with pytest.raises(ValueError):
        validate_public_url(url, resolver=resolve)


def test_public_target_accepted():
    assert validate_public_url('https://public.test/a', resolver=resolve) == 'https://public.test/a'


@pytest.mark.parametrize('address', ['fec0::1', '::ffff:10.0.0.1', '64:ff9b::a00:1', '64:ff9b:1::1'])
def test_non_public_ipv6_transition_answers_rejected(address):
    with pytest.raises(ValueError):
        validate_public_url('https://public.test/', resolver=lambda host, port: [address])


def test_private_redirect_is_stopped_before_transport():
    from weave_providers.crawl.fetch import Fetched, FixtureFetcher, PoliteFetcher
    f = FixtureFetcher({'https://public.test/robots.txt': Fetched('x', 404, '', None),
                        'https://public.test/a': Fetched('x', 302, '', None, redirect_to='http://169.254.169.254/secret')})
    polite = PoliteFetcher(f, min_interval=0, validator=lambda u: validate_public_url(u, resolver=resolve), domain='public.test')
    assert polite.fetch('https://public.test/a').status == 0
    assert 'http://169.254.169.254/secret' not in f.calls


def test_unsafe_nested_sitemap_is_rejected_without_transport():
    from datetime import datetime, timezone
    from weave_providers.crawl.discover import discover
    from weave_providers.crawl.fetch import Fetched, FixtureFetcher, PoliteFetcher
    from weave_providers.crawl.outlets import Outlet
    root = 'https://public.test/index'
    xml = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>http://169.254.169.254/data</loc></sitemap></sitemapindex>'
    f = FixtureFetcher({'https://public.test/robots.txt': Fetched('x', 404, None, None), root: Fetched('x', 200, xml, None)})
    polite = PoliteFetcher(f, min_interval=0, validator=lambda u: validate_public_url(u, resolver=resolve), domain='public.test')
    errors = []
    discover(Outlet('x', 'public.test', 'en', sitemaps=[root]), polite.meta_view(),
             (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)),
             on_error=lambda u, reason: errors.append((u, reason)))
    assert len(errors) == 1 and 'unsafe URL' in errors[0][1]
    assert f.calls == ['https://public.test/robots.txt', root]


def test_browser_blocks_private_subrequest():
    from weave_providers.crawl.fetch import Crawl4aiFetcher

    class Request:
        def __init__(self, url): self.url = url
        def is_navigation_request(self): return False
    class Route:
        def __init__(self, url):
            self.request, self.action, self.calls = Request(url), None, []
        async def abort(self): self.action = 'abort'
        async def fulfill(self, response): self.action = 'fulfill'
        async def fetch(self, *, url, max_redirects):
            self.calls.append((url, max_redirects))
            if url.endswith('/redirect'):
                return type('Response', (), {'status': 302, 'headers': {'location': 'http://169.254.169.254/'}})()
            return type('Response', (), {'status': 200, 'headers': {}})()
    class Page:
        async def route(self, pattern, handler): self.handler = handler
        async def route_web_socket(self, pattern, handler): self.socket_handler = handler
    class Strategy:
        def set_hook(self, name, hook): self.hook = hook
    class Browser:
        def __init__(self): self.crawler_strategy, self.actions = Strategy(), []
        async def start(self): pass
        async def close(self): pass
        async def arun(self, url, config):
            page = Page()
            await self.crawler_strategy.hook(page, context=page)
            for target in ('https://public.test/a', 'http://169.254.169.254/', 'file:///etc/passwd', 'https://public.test/redirect'):
                route = Route(target)
                await page.handler(route)
                self.actions.append((route.action, route.calls))
            return type('Result', (), dict(success=True, status_code=200, html='ok', redirected_url=None))()
    browser = Browser()
    with Crawl4aiFetcher(lambda: browser, validator=lambda u: validate_public_url(u, resolver=resolve)) as fetcher:
        assert fetcher.fetch('https://public.test/a').status == 200
    assert browser.actions == [
        ('fulfill', [('https://public.test/a', 0)]), ('abort', []), ('abort', []),
        ('abort', [('https://public.test/redirect', 0)]),
    ]


def test_browser_navigation_redirect_checks_robots_before_next_hop():
    from weave_providers.crawl.fetch import Crawl4aiFetcher

    class Request:
        url = 'https://public.test/start'
        def is_navigation_request(self): return True
    class Route:
        request = Request()
        def __init__(self): self.calls, self.action = [], None
        async def abort(self): self.action = 'abort'
        async def fulfill(self, response): self.action = 'fulfill'
        async def fetch(self, *, url, max_redirects):
            self.calls.append(url)
            return type('Response', (), {'status': 302, 'headers': {'location': '/private'}})()
    class Context:
        async def route(self, pattern, handler): self.handler = handler
        async def route_web_socket(self, pattern, handler): pass
    class Strategy:
        def set_hook(self, name, hook): self.hook = hook
    class Browser:
        def __init__(self): self.crawler_strategy, self.route = Strategy(), Route()
        async def start(self): pass
        async def close(self): pass
        async def arun(self, url, config):
            context = Context()
            await self.crawler_strategy.hook(context, context=context)
            await context.handler(self.route)
            return type('Result', (), dict(success=False, status_code=0, error_message='blocked'))()
    browser = Browser()
    f = Crawl4aiFetcher(lambda: browser, validator=lambda u: validate_public_url(u, resolver=resolve))
    f.navigation_guard = lambda u: not u.endswith('/private')
    with f:
        f.fetch('https://public.test/start')
    assert browser.route.action == 'abort'
    assert browser.route.calls == ['https://public.test/start']
