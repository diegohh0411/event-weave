"""Crawl orchestration: discover, fetch politely, extract, and store with dedup."""
import gzip
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from weave_core.schema import crawled_pages, documents
from weave_providers.crawl.discover import Candidate, discover
from weave_providers.crawl.extract import Rejected, extract
from weave_providers.crawl.fetch import Fetcher, PoliteFetcher
from weave_providers.crawl.outlets import Outlet
from weave_providers.crawl.requests import CrawlSite
from weave_providers.crawl.site_sources import resolve_sources
from weave_providers.shape import content_hash, opaque_id, tokenize

PROVIDER = "crawl"
ROBOTS_DISALLOWED = "robots disallowed"
FETCH_ERROR = "fetch error: "
UNDATED_ORDER = 2**62
log = logging.getLogger(__name__)


@dataclass
class CrawlReport:
    discovered: int = 0
    fetched: int = 0
    stored: int = 0
    duplicates: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    discovery_errors: dict[str, int] = field(default_factory=dict)  # by outlet name


def crawl(engine: Engine, outlets: list[Outlet | CrawlSite], fetcher: Fetcher, now: datetime,
          days: int = 7, meta_fetcher: Fetcher | None = None, observer=None, cancelled=None,
          write_guard=None) -> CrawlReport:
    """Crawl configured outlets or CrawlSites, emitting ``(kind, outcome)`` events.

    Pass a source-less CrawlSite for automatic discovery; legacy Outlet/CLI
    validation remains unchanged. Public jobs must supply a PoliteFetcher with
    a public URL validator. Cancellation stops new requests and writes after an
    in-flight request returns; the observer must still enforce lease ownership.
    Public jobs also pass a write_guard that locks and checks ownership in each
    document/page transaction, serializing those writes with lease reclaim.
    """
    if not isinstance(fetcher, PoliteFetcher):
        fetcher = PoliteFetcher(fetcher, meta=meta_fetcher)
    previous_cancelled = fetcher.cancelled
    fetcher.cancelled = cancelled or (lambda: False)
    try:
        return _run_crawl(engine, outlets, fetcher, now, days, observer, fetcher.cancelled, write_guard)
    finally:
        fetcher.cancelled = previous_cancelled


def _run_crawl(engine, outlets, fetcher, now, days, observer, stopped, write_guard):
    meta = fetcher.meta_view()
    window = (now - timedelta(days=days), now)
    report = CrawlReport()
    rejected: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    seen_sources = set()
    def emit(kind, event):
        if observer and not stopped():
            observer(kind, event)
    for outlet in outlets:
        if stopped() or len(seen_sources) >= 100:
            break
        if fetcher.validator:
            fetcher.domain = outlet.domain
        if not outlet.sitemaps and not outlet.feeds:
            resolution = resolve_sources(outlet, meta, cancelled=stopped)
            if stopped():
                break
            for url, reason in resolution.errors:
                errors[outlet.name] += 1
                log.warning("discovery failed for %s (%s): %s", url, outlet.name, reason)
                # Record failed discovery probes as source outcomes, so their
                # diagnostics survive refresh. Kind identifies the probe purpose.
                if url not in seen_sources and len(seen_sources) < 100:
                    seen_sources.add(url)
                    kind = 'sitemap' if urlsplit(url).path == '/robots.txt' else 'feed'
                    emit('source', dict(site=outlet.domain, url=url, kind=kind,
                                        status='error', error=reason))
            if not resolution.sources:
                continue
            outlet = Outlet(outlet.name, outlet.domain, outlet.language,
                            sitemaps=[s.url for s in resolution.sources if s.kind == 'sitemap'],
                            feeds=[s.url for s in resolution.sources if s.kind == 'feed'],
                            per_day=outlet.per_day)
        def on_error(url: str, reason: str, name: str = outlet.name) -> None:
            errors[name] += 1
            log.warning("discovery failed for %s (%s): %s", url, name, reason)

        def on_source(url, kind, error):
            emit('source', dict(site=outlet.domain, url=url, kind=kind,
                                status='error' if error else 'completed', error=error))
        candidates = discover(outlet, meta, window, on_error, on_source, seen_sources, stopped)
        if stopped():
            break
        report.discovered += len(candidates)
        for candidate in candidates:
            if stopped():
                break
            prior = _prior_page(engine, candidate.url)
            if stopped():
                break
            if prior:
                status, reason, doc_id = prior
                if status == 'stored':
                    status = 'existing'
                elif reason and reason.startswith('duplicate of '):
                    status, doc_id = 'duplicate', reason.removeprefix('duplicate of ')
                else:
                    status = 'rejected'
                emit('article', dict(canonical_url=candidate.url, url=candidate.url,
                                     outlet=candidate.outlet, status=status, reason=reason,
                                     document_id=doc_id, fetched=False))
                continue
            outcome = _crawl_page(engine, candidate, outlet.language, fetcher, now, report, rejected, stopped,
                                  write_guard)
            if outcome is None:
                break
            emit('article', dict(canonical_url=candidate.url, url=candidate.url,
                                 outlet=candidate.outlet, **outcome))
    report.rejected = dict(rejected)
    report.discovery_errors = dict(errors)
    return report


