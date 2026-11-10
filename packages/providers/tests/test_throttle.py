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


def test_wait_uses_database_clock_when_python_clock_is_skewed(database_url):
    from datetime import datetime, timezone
    import sqlalchemy as sa
    engine = sa.create_engine(database_url)
    throttle = DomainThrottle(engine, min_interval_for=lambda _: 0.15,
                              clock=lambda: datetime(2099, 1, 1, tzinfo=timezone.utc))
    starts = []
    for _ in range(2):
        with throttle.acquire('skew.example'):
            starts.append(time.monotonic())
    assert starts[1] - starts[0] >= 0.14
    engine.dispose()


def test_wait_exception_releases_advisory_lock_and_connection(database_url):
    import pytest
    import sqlalchemy as sa
    engine = sa.create_engine(database_url, pool_size=1, max_overflow=0)
    throttle = DomainThrottle(engine, min_interval_for=lambda _: 10)
    with throttle.acquire('failure.example'):
        pass
    def fail(_):
        raise RuntimeError('sleep failed')
    throttle.sleep = fail
    with pytest.raises(RuntimeError, match='sleep failed'):
        with throttle.acquire('failure.example'):
            pass
    assert engine.pool.checkedout() == 0
    independent = sa.create_engine(database_url)
    with independent.connect() as conn:
        assert conn.scalar(sa.text('SELECT pg_try_advisory_lock(hashtext(:domain), 0)'), {'domain': 'failure.example'})
        conn.execute(sa.text('SELECT pg_advisory_unlock(hashtext(:domain), 0)'), {'domain': 'failure.example'})
    independent.dispose()
    engine.dispose()


def test_reservation_exception_releases_advisory_lock_and_connection(database_url):
    import pytest
    import sqlalchemy as sa
    engine = sa.create_engine(database_url, pool_size=1, max_overflow=0)
    def fail(conn, cursor, statement, parameters, context, executemany):
        if 'INSERT INTO domain_throttle' in statement:
            raise RuntimeError('reservation failed')
    sa.event.listen(engine, 'before_cursor_execute', fail)
    with pytest.raises(RuntimeError, match='reservation failed'):
        with DomainThrottle(engine).acquire('failure.example'):
            pass
    sa.event.remove(engine, 'before_cursor_execute', fail)
    assert engine.pool.checkedout() == 0
    independent = sa.create_engine(database_url)
    with independent.connect() as conn:
        assert conn.scalar(sa.text('SELECT pg_try_advisory_lock(hashtext(:domain), 0)'), {'domain': 'failure.example'})
        conn.execute(sa.text('SELECT pg_advisory_unlock(hashtext(:domain), 0)'), {'domain': 'failure.example'})
    independent.dispose()
    engine.dispose()


def test_parallel_request_starts_are_spaced(database_url):
    import sqlalchemy as sa
    engine = sa.create_engine(database_url)
    interval = 0.12
    throttle = DomainThrottle(engine, min_interval_for=lambda _: interval, max_parallel_for=lambda _: 2)
    starts = []
    def request():
        with throttle.acquire('parallel-spacing.example'):
            starts.append(time.monotonic())
            time.sleep(0.3)
    threads = [threading.Thread(target=request) for _ in range(3)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    starts.sort()
    assert len(starts) == 3
    assert all(b - a >= interval - 0.005 for a, b in zip(starts, starts[1:]))
    engine.dispose()
