"""Persistent sitemap frontier for historical discovery."""
import os
import xml.etree.ElementTree as ET
from datetime import datetime, time, timezone
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from weave_core.schema import backfill_sitemaps, backfill_urls, sites
from weave_providers.crawl.discover import _on_domain, _sitemap_entries, canonical_url
from weave_providers.crawl.site_sources import resolve_sources


def seed_frontier(engine, site, meta_fetcher):
    roots = site.sitemaps
    if not roots:
        source_site = SimpleNamespace(domain=site.domain, sitemaps=[], feeds=[])
        roots = [source.url for source in resolve_sources(source_site, meta_fetcher).sources if source.kind == 'sitemap']
    with engine.begin() as conn:
        conn.execute(sa.select(sites.c.id).where(sites.c.id == site.id).with_for_update())
        cap = int(os.environ.get('WEAVE_BACKFILL_MAX_SITEMAPS', '5000'))
        count = conn.scalar(sa.select(sa.func.count()).select_from(backfill_sitemaps).where(
            backfill_sitemaps.c.site_id == site.id, backfill_sitemaps.c.status != 'skipped'))
        for url in roots:
            status = 'pending' if count < cap else 'skipped'
            result = conn.execute(insert(backfill_sitemaps).values(site_id=site.id, url=url, depth=0, status=status).on_conflict_do_nothing())
            if result.rowcount and status == 'pending':
                count += 1


def scan_sitemaps(engine, site, fetcher, limit, cancelled, write_guard=None, running=lambda: True):
    cap = int(os.environ.get('WEAVE_BACKFILL_MAX_SITEMAPS', '5000'))
    start = datetime.combine(site.backfill_from, time.min, tzinfo=timezone.utc) if site.backfill_mode == 'date' and site.backfill_from else None
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as lock:
        lock.execute(sa.text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {'key': 'backfill-map:' + str(site.id)})
        try:
            _scan_locked(engine, site, fetcher, limit, cancelled, write_guard, running, cap, start)
        finally:
            lock.execute(sa.text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {'key': 'backfill-map:' + str(site.id)})


def _scan_locked(engine, site, fetcher, limit, cancelled, write_guard, running, cap, start):
    from defusedxml import DefusedXmlException  # extra `crawl`; the API imports this module without it

    with engine.begin() as conn:
        rows = conn.execute(sa.select(backfill_sitemaps).where(
            backfill_sitemaps.c.site_id == site.id, backfill_sitemaps.c.status == 'pending'
        ).order_by(backfill_sitemaps.c.depth, backfill_sitemaps.c.url).limit(limit)).mappings().all()
    for row in rows:
        if cancelled() or not running():
            break
        got = fetcher.fetch(row['url'])
        if cancelled():
            break
        error = None
        children, entries = [], []
        if got.status != 200 or not got.html:
            error = got.error or ('empty response' if got.status == 200 else f'HTTP {got.status}')
        else:
            try:
                children, entries = _sitemap_entries(got.html)
            except (ET.ParseError, DefusedXmlException) as exc:
                error = f'XML {type(exc).__name__}: {exc}'
        with engine.begin() as conn:
            if write_guard:
                write_guard(conn)
            conn.execute(sa.select(sites.c.id).where(sites.c.id == site.id).with_for_update())
            count = conn.scalar(sa.select(sa.func.count()).select_from(backfill_sitemaps).where(
                backfill_sitemaps.c.site_id == site.id, backfill_sitemaps.c.status != 'skipped'))
            for url, lastmod in children:
                skipped = row['depth'] >= 3 or count >= cap or (start is not None and lastmod is not None and lastmod < start)
                result = conn.execute(insert(backfill_sitemaps).values(site_id=site.id, url=url, depth=row['depth'] + 1,
                    lastmod=lastmod, status='skipped' if skipped else 'pending').on_conflict_do_nothing())
                if result.rowcount and not skipped:
                    count += 1
            found = 0
            for url, published, title, updated in entries:
                if not _on_domain(url, site.domain) or (published is None and site.backfill_mode != 'all'):
                    continue
                if published is not None and ((start is not None and published < start) or (site.backfill_until and published > site.backfill_until)):
                    continue
                result = conn.execute(insert(backfill_urls).values(site_id=site.id, canonical_url=canonical_url(url),
                    published_at=published, updated_at=updated, title=title, sitemap_url=row['url'], status='pending').on_conflict_do_nothing())
                found += result.rowcount
            conn.execute(backfill_sitemaps.update().where(backfill_sitemaps.c.site_id == site.id,
                backfill_sitemaps.c.url == row['url']).values(status='error' if error else 'done', error=error,
                    urls_found=found, scanned_at=sa.func.now()))


