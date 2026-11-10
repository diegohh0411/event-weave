"""Persistent sitemap frontier for historical discovery."""
import os
import xml.etree.ElementTree as ET
from datetime import datetime, time, timezone
from types import SimpleNamespace

import sqlalchemy as sa
from defusedxml import DefusedXmlException
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
        for url in roots:
            conn.execute(insert(backfill_sitemaps).values(site_id=site.id, url=url, depth=0, status='pending').on_conflict_do_nothing())


def scan_sitemaps(engine, site, fetcher, limit, cancelled):
    cap = int(os.environ.get('WEAVE_BACKFILL_MAX_SITEMAPS', '5000'))
    start = datetime.combine(site.backfill_from, time.min, tzinfo=timezone.utc) if site.backfill_mode == 'date' and site.backfill_from else None
    with engine.begin() as conn:
        # Serialize insertions for a site so concurrent scanners respect the cap.
        conn.execute(sa.select(sites.c.id).where(sites.c.id == site.id).with_for_update())
        rows = conn.execute(sa.select(backfill_sitemaps).where(
            backfill_sitemaps.c.site_id == site.id, backfill_sitemaps.c.status == 'pending'
        ).order_by(backfill_sitemaps.c.depth, backfill_sitemaps.c.url).limit(limit).with_for_update(skip_locked=True)).mappings().all()
        count = conn.scalar(sa.select(sa.func.count()).select_from(backfill_sitemaps).where(
            backfill_sitemaps.c.site_id == site.id, backfill_sitemaps.c.status != 'skipped'))
        for row in rows:
            if cancelled():
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
