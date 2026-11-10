"""The public crawl queue is exercised with fixture fetchers, never the network."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from weave_core.db import make_engine
from weave_core.schema import crawl_articles, crawl_runs, crawl_sources, crawled_pages, documents
from weave_providers.crawl.fetch import Fetched, FixtureFetcher
from weave_providers.crawl.jobs import claim_crawl, create_crawl, crawl_counters
from weave_providers.crawl.requests import CrawlRequest
from weave_providers.crawl import worker

BASE = 'https://example.test'
HTML = (Path(__file__).parent / 'fixtures/reuters/article.html').read_text()


@pytest.fixture
def engine(database_url, monkeypatch):
    # Validation is injected, not bypassed, in fixture-only tests.
    monkeypatch.setattr(worker, 'validate_public_url', lambda url: url)
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


def request():
    return CrawlRequest(sites=[{'domain': 'example.test', 'language': 'en',
                                'sitemaps': [BASE + '/sitemap.xml'], 'per_day': 10}])


def factory(on_fetch=None, on_close=None):
    sitemap = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>https://example.test/a</loc></url>'
               '<url><loc>https://example.test/b</loc></url></urlset>')
    routes = {BASE + '/robots.txt': Fetched('', 200, 'User-agent: *\nAllow: /', None),
              BASE + '/sitemap.xml': Fetched('', 200, sitemap, None),
              BASE + '/a': Fetched('', 200, HTML, None),
              BASE + '/b': Fetched('', 200, HTML.replace('replace roughly forty', 'replace roughly fifty'), None)}
    class Fake(FixtureFetcher):
        def fetch(self, url):
            if on_fetch:
                on_fetch(url)
            return super().fetch(url)
    @contextmanager
    def create():
        try:
            yield Fake(routes), Fake(routes)
        finally:
            if on_close:
                on_close()
    return create


def status(engine, id):
    with engine.connect() as c:
        return c.execute(sa.select(crawl_runs).where(crawl_runs.c.id == id)).mappings().one()


def test_worker_records_progress_and_resumes_interrupted_claim(engine, monkeypatch):
    crawl_id = create_crawl(engine, request())
    stop = Event()
    original = worker.record_article
    seen_progress = []
    def interrupt(engine, id, token, outcome):
        ok = original(engine, id, token, outcome)
        with engine.connect() as c:
            seen_progress.append(crawl_counters(c, id))
        stop.set()
        return ok
    monkeypatch.setattr(worker, 'record_article', interrupt)
    worker.run_crawl_worker(engine, stop, once=True, fetcher_factory=factory(), min_interval=0)
    assert seen_progress[0]['stored'] == 1
    assert status(engine, crawl_id)['status'] == 'queued'
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(crawl_sources)) == 1
    monkeypatch.setattr(worker, 'record_article', original)
    worker.run_crawl_worker(engine, Event(), once=True, fetcher_factory=factory(), min_interval=0)
    assert status(engine, crawl_id)['status'] == 'completed'
    with engine.connect() as c:
        counters = crawl_counters(c, crawl_id)
        assert counters['discovered'] == counters['stored'] == 2
        assert c.scalar(sa.select(sa.func.count()).select_from(documents)) == 2
        assert c.scalar(sa.select(sa.func.count()).select_from(crawl_articles)) == 2


def test_stale_claim_does_not_fetch_or_finish(engine):
    crawl_id = create_crawl(engine, request())
    now = datetime.now(timezone.utc)
    stale = claim_crawl(engine, now)
    current = claim_crawl(engine, now + timedelta(seconds=121))
    calls = []
    worker.process_claim(engine, stale, Event(), fetcher_factory=factory(on_fetch=calls.append), min_interval=0)
    assert not calls
    assert status(engine, crawl_id)['claim_token'] == current.token


def test_stale_source_event_is_rejected_before_heartbeat(engine):
    crawl_id = create_crawl(engine, request())
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    current = []
    def takeover(url):
        if url == BASE + '/sitemap.xml' and not current:
            current.append(claim_crawl(engine, datetime.now(timezone.utc) + timedelta(seconds=121)))
    worker.process_claim(engine, claim, Event(), fetcher_factory=factory(on_fetch=takeover), min_interval=0)
    assert status(engine, crawl_id)['claim_token'] == current[0].token
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(crawl_sources)) == 0
        assert c.scalar(sa.select(sa.func.count()).select_from(crawl_articles)) == 0


def test_reclaim_during_fetch_prevents_stale_document_and_event_writes(engine):
    crawl_id = create_crawl(engine, request())
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    reclaimed = []
    def takeover(url):
        if url == BASE + '/a' and not reclaimed:
            reclaimed.append(claim_crawl(engine, datetime.now(timezone.utc) + timedelta(seconds=121)))
    worker.process_claim(engine, claim, Event(), fetcher_factory=factory(on_fetch=takeover), min_interval=0)
    assert reclaimed[0].id == crawl_id
    assert status(engine, crawl_id)['claim_token'] == reclaimed[0].token
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(documents)) == 0
        assert c.scalar(sa.select(sa.func.count()).select_from(crawled_pages)) == 0
        assert c.scalar(sa.select(sa.func.count()).select_from(crawl_articles)) == 0


def test_reclaim_after_document_commit_before_membership_event(engine, monkeypatch):
    crawl_id = create_crawl(engine, request())
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    original = worker.record_article
    def crash(*args):
        raise SystemExit('simulated process death')
    monkeypatch.setattr(worker, 'record_article', crash)
    closed = Event()
    with pytest.raises(SystemExit):
        worker.process_claim(engine, claim, Event(), fetcher_factory=factory(on_close=closed.set), min_interval=0)
    assert closed.is_set()
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(documents)) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(crawl_articles)) == 0
    monkeypatch.setattr(worker, 'record_article', original)
    resumed = claim_crawl(engine, datetime.now(timezone.utc) + timedelta(seconds=121))
    assert resumed.started_at == claim.started_at
    worker.process_claim(engine, resumed, Event(), fetcher_factory=factory(), min_interval=0)
    assert status(engine, crawl_id)['status'] == 'completed'
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(documents)) == 2
        assert {r.status for r in c.execute(sa.select(crawl_articles.c.status))} == {'existing', 'stored'}
        assert c.scalar(sa.select(sa.func.count()).select_from(crawl_articles)) == 2


def test_heartbeat_uses_an_independent_connection_and_stops_after_loss(engine):
    create_crawl(engine, request())
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    entered, unblock, lost, renewed = Event(), Event(), Event(), Event()
    original_heartbeat = worker.heartbeat
    renewals = []
    def observe_heartbeat(engine, id, token, now, lease_seconds=120):
        ok = original_heartbeat(engine, id, token, now, lease_seconds=lease_seconds)
        if not ok:
            lost.set()
        else:
            renewals.append(now)
            if len(renewals) > 1:
                renewed.set()
        return ok
    def block(url):
        if url == BASE + '/a':
            entered.set()
            assert unblock.wait(5)
    # Patch manually so this thread can observe the heartbeat after a takeover.
    from unittest.mock import patch
    with patch.object(worker, 'heartbeat', observe_heartbeat):
        thread = Thread(target=worker.process_claim, args=(engine, claim, Event()),
                        kwargs={'fetcher_factory': factory(on_fetch=block), 'min_interval': 0,
                                'heartbeat_seconds': 0.01})
        thread.start()
        try:
            assert entered.wait(5)
            assert renewed.wait(5)  # the crawler is still blocked in the fetch
            assert claim_crawl(engine, datetime.now(timezone.utc) + timedelta(seconds=121))
            assert lost.wait(5)
        finally:
            unblock.set()
            thread.join(5)
    assert not thread.is_alive()
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(documents)) == 0
        assert c.scalar(sa.select(sa.func.count()).select_from(crawl_articles)) == 0


def test_shutdown_during_fetch_closes_and_releases_claim(engine):
    crawl_id = create_crawl(engine, request())
    stop, closed = Event(), Event()
    def shutdown(url):
        if url == BASE + '/a':
            stop.set()
    worker.run_crawl_worker(engine, stop, once=True,
                            fetcher_factory=factory(on_fetch=shutdown, on_close=closed.set), min_interval=0)
    assert closed.is_set()
    assert status(engine, crawl_id)['status'] == 'queued'
    assert claim_crawl(engine, datetime.now(timezone.utc)).id == crawl_id
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(documents)) == 0


def test_heartbeat_lease_outlives_the_configured_interval(engine, monkeypatch):
    create_crawl(engine, request())
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    seen = []
    original_heartbeat = worker.heartbeat
    def capture(engine, id, token, now, lease_seconds=120):
        seen.append(lease_seconds)
        return original_heartbeat(engine, id, token, now, lease_seconds=lease_seconds)
    monkeypatch.setattr(worker, 'heartbeat', capture)
    worker.process_claim(engine, claim, Event(), fetcher_factory=factory(), min_interval=0,
                         heartbeat_seconds=600)
    assert seen and min(seen) > 600


def test_worker_validates_public_urls_before_any_fixture_transport(engine, monkeypatch):
    from weave_providers.crawl import public_urls
    monkeypatch.setattr(public_urls, '_resolve', lambda host, port: ['127.0.0.1'])
    monkeypatch.setattr(worker, 'validate_public_url', public_urls.validate_public_url)
    crawl_id = create_crawl(engine, request())
    calls = []
    worker.run_crawl_worker(engine, Event(), once=True, fetcher_factory=factory(on_fetch=calls.append), min_interval=0)
    assert calls == []
    assert status(engine, crawl_id)['status'] == 'completed'
    with engine.connect() as c:
        source = c.execute(sa.select(crawl_sources.c.status, crawl_sources.c.error)).one()
    assert source.status == 'error' and 'unsafe URL' in source.error


def test_failure_does_not_stop_worker_from_processing_next_job(engine, monkeypatch):
    first, second = create_crawl(engine, request()), create_crawl(engine, request())
    stop = Event()
    count = 0
    finish = worker.finish_crawl
    def finish_and_stop(engine, id, token, error=None):
        result = finish(engine, id, token, error=error)
        if id == second:
            stop.set()
        return result
    monkeypatch.setattr(worker, 'finish_crawl', finish_and_stop)
    @contextmanager
    def sometimes_fails():
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError('browser unavailable')
        with factory()() as fetchers:
            yield fetchers
    worker.run_crawl_worker(engine, stop, poll_seconds=0.01, fetcher_factory=sometimes_fails, min_interval=0)
    assert status(engine, first)['status'] == 'failed'
    assert 'browser unavailable' in status(engine, first)['error']
    assert status(engine, second)['status'] == 'completed'


def test_cli_registers_crawl_worker(monkeypatch, database_url):
    from weave_core.cli import app
    from weave_providers.crawl import cli
    monkeypatch.setenv('WEAVE_DATABASE_URL', database_url)
    seen = []
    monkeypatch.setattr(cli, 'run_crawl_worker', lambda engine, stop, **kw: seen.append(kw))
    result = CliRunner().invoke(app, ['crawl-worker', '--once'])
    assert result.exit_code == 0, result.output
    assert seen == [{'once': True}]


def test_invalid_robots_configuration_stops_worker_before_claiming_jobs(monkeypatch):
    monkeypatch.setenv('WEAVE_CRAWL_RESPECT_ROBOTS', 'invalid')
    claimed = []
    def claim(*args):
        claimed.append(True)
        return None
    monkeypatch.setattr(worker, 'claim_crawl', claim)
    with pytest.raises(ValueError, match='WEAVE_CRAWL_RESPECT_ROBOTS'):
        worker.run_crawl_worker(None, Event(), once=True)
    assert claimed == []


def test_site_cap_counts_only_stored_today(engine, monkeypatch):
    from uuid import uuid4
    from weave_core.schema import sites
    site_id = uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(sites.insert().values(id=site_id, name='Example', domain='example.test', language='en', daily_cap=3))
    old = create_crawl(engine, request(), site_id=site_id)
    with engine.begin() as conn:
        for i, (state, date) in enumerate([('stored', now), ('stored', now - timedelta(days=1)), ('updated', now)]):
            conn.execute(crawl_articles.insert().values(crawl_id=old, canonical_url=f'https://example.test/{i}', url=f'https://example.test/{i}', outlet='Example', status=state, fetched=True, updated_at=date))
        conn.execute(crawl_runs.update().where(crawl_runs.c.id == old).values(status='completed'))
    other_id = uuid4()
    with engine.begin() as conn:
        conn.execute(sites.insert().values(id=other_id, name='Other', domain='other.test', language='en'))
    other = create_crawl(engine, request(), site_id=other_id)
    with engine.begin() as conn:
        conn.execute(crawl_articles.insert().values(crawl_id=other, canonical_url='https://other.test/a', url='https://other.test/a', outlet='Other', status='stored', fetched=True, updated_at=now))
        conn.execute(crawl_runs.update().where(crawl_runs.c.id == other).values(status='completed'))
    create_crawl(engine, request(), site_id=site_id)
    claim = claim_crawl(engine, now)
    monkeypatch.setattr(worker, '_now', lambda: now + timedelta(days=3))
    received = []
    monkeypatch.setattr(worker, 'crawl', lambda *args, **kwargs: received.append(kwargs['caps']))
    worker.process_claim(engine, claim, Event(), fetcher_factory=factory(), min_interval=0)
    assert received == [{'Example': 2}]


def test_concurrent_site_crawls_consume_cap_atomically(engine, monkeypatch):
    from uuid import uuid4
    from threading import Barrier
    from weave_core.schema import sites
    site_id = uuid4()
    with engine.begin() as conn:
        conn.execute(sites.insert().values(id=site_id, name='Example', domain='example.test', language='en', daily_cap=1))
    for _ in range(2):
        create_crawl(engine, request(), site_id=site_id)
    claims = [claim_crawl(engine, datetime.now(timezone.utc)) for _ in range(2)]
    barrier = Barrier(2, timeout=10)
    @contextmanager
    def no_throttle(self, domain):
        yield
    monkeypatch.setattr(worker.DomainThrottle, 'acquire', no_throttle)
    def fetch(url):
        if url == BASE + '/a':
            barrier.wait()
    threads = [Thread(target=worker.process_claim, args=(engine, claim, Event()),
                      kwargs={'fetcher_factory': factory(on_fetch=fetch), 'min_interval': 0}) for claim in claims]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=20)
    assert all(not t.is_alive() for t in threads)
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(documents)) == 1
        assert conn.scalar(sa.select(sa.func.count()).select_from(crawl_articles).where(crawl_articles.c.status == 'stored')) == 1
        assert conn.scalar(sa.select(sa.func.count()).select_from(crawl_articles).where(crawl_articles.c.reason == 'daily cap reached')) >= 1


def test_backfill_worker_completes_and_records_claim_id(engine):
    from uuid import uuid4
    from weave_core.schema import sites, backfill_urls
    from weave_providers.crawl.scheduler import schedule_due_backfills
    now = datetime.now(timezone.utc)
    site_id = uuid4()
    with engine.begin() as conn:
        conn.execute(sites.insert().values(id=site_id, name='example.test', domain='example.test', language='en',
            sitemaps=[BASE + '/sitemap.xml'], backfill_state='running', backfill_mode='all', backfill_until=now))
    crawl_id = schedule_due_backfills(engine, now)[0]
    claim = claim_crawl(engine, now)
    worker.process_claim(engine, claim, Event(), fetcher_factory=factory(), min_interval=0)
    assert status(engine, crawl_id)['status'] == 'completed'
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sites.c.backfill_state).where(sites.c.id == site_id)) == 'done'
        assert conn.scalars(sa.select(backfill_urls.c.crawl_id)).all() == [crawl_id, crawl_id]


def test_backfill_restart_resumes_existing_frontier(engine):
    from uuid import uuid4
    from weave_core.schema import sites, backfill_sitemaps, backfill_urls
    from weave_providers.crawl.scheduler import schedule_due_backfills
    now = datetime.now(timezone.utc)
    site_id = uuid4()
    with engine.begin() as conn:
        conn.execute(sites.insert().values(id=site_id, name='example.test', domain='example.test', language='en',
            sitemaps=[BASE + '/sitemap.xml'], backfill_state='running', backfill_mode='all', backfill_until=now))
        conn.execute(backfill_sitemaps.insert().values(site_id=site_id, url=BASE + '/sitemap.xml', status='done', depth=0))
        conn.execute(backfill_urls.insert().values(site_id=site_id, canonical_url=BASE + '/a', status='pending'))
    schedule_due_backfills(engine, now)
    calls = []
    worker.process_claim(engine, claim_crawl(engine, now), Event(), fetcher_factory=factory(on_fetch=calls.append), min_interval=0)
    assert BASE + '/sitemap.xml' not in calls
    assert BASE + '/a' in calls
    with engine.connect() as conn:
        assert conn.scalar(sa.select(backfill_urls.c.status).where(backfill_urls.c.site_id == site_id)) == 'done'


def test_backfill_stored_articles_do_not_consume_daily_cap(engine, monkeypatch):
    from uuid import uuid4
    from weave_core.schema import sites
    site_id = uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(sites.insert().values(id=site_id, name='Example', domain='example.test', language='en', daily_cap=1))
    old = create_crawl(engine, request(), site_id=site_id)
    with engine.begin() as conn:
        conn.execute(crawl_runs.update().where(crawl_runs.c.id == old).values(status='completed', trigger='backfill'))
        conn.execute(crawl_articles.insert().values(crawl_id=old, canonical_url=BASE+'/old', url=BASE+'/old', outlet='Example', status='stored', fetched=True, updated_at=now))
    create_crawl(engine, request(), site_id=site_id)
    claim = claim_crawl(engine, now)
    received = []
    monkeypatch.setattr(worker, 'crawl', lambda *args, **kwargs: received.append(kwargs['caps']))
    worker.process_claim(engine, claim, Event(), fetcher_factory=factory(), min_interval=0)
    assert received == [{'Example': 1}]


def test_consume_cap_excludes_backfill_articles(engine):
    from uuid import uuid4
    from weave_core.schema import sites
    site_id = uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(sites.insert().values(id=site_id, name='Example', domain='example.test', language='en', daily_cap=1))
    old = create_crawl(engine, request(), site_id=site_id)
    with engine.begin() as conn:
        conn.execute(crawl_runs.update().where(crawl_runs.c.id == old).values(status='completed', trigger='backfill'))
        conn.execute(crawl_articles.insert().values(crawl_id=old, canonical_url=BASE+'/old', url=BASE+'/old', outlet='Example', status='stored', fetched=True, updated_at=now))
    create_crawl(engine, request(), site_id=site_id)
    worker.process_claim(engine, claim_crawl(engine, now), Event(), fetcher_factory=factory(), min_interval=0)
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(documents)) == 1
