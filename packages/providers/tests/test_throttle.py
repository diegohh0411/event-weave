import threading
import time

from weave_providers.crawl.throttle import DomainThrottle


def test_shared_throttle_spacing_and_parallelism(database_url):
    import sqlalchemy as sa
    engine = sa.create_engine(database_url)
    throttles = [DomainThrottle(engine, min_interval_for=lambda _: 0.15) for _ in range(2)]
    starts = []
    ends = []
    def request(throttle):
        with throttle.acquire('example.com'):
            starts.append(time.monotonic())
            time.sleep(0.2)
            ends.append(time.monotonic())
    threads = [threading.Thread(target=request, args=(t,)) for t in throttles]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.14
    assert starts[1] >= ends[0]
    engine.dispose()


def test_different_domains_do_not_block(database_url):
    import sqlalchemy as sa
    engine = sa.create_engine(database_url)
    throttle = DomainThrottle(engine, min_interval_for=lambda _: 0.5)
    with throttle.acquire('one.example'):
        start = time.monotonic()
        with throttle.acquire('two.example'):
            assert time.monotonic() - start < 0.4
    engine.dispose()


def test_fetcher_throttles_robots_meta_and_pages():
    from contextlib import contextmanager
    from weave_providers.crawl.fetch import Fetched, FixtureFetcher, PoliteFetcher
    calls = []
    class RecordingThrottle:
        @contextmanager
        def acquire(self, domain):
            calls.append(domain)
            yield
    routes = {url: Fetched(url, 200, '', None) for url in (
        'https://example.com/robots.txt', 'https://example.com/sitemap.xml',
        'https://example.com/article')}
    inner = FixtureFetcher(routes)
    fetcher = PoliteFetcher(inner, throttle=RecordingThrottle())
    fetcher.meta_view().fetch('https://example.com/sitemap.xml')
    fetcher.fetch('https://example.com/article')
    assert calls == ['example.com'] * 3


def test_site_settings_allow_parallel_requests_but_space_starts(database_url):
    import uuid
    import sqlalchemy as sa
    engine = sa.create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO sites (id, name, domain, language, min_interval_seconds, max_parallel) VALUES (:id, 'test', 'site.example', 'en', 0.5, 2)"), {'id': uuid.uuid4()})
    starts, ends = [], []
    def request():
        with DomainThrottle(engine).acquire('site.example'):
            starts.append(time.monotonic())
            time.sleep(0.8)
            ends.append(time.monotonic())
    threads = [threading.Thread(target=request) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.48
    assert starts[1] < ends[0]
    engine.dispose()
