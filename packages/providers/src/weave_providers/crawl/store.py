"""Crawl orchestration: discover, fetch politely, extract, and store with dedup."""
import gzip
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from weave_core.schema import crawled_pages, documents
from weave_providers.crawl.discover import Candidate, discover
from weave_providers.crawl.extract import Rejected, extract
from weave_providers.crawl.fetch import Fetcher, PoliteFetcher
from weave_providers.crawl.outlets import Outlet
from weave_providers.shape import content_hash, opaque_id, tokenize

PROVIDER = "crawl"
ROBOTS_DISALLOWED = "robots disallowed"
FETCH_ERROR = "fetch error: "
UNDATED_ORDER = 2**62


@dataclass
class CrawlReport:
    discovered: int = 0
    fetched: int = 0
    stored: int = 0
    duplicates: int = 0
    rejected: dict[str, int] = field(default_factory=dict)


def crawl(engine: Engine, outlets: list[Outlet], fetcher: Fetcher, now: datetime,
          days: int = 7) -> CrawlReport:
    if not isinstance(fetcher, PoliteFetcher):
        fetcher = PoliteFetcher(fetcher)
    window = (now - timedelta(days=days), now)
    report = CrawlReport()
    rejected: Counter[str] = Counter()
    language = {o.name: o.language for o in outlets}
    for outlet in outlets:
        candidates = discover(outlet, fetcher, window)
        report.discovered += len(candidates)
        for candidate in candidates:
            if _already_crawled(engine, candidate.url):
                continue
            _crawl_page(engine, candidate, language[candidate.outlet], fetcher, now, report, rejected)
    report.rejected = dict(rejected)
    return report


def _already_crawled(engine: Engine, canonical: str) -> bool:
    """Seen before, unless the earlier attempt was a fetch error (those are retried)."""
    c = crawled_pages.c
    with engine.connect() as conn:
        row = conn.execute(sa.select(c.status, c.reason).where(c.canonical_url == canonical)).first()
    return row is not None and not (row.reason or "").startswith(FETCH_ERROR)


def _crawl_page(engine, candidate: Candidate, language: str, fetcher: Fetcher, now: datetime,
                report: CrawlReport, rejected: Counter) -> None:
    got = fetcher.fetch(candidate.url)
    if got.error == ROBOTS_DISALLOWED:
        _record(engine, candidate, now, ROBOTS_DISALLOWED, None, rejected)
        return
    if got.error or got.status == 0 or got.status >= 500 or (not got.html and got.status == 200):
        reason = FETCH_ERROR + (got.error or ("empty response" if got.status == 200 else f"HTTP {got.status}"))
        _record(engine, candidate, now, reason, None, rejected, count_as=FETCH_ERROR.rstrip(": "))
        return
    if got.status != 200 or not got.html:
        _record(engine, candidate, now, f"HTTP {got.status}", None, rejected)
        return
    report.fetched += 1
    raw = gzip.compress(got.html.encode("utf-8"))
    result = extract(got.html, candidate.url, candidate.published_at)
    if isinstance(result, Rejected):
        _record(engine, candidate, now, result.reason, raw, rejected)
        return
    digest = content_hash(result.text)
    published = result.published_at
    tokens, sentences = tokenize(result.text)
    external_id = opaque_id(candidate.url)
    doc_id = f"{PROVIDER}:{external_id}"
    with engine.begin() as conn:
        dup = conn.execute(sa.select(documents.c.id).where(
            documents.c.provider == PROVIDER, documents.c.content_hash == digest)).scalar()
        if dup is not None:
            _upsert_page(conn, candidate, now, "rejected", f"duplicate of {dup}", raw, None)
            report.duplicates += 1
            return
        conn.execute(sa.insert(documents).values(
            id=doc_id, provider=PROVIDER, external_id=external_id, language=language,
            title=result.title, byline=result.byline, outlet=candidate.outlet, url=candidate.url,
            published_at=published, text=result.text, token_offsets=tokens, sentence_offsets=sentences,
            content_hash=digest,
            order_key=[int(published.timestamp()) if published else UNDATED_ORDER]))
        _upsert_page(conn, candidate, now, "stored", None, raw, doc_id)
    report.stored += 1


def _record(engine, candidate, now, reason, raw, rejected: Counter, count_as: str | None = None) -> None:
    with engine.begin() as conn:
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
