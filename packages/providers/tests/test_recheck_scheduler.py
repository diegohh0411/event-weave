from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
import sqlalchemy as sa

from weave_core.db import make_engine
from weave_core.schema import backfill_urls, crawl_articles, crawl_runs, crawled_pages, page_rechecks, sites
from weave_providers.crawl import worker
from weave_providers.crawl.fetch import Fetched, FixtureFetcher, PoliteFetcher
from weave_providers.crawl.jobs import claim_crawl
from weave_providers.crawl.outlets import Outlet
from weave_providers.crawl.scheduler import RECHECK_BATCH, _queue, due_pages_query, schedule_due_rechecks
from weave_providers.crawl.store import crawl

BASE = "https://example.test"
NAME = "example"
ARTICLE = (Path(__file__).parent / "fixtures" / "reuters" / "article.html").read_text(encoding="utf-8")
STORED_AT = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
LATER = STORED_AT + timedelta(days=2)


def html(i):
    return ARTICLE.replace("roughly forty kilometres", f"roughly {i + 10} kilometres")


def routes(n, edit=(), gone=()):
    sitemap = "".join(f"<url><loc>{BASE}/a/{i}</loc><lastmod>2026-10-31T10:00:00Z</lastmod></url>" for i in range(n))
    table = {f"{BASE}/robots.txt": Fetched("", 200, "User-agent: *\nAllow: /", None),
             f"{BASE}/sitemap.xml": Fetched("", 200, f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sitemap}</urlset>', None)}
    for i in range(n):
        body = html(i).replace("regional water board", "regional water authority") if i in edit else html(i)
        table[f"{BASE}/a/{i}"] = Fetched("", 404, None, None) if i in gone else Fetched("", 200, body, None)
    return table


@pytest.fixture
def engine(database_url, monkeypatch):
    monkeypatch.setattr(worker, "validate_public_url", lambda url: url)
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


def make_site(engine, n=3, **values):
    values.setdefault("min_interval_seconds", 0.5)
    site_id = uuid4()
    with engine.begin() as conn:
        conn.execute(sites.insert().values(id=site_id, name=NAME, domain="example.test", language="en",
                                           sitemaps=[f"{BASE}/sitemap.xml"], next_check_at=LATER + timedelta(days=9), **values))
    fetcher = PoliteFetcher(FixtureFetcher(routes(n)), min_interval=0, clock=lambda: 0.0, sleep=lambda s: None)
    crawl(engine, [Outlet(NAME, "example.test", "en", sitemaps=[f"{BASE}/sitemap.xml"])], fetcher, STORED_AT)
    return site_id


def site_row(engine, site_id):
    with engine.connect() as conn:
        return conn.execute(sa.select(sites).where(sites.c.id == site_id)).mappings().one()


def due(engine, site_id, now=LATER):
    with engine.connect() as conn:
        return {r.canonical_url for r in conn.execute(due_pages_query(site_row(engine, site_id), now))}


def runs(engine, trigger=None):
    with engine.connect() as conn:
        query = sa.select(crawl_runs.c.id, crawl_runs.c.trigger)
        return conn.execute(query.where(crawl_runs.c.trigger == trigger) if trigger else query).all()


def test_due_site_queued_once(engine):
    make_site(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: schedule_due_rechecks(engine, LATER), range(2)))
    assert sum(map(len, results)) == 1
    assert len(runs(engine, "recheck")) == 1


def test_not_queued_when_nothing_due(engine):
    make_site(engine)
    assert schedule_due_rechecks(engine, STORED_AT + timedelta(hours=1)) == []


def test_not_queued_when_disabled(engine):
    make_site(engine, enabled=False)
    assert schedule_due_rechecks(engine, LATER) == []


def test_recheck_coexists_with_check_and_backfill(engine):
    site_id = make_site(engine)
    with engine.begin() as conn:
        site = conn.execute(sa.select(sites).where(sites.c.id == site_id)).mappings().one()
        for trigger in ("manual", "backfill"):
            _queue(conn, site, trigger, LATER)
    assert len(schedule_due_rechecks(engine, LATER)) == 1
    assert schedule_due_rechecks(engine, LATER) == []
    assert sorted(t for _, t in runs(engine)) == ["backfill", "manual", "recheck"]


def test_claim_order(engine):
    site_id = make_site(engine)
    with engine.begin() as conn:
        site = conn.execute(sa.select(sites).where(sites.c.id == site_id)).mappings().one()
        for minutes, trigger in ((0, "backfill"), (1, "recheck"), (2, "manual")):
            _queue(conn, site, trigger, LATER + timedelta(minutes=minutes))
    claimed = [claim_crawl(engine, LATER + timedelta(minutes=5)).trigger for _ in range(3)]
    assert claimed == ["manual", "recheck", "backfill"]