def budget_left(conn, site_id):
    from weave_core.schema import crawl_articles, crawl_runs
    fetched = conn.scalar(sa.select(sa.func.count()).select_from(
        crawl_articles.join(crawl_runs, crawl_articles.c.crawl_id == crawl_runs.c.id)
    ).where(crawl_runs.c.site_id == site_id, crawl_runs.c.trigger == 'backfill',
            crawl_articles.c.fetched.is_(True),
            crawl_articles.c.updated_at >= sa.func.now() - sa.text("interval '1 hour'")))
    pace = conn.scalar(sa.select(sites.c.backfill_per_hour).where(sites.c.id == site_id))
    return max(0, pace - fetched)


def fetch_batch(engine, site, fetcher, limit, now, emit, cancelled, write_guard):
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as lock:
        key = 'backfill-url:' + str(site.id)
        lock.execute(sa.text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {'key': key})
        try:
            _fetch_locked(engine, site, fetcher, limit, now, emit, cancelled, write_guard)
        finally:
            lock.execute(sa.text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {'key': key})


def _fetch_locked(engine, site, fetcher, limit, now, emit, cancelled, write_guard):
    from weave_core.schema import crawled_pages
    from weave_providers.crawl.discover import Candidate
    from weave_providers.crawl.store import CrawlReport, process_candidates

    remaining = None
    for _ in range(limit):
        with engine.begin() as conn:
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {'key': 'backfill-budget:' + str(site.id)})
            row = conn.execute(sa.select(backfill_urls).where(
                backfill_urls.c.site_id == site.id, backfill_urls.c.status == 'pending'
            ).order_by(backfill_urls.c.published_at.desc().nulls_last(), backfill_urls.c.canonical_url
            ).limit(1).with_for_update(skip_locked=True)).mappings().first()
            state = conn.scalar(sa.select(sites.c.backfill_state).where(sites.c.id == site.id))
            budget = budget_left(conn, site.id)
            known = row is not None and conn.scalar(sa.select(sa.exists().where(crawled_pages.c.canonical_url == row['canonical_url'])))
        remaining = budget if remaining is None else min(remaining, budget)
        if row is None or cancelled() or state != 'running' or (remaining <= 0 and not known):
            break
        candidate = Candidate(row['canonical_url'], site.name, row['published_at'], row['title'], row['updated_at'])
        outcomes = []
        process_candidates(engine, site, [candidate], fetcher, now, CrawlReport(),
                           lambda kind, outcome: outcomes.append(outcome), cancelled,
                           write_guard, refetch_known=False)
        if not outcomes or cancelled():
            break
        outcome = outcomes[0]
        if outcome['status'] == 'rejected' and (outcome.get('reason') or '').startswith('HTTP '):
            outcome.update(status='error', fetched=True)
        if outcome['status'] == 'error':
            outcome['fetched'] = True
        emit('article', outcome)
        with engine.begin() as conn:
            if write_guard:
                write_guard(conn)
            conn.execute(backfill_urls.update().where(backfill_urls.c.site_id == site.id,
                backfill_urls.c.canonical_url == candidate.url).values(status='done', outcome=outcome['status'],
                document_id=outcome.get('document_id'), crawl_id=getattr(site, 'crawl_id', None), done_at=sa.func.now()))
        if outcome['fetched']:
            remaining -= 1
