"""Consume public crawl jobs, retaining progress across lease expiry and shutdown."""
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event, Thread

from .fetch import Crawl4aiFetcher, HttpFetcher, PoliteFetcher
from .jobs import (claim_crawl, finish_crawl, heartbeat, lock_owned, record_article,
                   record_source, release_crawl)
from .public_urls import validate_public_url
from .store import crawl

log = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 20


def _now():
    return datetime.now(timezone.utc)


@contextmanager
def public_fetchers():
    # PoliteFetcher enables guarding on the browser before it is started.
    with Crawl4aiFetcher() as browser, HttpFetcher() as http:
        yield browser, http


class LeaseLost(Exception):
    """A newer owner (or an uncertain heartbeat) prevents further writes."""


def process_claim(engine, claim, stop, *, fetcher_factory=public_fetchers,
                  heartbeat_seconds=HEARTBEAT_SECONDS, min_interval=2.0):
    lost = Event()
    closing = Event()

    def cancelled():
        return stop.is_set() or lost.is_set()

    def renew():
        try:
            if not heartbeat(engine, claim.id, claim.token, _now()):
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
    try:
        if not cancelled():
            with fetcher_factory() as (browser, http):
                fetcher = PoliteFetcher(browser, meta=http, validator=validate_public_url,
                                        min_interval=min_interval)
                # Reclaims must use the original discovery window, not a later one.
                crawl(engine, claim.request.sites, fetcher, claim.started_at, claim.request.days,
                      observer=observe, cancelled=cancelled, write_guard=guard)
    except LeaseLost:
        lost.set()
    except Exception as exc:
        log.exception('Crawl %s failed', claim.id)
        error = f'{type(exc).__name__}: {exc}'
    finally:
        closing.set()
        heart.join()

    if stop.is_set():
        release_crawl(engine, claim.id, claim.token, _now())
    elif not lost.is_set():
        finish_crawl(engine, claim.id, claim.token, error=error)


def run_crawl_worker(engine, stop, poll_seconds=2, once=False, fetcher_factory=public_fetchers,
                     *, min_interval=2.0):
    while not stop.is_set():
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
