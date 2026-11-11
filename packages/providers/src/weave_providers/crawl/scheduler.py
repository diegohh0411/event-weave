"""Queue site checks under row locks shared by schedulers and manual requests."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import sqlalchemy as sa

from weave_core.schema import backfill_sitemaps, backfill_urls, crawl_runs, crawled_pages, sites
from .backfill import budget_left
from .requests import CrawlRequest


class AlreadyQueued(Exception):
    pass


class SiteNotFound(Exception):
    pass


BACKGROUND = ('backfill', 'recheck')


def _same_kind(trigger):
    """Checks, backfill and rechecks each allow one active run per site."""
    if trigger in BACKGROUND:
        return crawl_runs.c.trigger == trigger
    return crawl_runs.c.trigger.not_in(BACKGROUND)


def _active(trigger):
    return sa.exists(sa.select(crawl_runs.c.id).where(crawl_runs.c.site_id == sites.c.id, _same_kind(trigger),
                                                      crawl_runs.c.status.in_(['queued', 'running'])))


def _queue(conn, site, trigger, now):
    active = conn.scalar(sa.select(crawl_runs.c.id).where(
        crawl_runs.c.site_id == site['id'], crawl_runs.c.status.in_(['queued', 'running']),
        _same_kind(trigger)).limit(1))
    if active is not None:
        raise AlreadyQueued(site['id'])
    fields = {key: site[key] for key in ('name', 'domain', 'language', 'feeds', 'sitemaps', 'per_day')}
    request = CrawlRequest(sites=[fields], days=1)
    crawl_id = uuid4()
    conn.execute(crawl_runs.insert().values(id=crawl_id, site_id=site['id'], trigger=trigger,
        request=request.model_dump(mode='json'), status='queued', created_at=now, progress={}))
    return crawl_id


BATCH_SIZE = 20
MAX_BATCHES = 10


def schedule_due_sites(engine, now):
    queued = []
    for _ in range(MAX_BATCHES):
        with engine.begin() as conn:
            rows = conn.execute(sa.select(sites).where(sites.c.enabled.is_(True),
                sites.c.next_check_at <= now, ~_active('schedule')).order_by(sites.c.next_check_at, sites.c.id)
                .limit(BATCH_SIZE).with_for_update(skip_locked=True)).mappings().all()
            for site in rows:
                try:
                    crawl_id = _queue(conn, site, 'schedule', now)
                except AlreadyQueued:
                    continue
                conn.execute(sites.update().where(sites.c.id == site['id']).values(
                    last_scheduled_at=now, next_check_at=now + timedelta(minutes=site['check_every_minutes'])))
                queued.append(crawl_id)
        if len(rows) < BATCH_SIZE:
            break
    return queued


def schedule_due_backfills(engine, now):
    queued, examined = [], []
    for _ in range(MAX_BATCHES):
        with engine.begin() as conn:
            rows = conn.execute(sa.select(sites).where(sites.c.enabled.is_(True),
                sites.c.backfill_state == 'running', ~_active('backfill'), sites.c.id.not_in(examined))
                .order_by(sites.c.id).limit(BATCH_SIZE).with_for_update(skip_locked=True)).mappings().all()
            for site in rows:
                examined.append(site['id'])
                pending = conn.scalar(sa.select(sa.exists().where(
                    backfill_sitemaps.c.site_id == site['id'], backfill_sitemaps.c.status == 'pending')))
                if pending or budget_left(conn, site['id']) > 0:
                    queued.append(_queue(conn, site, 'backfill', now))
        if len(rows) < BATCH_SIZE:
            break
    return queued


RECHECK_BATCH = 20


def due_pages_query(site, now):
    p = crawled_pages.c
    query = sa.select(crawled_pages).where(p.outlet == site['name'], p.status == 'stored',
                                           p.recheck_due_at.is_not(None), p.recheck_due_at <= now)
    if not site['recheck_backfilled']:
        query = query.where(~sa.exists().where(backfill_urls.c.site_id == site['id'],
            backfill_urls.c.canonical_url == p.canonical_url, backfill_urls.c.outcome == 'stored'))
    return query.order_by(p.recheck_due_at, p.canonical_url)


def schedule_due_rechecks(engine, now):
    queued, examined = [], []
    for _ in range(MAX_BATCHES):
        with engine.begin() as conn:
            rows = conn.execute(sa.select(sites).where(sites.c.enabled.is_(True), ~_active('recheck'),
                sites.c.id.not_in(examined)).order_by(sites.c.id).limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)).mappings().all()
            for site in rows:
                examined.append(site['id'])
                if conn.execute(due_pages_query(site, now).limit(1)).first() is not None:
                    queued.append(_queue(conn, site, 'recheck', now))
        if len(rows) < BATCH_SIZE:
            break
    return queued


def queue_check(engine, site_id, trigger='manual'):
    with engine.begin() as conn:
        site = conn.execute(sa.select(sites).where(sites.c.id == site_id).with_for_update()).mappings().first()
        if site is None:
            raise SiteNotFound(site_id)
        return _queue(conn, site, trigger, datetime.now(timezone.utc))
