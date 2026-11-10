"""Consume public crawl jobs, retaining progress across lease expiry and shutdown."""
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
import time

import sqlalchemy as sa
from weave_core.schema import sites, crawl_runs, crawl_articles
from .scheduler import schedule_due_sites
from threading import Event, Thread

from .throttle import DomainThrottle
from .fetch import (HttpFetcher, PoliteFetcher, browser_fetcher_from_env,
                    respect_robots_from_env)
from .jobs import (LEASE_SECONDS, claim_crawl, finish_crawl, heartbeat, lock_owned,
                   record_article, record_source, release_crawl)
from .public_urls import validate_public_url
from .store import crawl

log = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 20


def _now():
    return datetime.now(timezone.utc)


@contextmanager
def public_fetchers():
    # PoliteFetcher enables guarding on the browser before it is started.
    with browser_fetcher_from_env() as browser, HttpFetcher(
            proxy=os.environ.get('WEAVE_CRAWL_EGRESS_PROXY')) as http:
        yield browser, http


class LeaseLost(Exception):
    """A newer owner (or an uncertain heartbeat) prevents further writes."""


def process_claim(engine, claim, stop, *, fetcher_factory=public_fetchers,
                  heartbeat_seconds=HEARTBEAT_SECONDS, min_interval=2.0):
    lost = Event()
    closing = Event()
    # Survive at least two missed renewals when the interval is tuned upwards.
    lease_seconds = max(LEASE_SECONDS, int(heartbeat_seconds * 3))

    def cancelled():
        return stop.is_set() or lost.is_set()

    def renew():
        try:
            if not heartbeat(engine, claim.id, claim.token, _now(), lease_seconds=lease_seconds):
                lost.set()
                return False
        except Exception:
            # Uncertain ownership must not authorize more work.
            log.exception('Crawl %s heartbeat failed', claim.id)
            lost.set()
            return False
        return True

    def heartbeat_loop():
        while not closing.wait(heartbeat_seconds):
            if not renew():
                break

    def guard(conn):
        # Lock in the same transaction as document/page writes. A check in a
        # separate transaction would race an expired lease being reclaimed.
        if cancelled() or not lock_owned(conn, claim.id, claim.token):
            lost.set()
            raise LeaseLost()

    def consume_cap(conn, candidate, doc_id, record=False):
        if record:
            conn.execute(crawl_articles.insert().values(crawl_id=claim.id, canonical_url=candidate.url,
                url=candidate.url, outlet=candidate.outlet, status='stored', document_id=doc_id,
                fetched=True, updated_at=sa.func.now()))
            return True
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:site AS text), 0))"), {'site': str(claim.site_id)})
        site = conn.execute(sa.select(sites).where(sites.c.id == claim.site_id)).mappings().one()
        count = conn.scalar(sa.text("""SELECT count(*) FROM crawl_articles a JOIN crawl_runs r ON r.id = a.crawl_id
            WHERE r.site_id = :site AND a.status = 'stored'
            AND a.updated_at >= (date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc')
            AND a.updated_at < (date_trunc('day', now() AT TIME ZONE 'utc') + interval '1 day') AT TIME ZONE 'utc'"""), {'site': claim.site_id})
        if count >= site['daily_cap']:
            return False
        return True

    def observe(kind, outcome):
        if cancelled():
            raise LeaseLost()
        writer = record_article if kind == 'article' else record_source
        if not writer(engine, claim.id, claim.token, outcome):
            lost.set()
            raise LeaseLost()

    if not renew():
        return
    heart = Thread(target=heartbeat_loop, name=f'crawl-heartbeat-{claim.id}', daemon=True)
    heart.start()
    error = None
    completed = False
    try:
        if not cancelled():
            caps = None
            if claim.site_id is not None:
                with engine.connect() as conn:
                    site = conn.execute(sa.select(sites).where(sites.c.id == claim.site_id)).mappings().first()
                    if site is not None:
                        count = conn.scalar(sa.select(sa.func.count()).select_from(
                            crawl_articles.join(crawl_runs, crawl_articles.c.crawl_id == crawl_runs.c.id)).where(
                            crawl_runs.c.site_id == claim.site_id, crawl_articles.c.status == "stored",
                            crawl_articles.c.updated_at >= sa.text("(date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc')"), crawl_articles.c.updated_at < sa.text("((date_trunc('day', now() AT TIME ZONE 'utc') + interval '1 day') AT TIME ZONE 'utc')")))
                        caps = {site["name"]: max(0, site["daily_cap"] - count)}
            with fetcher_factory() as (browser, http):
                fetcher = PoliteFetcher(browser, meta=http, validator=validate_public_url,
                                        min_interval=min_interval, respect_robots=respect_robots_from_env(),
                                        throttle=DomainThrottle(engine))
                # Reclaims must use the original discovery window, not a later one.
                crawl(engine, claim.request.sites, fetcher, claim.started_at, claim.request.days,
                      observer=observe, cancelled=cancelled, write_guard=guard, caps=caps,
                      consume_cap=consume_cap if claim.site_id is not None else None)
            completed = not cancelled()
    except LeaseLost:
        lost.set()
    except Exception as exc:
        log.exception('Crawl %s failed', claim.id)
        error = f'{type(exc).__name__}: {exc}'
    finally:
        closing.set()
        heart.join()

    if not lost.is_set():
        if stop.is_set() and not completed:
            release_crawl(engine, claim.id, claim.token, _now())
        else:
            finish_crawl(engine, claim.id, claim.token, error=error)


def run_crawl_worker(engine, stop, poll_seconds=2, once=False, fetcher_factory=public_fetchers,
                     *, min_interval=2.0, clock=time.monotonic):
    # Configuration errors must never consume queued work.
    respect_robots_from_env()
    tick_seconds = float(os.environ.get("WEAVE_SCHEDULER_TICK_SECONDS", "60"))
    last_tick = None
    while not stop.is_set():
        tick = clock()
        if last_tick is None or tick - last_tick >= tick_seconds:
            last_tick = tick
            try:
                schedule_due_sites(engine, _now())
            except Exception:
                log.exception("Crawl scheduler tick failed")
        try:
            claim = claim_crawl(engine, _now())
            if claim is not None:
                process_claim(engine, claim, stop, fetcher_factory=fetcher_factory,
                              min_interval=min_interval)
        except Exception:
            # A connection failure should not kill the long-lived polling process.
            # An unfinished claim becomes reclaimable when its lease expires.
            log.exception('Crawl worker could not process job')
            claim = None
        if once:
            return
        if claim is None:
            stop.wait(poll_seconds)
