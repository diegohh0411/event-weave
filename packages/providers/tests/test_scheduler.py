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
