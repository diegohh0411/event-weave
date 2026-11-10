from datetime import datetime, timedelta, timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from weave_core.db import make_engine
from weave_core.schema import sites, crawl_runs
from weave_providers.crawl.scheduler import schedule_due_sites, queue_check, AlreadyQueued, SiteNotFound


def test_scheduler_and_queue(database_url):
    engine = make_engine(database_url)
    now = datetime.now(timezone.utc)
    ids = [uuid4() for _ in range(3)]
    with engine.begin() as c:
        for i, id in enumerate(ids):
            c.execute(sites.insert().values(id=id, name=f'site{i}', domain=f'site{i}.com', language='en', enabled=i != 2, next_check_at=now + timedelta(minutes=1 if i == 1 else -1)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: schedule_due_sites(engine, now), range(2)))
    assert sum(map(len, results)) == 1
    with engine.connect() as c:
        assert c.scalar(sa.select(sites.c.next_check_at).where(sites.c.id == ids[0])) == now + timedelta(minutes=60)
        assert c.scalar(sa.select(sa.func.count()).select_from(crawl_runs)) == 1
    with pytest.raises(AlreadyQueued):
        queue_check(engine, ids[0])
    with pytest.raises(SiteNotFound):
        queue_check(engine, uuid4())
    engine.dispose()


def test_import_sites_idempotent(database_url, tmp_path, monkeypatch, capsys):
    from weave_providers.crawl import cli
    from types import SimpleNamespace
    path = tmp_path / 'outlets.yaml'
    path.write_text('outlets:\n  - name: Example\n    domain: example.com\n    language: en\n    feeds: [https://example.com/feed]\n    per_day: 7\n')
    monkeypatch.setattr(cli.Settings, 'from_env', lambda: SimpleNamespace(database_url=database_url))
    cli.import_sites(path)
    cli.import_sites(path)
    assert capsys.readouterr().out.splitlines() == ['inserted=1 skipped=0', 'inserted=0 skipped=1']
    engine = make_engine(database_url)
    with engine.connect() as conn:
        row = conn.execute(sa.select(sites)).mappings().one()
        assert row['per_day'] == 7 and row['daily_cap'] == 50
    engine.dispose()


def test_scheduler_tick_interval(monkeypatch):
    from weave_providers.crawl import worker
    from threading import Event
    ticks = iter([0, 10, 60, 61])
    stop = Event()
    calls = []
    monkeypatch.setattr(worker, 'schedule_due_sites', lambda engine, now: calls.append(now))
    def claim(engine, now):
        if len(calls) == 2:
            stop.set()
        return None
    monkeypatch.setattr(worker, 'claim_crawl', claim)
    monkeypatch.setenv('WEAVE_SCHEDULER_TICK_SECONDS', '60')
    worker.run_crawl_worker(None, stop, poll_seconds=0, clock=lambda: next(ticks))
    assert len(calls) == 2


def test_scheduler_limits_each_tick_to_bounded_batches(database_url, monkeypatch):
    from weave_providers.crawl import scheduler
    engine = make_engine(database_url)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        for i in range(5):
            conn.execute(sites.insert().values(id=uuid4(), name=f'batch{i}', domain=f'batch{i}.test',
                language='en', next_check_at=now - timedelta(minutes=5-i)))
    monkeypatch.setattr(scheduler, 'BATCH_SIZE', 2, raising=False)
    monkeypatch.setattr(scheduler, 'MAX_BATCHES', 2, raising=False)
    assert len(scheduler.schedule_due_sites(engine, now)) == 4
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(crawl_runs)) == 4
    engine.dispose()


def test_worker_recovers_after_scheduler_database_error(monkeypatch):
    from weave_providers.crawl import worker
    from threading import Event
    stop = Event()
    ticks = iter([0, 61])
    calls = []
    def schedule(engine, now):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError('database unavailable')
        stop.set()
    monkeypatch.setattr(worker, 'schedule_due_sites', schedule)
    monkeypatch.setattr(worker, 'claim_crawl', lambda *args: None)
    worker.run_crawl_worker(None, stop, poll_seconds=0, clock=lambda: next(ticks))
    assert len(calls) == 2


def test_backfill_and_check_queue_independently_and_prioritize_check(database_url):
    from weave_providers.crawl.scheduler import schedule_due_backfills
    from weave_providers.crawl.jobs import claim_crawl
    engine = make_engine(database_url)
    now = datetime.now(timezone.utc)
    site_id = uuid4()
    with engine.begin() as conn:
        conn.execute(sites.insert().values(id=site_id, name='archive', domain='archive.test', language='en',
            backfill_state='running', backfill_mode='all', backfill_until=now))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: schedule_due_backfills(engine, now), range(2)))
    assert sum(map(len, results)) == 1
    check = queue_check(engine, site_id)
    assert claim_crawl(engine, now).id == check
    assert schedule_due_backfills(engine, now) == []
    engine.dispose()


def test_backfill_scheduler_excludes_paused_disabled_and_exhausted(database_url):
    from weave_providers.crawl.scheduler import schedule_due_backfills
    from weave_core.schema import crawl_articles
    from weave_providers.crawl.jobs import create_crawl
    from weave_providers.crawl.requests import CrawlRequest
    engine = make_engine(database_url)
    now = datetime.now(timezone.utc)
    paused, disabled, exhausted = [uuid4() for _ in range(3)]
    with engine.begin() as conn:
        for id, state, enabled in [(paused, 'paused', True), (disabled, 'running', False), (exhausted, 'running', True)]:
            conn.execute(sites.insert().values(id=id, name=str(id), domain=f'{id}.test', language='en',
                enabled=enabled, backfill_state=state, backfill_per_hour=1))
    run = create_crawl(engine, CrawlRequest(sites=[{'domain': 'example.test', 'language': 'en'}]), site_id=exhausted, trigger='backfill')
    with engine.begin() as conn:
        conn.execute(crawl_runs.update().where(crawl_runs.c.id == run).values(status='completed'))
        conn.execute(crawl_articles.insert().values(crawl_id=run, canonical_url='https://example.test/a', url='https://example.test/a', outlet='example', status='stored', fetched=True, updated_at=now))
    assert schedule_due_backfills(engine, now) == []
    engine.dispose()