def _prior_page(engine: Engine, canonical: str):
    """Seen before, unless the earlier attempt was a fetch error (those are retried)."""
    c = crawled_pages.c
    with engine.connect() as conn:
        row = conn.execute(sa.select(c.status, c.reason, c.document_id).where(c.canonical_url == canonical)).first()
    return row if row is not None and not (row.reason or "").startswith(FETCH_ERROR) else None


def _crawl_page(engine, candidate: Candidate, language: str, fetcher: Fetcher, now: datetime,
                report: CrawlReport, rejected: Counter, cancelled, write_guard) -> dict | None:
    got = fetcher.fetch(candidate.url)
    if cancelled():
        return None
    if got.error == ROBOTS_DISALLOWED:
        _record(engine, candidate, now, ROBOTS_DISALLOWED, None, rejected, write_guard=write_guard)
        return dict(status='rejected', reason=ROBOTS_DISALLOWED, document_id=None, fetched=False)
    if got.error or got.status == 0 or got.status >= 500 or (not got.html and got.status == 200):
        reason = FETCH_ERROR + (got.error or ("empty response" if got.status == 200 else f"HTTP {got.status}"))
        _record(engine, candidate, now, reason, None, rejected, count_as=FETCH_ERROR.rstrip(": "),
                write_guard=write_guard)
        return dict(status='error', reason=reason, document_id=None, fetched=False)
    if got.status != 200 or not got.html:
        _record(engine, candidate, now, f"HTTP {got.status}", None, rejected, write_guard=write_guard)
        return dict(status='rejected', reason=f'HTTP {got.status}', document_id=None, fetched=False)
    report.fetched += 1
    raw = gzip.compress(got.html.encode("utf-8"))
    result = extract(got.html, candidate.url, candidate.published_at)
    if cancelled():
        return None
    if isinstance(result, Rejected):
        _record(engine, candidate, now, result.reason, raw, rejected, write_guard=write_guard)
        return dict(status='rejected', reason=result.reason, document_id=None, fetched=True)
    digest = content_hash(result.text)
    published = result.published_at
    tokens, sentences = tokenize(result.text)
    external_id = opaque_id(candidate.url)
    doc_id = f"{PROVIDER}:{external_id}"
    with engine.begin() as conn:
        if write_guard:
            write_guard(conn)
        dup = conn.execute(sa.select(documents.c.id).where(
            documents.c.provider == PROVIDER, documents.c.content_hash == digest)).scalar()
        if cancelled():
            return None
        if dup is not None:
            _upsert_page(conn, candidate, now, "rejected", f"duplicate of {dup}", raw, None)
            report.duplicates += 1
            return dict(status='duplicate', reason=f'duplicate of {dup}', document_id=dup, fetched=True)
        conn.execute(sa.insert(documents).values(
            id=doc_id, provider=PROVIDER, external_id=external_id, language=language,
            title=result.title, byline=result.byline, outlet=candidate.outlet, url=candidate.url,
            published_at=published, text=result.text, token_offsets=tokens, sentence_offsets=sentences,
            content_hash=digest,
            order_key=[int(published.timestamp()) if published else UNDATED_ORDER]))
        _upsert_page(conn, candidate, now, "stored", None, raw, doc_id)
    report.stored += 1
    return dict(status="stored", reason=None, document_id=doc_id, fetched=True)


def _record(engine, candidate, now, reason, raw, rejected: Counter, count_as: str | None = None,
            write_guard=None) -> None:
    with engine.begin() as conn:
        if write_guard:
            write_guard(conn)
        _upsert_page(conn, candidate, now, "rejected", reason, raw, None)
    rejected[count_as or reason] += 1


def _upsert_page(conn, candidate: Candidate, now: datetime, status: str, reason: str | None,
                 raw: bytes | None, document_id: str | None) -> None:
    values = dict(canonical_url=candidate.url, url=candidate.url, outlet=candidate.outlet,
                  fetched_at=now.astimezone(timezone.utc), status=status, reason=reason,
                  raw_html_gz=raw, document_id=document_id)
    stmt = pg_insert(crawled_pages).values(**values)
    conn.execute(stmt.on_conflict_do_update(
        index_elements=[crawled_pages.c.canonical_url],
        set_={k: v for k, v in values.items() if k != "canonical_url"}))
