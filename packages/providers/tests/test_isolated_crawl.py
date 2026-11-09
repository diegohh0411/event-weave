import json

import httpx

from weave_providers.crawl.fetch import Fetched, PoliteFetcher


def test_remote_fetcher_sends_only_url_domain_and_policy_and_returns_rendered_html():
    from weave_providers.crawl.fetch import RemoteBrowserFetcher
    def handler(request):
        assert request.url == 'http://crawl-browser:8081/fetch'
        assert json.loads(request.content) == {'url': 'https://ex.com/a', 'domain': 'ex.com',
                                              'respect_robots': False}
        return httpx.Response(200, json={'url': 'https://ex.com/a', 'status': 200,
            'html': '<p>rendered by JavaScript</p>', 'error': None,
            'final_url': 'https://ex.com/a'})
    with RemoteBrowserFetcher('http://crawl-browser:8081', transport=httpx.MockTransport(handler)) as f:
        pf = PoliteFetcher(f, validator=lambda u: u, domain='ex.com',
                           min_interval=0, respect_robots=False)
        assert pf.fetch('https://ex.com/a').html == '<p>rendered by JavaScript</p>'
        pf.cancelled = lambda: True
        assert pf.fetch('https://ex.com/a').error == 'crawl cancelled'


def test_remote_browser_failure_never_falls_back_to_local_browser():
    from weave_providers.crawl.fetch import RemoteBrowserFetcher
    def handler(request):
        raise httpx.ConnectError('browser unavailable')
    with RemoteBrowserFetcher('http://crawl-browser:8081', transport=httpx.MockTransport(handler)) as f:
        pf = PoliteFetcher(f, validator=lambda u: u, domain='ex.com',
                           min_interval=0, respect_robots=False)
        got = pf.fetch('https://ex.com/a')
        assert got.status == 0 and 'browser unavailable' in got.error


def test_remote_response_outside_domain_is_rejected():
    from weave_providers.crawl.fetch import RemoteBrowserFetcher
    def handler(request):
        return httpx.Response(200, json={'url': 'https://ex.com/a', 'status': 200,
            'html': 'unexpected', 'error': None, 'final_url': 'https://other.com/a'})
    with RemoteBrowserFetcher('http://crawl-browser:8081', transport=httpx.MockTransport(handler)) as f:
        pf = PoliteFetcher(f, validator=lambda u: u, domain='ex.com',
                           min_interval=0, respect_robots=False)
        assert pf.fetch('https://ex.com/a').html is None


def test_only_explicit_isolated_browser_configuration_enables_javascript():
    from weave_providers.crawl.fetch import _default_crawler
    local = _default_crawler(public=True)
    isolated = _default_crawler(public=True, isolated_proxy='http://crawl-egress:8080')
    assert local.browser_config.java_script_enabled is False
    assert isolated.browser_config.java_script_enabled is True
    assert isolated.crawler_strategy.browser_manager.use_undetected is True
    assert isolated.browser_config.proxy_config.server == 'http://crawl-egress:8080'
    assert '--proxy-bypass-list=<-loopback>' in isolated.browser_config.extra_args


def test_browser_service_rejects_arbitrary_controls_and_unsafe_targets(monkeypatch):
    from fastapi.testclient import TestClient
    from weave_providers.crawl.browser_service import app
    monkeypatch.setenv('WEAVE_CRAWL_EGRESS_PROXY', 'http://crawl-egress:8080')
    with TestClient(app) as client:
        assert client.post('/fetch', json={'url': 'https://ex.com', 'domain': 'ex.com',
            'respect_robots': False, 'js_code': 'arbitrary()'}).status_code == 422
        assert client.post('/fetch', json={'url': 'http://127.0.0.1', 'domain': 'ex.com',
            'respect_robots': False}).status_code == 400
        assert client.post('/fetch', json={'url': 'https://other.com', 'domain': 'ex.com',
            'respect_robots': False}).status_code == 400
        monkeypatch.delenv('WEAVE_CRAWL_EGRESS_PROXY')
        assert client.post('/fetch', json={'url': 'https://ex.com', 'domain': 'ex.com',
            'respect_robots': False}).status_code == 503