def test_backfill_toggle_is_dynamic(engine):
    site_id = make_site(engine, n=2)
    backfilled = f"{BASE}/a/0"
    with engine.begin() as conn:
        conn.execute(backfill_urls.insert().values(site_id=site_id, canonical_url=backfilled, status="done", outcome="stored"))
        conn.execute(sites.update().values(recheck_backfilled=False))
    assert due(engine, site_id) == {f"{BASE}/a/1"}
    with engine.begin() as conn:
        conn.execute(sites.update().values(recheck_backfilled=True))
    assert due(engine, site_id) == {backfilled, f"{BASE}/a/1"}


@contextmanager
def fixture_fetchers(table):
    yield FixtureFetcher(table), FixtureFetcher(table)


def process(engine, table, now=LATER, stop=None):
    claim = claim_crawl(engine, now)
    assert claim.trigger == "recheck"
    worker.process_claim(engine, claim, stop or Event(), fetcher_factory=lambda: fixture_fetchers(table), min_interval=0)
    return claim


def test_worker_processes_batch(engine):
    n = RECHECK_BATCH + 5
    make_site(engine, n=n)
    schedule_due_rechecks(engine, LATER)
    claim = process(engine, routes(n, edit={0}, gone={1}))
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(page_rechecks)) == RECHECK_BATCH
        statuses = dict(conn.execute(sa.select(crawl_articles.c.status, sa.func.count())
                                     .where(crawl_articles.c.crawl_id == claim.id).group_by(crawl_articles.c.status)).all())
        assert conn.scalar(sa.select(crawl_runs.c.status).where(crawl_runs.c.id == claim.id)) == "completed"
    assert statuses == {"updated": 1, "removed": 1, "unchanged": RECHECK_BATCH - 2}
    assert len(due(engine, claim.site_id)) == 5


def test_rechecks_ignore_daily_cap(engine):
    site_id = make_site(engine, daily_cap=1)
    with engine.begin() as conn:
        crawl_id = uuid4()
        conn.execute(crawl_runs.insert().values(id=crawl_id, site_id=site_id, trigger="schedule", request={},
                                                status="completed", created_at=LATER, progress={}))
        conn.execute(crawl_articles.insert().values(crawl_id=crawl_id, canonical_url=f"{BASE}/x", outlet=NAME,
                                                    url=f"{BASE}/x", status="stored", fetched=True, updated_at=sa.func.now()))
    schedule_due_rechecks(engine, LATER)
    process(engine, routes(3))
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(page_rechecks)) == 3


def test_lease_loss_mid_batch(engine, monkeypatch):
    site_id = make_site(engine, n=3)
    schedule_due_rechecks(engine, LATER)
    real = worker.recheck_page
    calls = []

    def lose_after_first(engine_, page, *args, **kwargs):
        if calls:
            with engine.begin() as conn:
                conn.execute(crawl_runs.update().values(claim_token=uuid4()))
        calls.append(page["canonical_url"])
        return real(engine_, page, *args, **kwargs)

    monkeypatch.setattr(worker, "recheck_page", lose_after_first)
    process(engine, routes(3))
    assert len(due(engine, site_id)) == 2


def test_invalid_recheck_days_fails_startup(engine, monkeypatch):
    monkeypatch.setenv("WEAVE_RECHECK_DAYS", "7,1")
    monkeypatch.setattr(worker, "claim_crawl", lambda *a: pytest.fail("claimed work"))
    with pytest.raises(ValueError):
        worker.run_crawl_worker(engine, Event(), once=True)


def test_page_exception_is_recorded_and_batch_continues(engine, monkeypatch):
    site_id = make_site(engine, n=3)
    schedule_due_rechecks(engine, LATER)
    real = worker.recheck_page

    def boom(engine_, page, *args, **kwargs):
        if page["canonical_url"].endswith("/a/0"):
            raise RuntimeError("extractor bug")
        return real(engine_, page, *args, **kwargs)

    monkeypatch.setattr(worker, "recheck_page", boom)
    claim = process(engine, routes(3))
    with engine.connect() as conn:
        assert conn.scalar(sa.select(crawl_runs.c.status).where(crawl_runs.c.id == claim.id)) == "completed"
        failed = conn.execute(sa.select(crawled_pages).where(crawled_pages.c.canonical_url == f"{BASE}/a/0")).mappings().one()
        outcomes = [r.outcome for r in conn.execute(sa.select(page_rechecks.c.outcome).order_by(page_rechecks.c.id))]
    assert failed["recheck_attempts"] == 1 and failed["recheck_due_at"] > datetime.now(timezone.utc)
    assert sorted(outcomes) == ["error", "unchanged", "unchanged"]
