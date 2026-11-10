"""Decaying rechecks: re-fetch a stored page and record whether it was edited, removed or moved."""
import gzip
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from weave_core.schema import crawled_pages, documents, page_rechecks
from weave_providers.crawl.discover import Candidate, canonical_url
from weave_providers.crawl.extract import Rejected, extract
from weave_providers.crawl.fetch import REDIRECTED_AWAY
from weave_providers.crawl.store import FETCH_ERROR, apply_text

DEFAULT_DAYS = "1,7,30"
RETRY_HOURS = 6
MAX_ATTEMPTS = 3
# crawl_articles status for each recheck outcome.
ARTICLE_STATUS = {"unchanged": "unchanged", "edited": "updated", "gone": "removed",
                  "redirected": "removed", "error": "error"}


def recheck_days() -> list[int]:
    raw = os.environ.get("WEAVE_RECHECK_DAYS", DEFAULT_DAYS)
    try:
        days = [int(part) for part in raw.split(",")]
    except ValueError:
        raise ValueError(f"WEAVE_RECHECK_DAYS must be comma-separated days, got {raw!r}") from None
    if not days or days[0] < 1 or any(b <= a for a, b in zip(days, days[1:])):
        raise ValueError(f"WEAVE_RECHECK_DAYS must be ascending positive days, got {raw!r}")
    return days


def next_due(first_fetched_at: datetime, stage: int, days: list[int]) -> datetime | None:
    return first_fetched_at + timedelta(days=days[stage]) if stage < len(days) else None


def recheck_page(engine, page, fetcher, now, days, crawl_id, write_guard, cancelled) -> dict | None:
    got = fetcher.fetch(page["url"])
    if cancelled():
        return None
    now = now.astimezone(timezone.utc)
    final = got.final_url if got.final_url and canonical_url(got.final_url) != canonical_url(page["url"]) else None
    doc_id = page["document_id"]
    entry = dict(http_status=got.status or None, final_url=got.final_url if got.final_url != page["url"] else None)

    def write(apply):
        with engine.begin() as conn:
            if write_guard:
                write_guard(conn)
            outcome, values, reason, current = apply(conn)
            conn.execute(crawled_pages.update().where(crawled_pages.c.canonical_url == page["canonical_url"]).values(**values))
            conn.execute(page_rechecks.insert().values(canonical_url=page["canonical_url"], crawl_id=crawl_id,
                stage=page["recheck_stage"], checked_at=now, outcome=outcome, reason=reason, document_id=current, **entry))
        return dict(status=ARTICLE_STATUS[outcome], reason=reason, document_id=current,
                    fetched=outcome not in ("error",))

    def gone(reason, outcome):
        values = dict(status="gone", gone_at=now, gone_reason=reason, recheck_due_at=None, recheck_attempts=0)
        return write(lambda conn: (outcome, values, reason, doc_id))

    def advance(stage=None):
        stage = page["recheck_stage"] + 1 if stage is None else stage
        first = page["first_fetched_at"] or page["fetched_at"]
        return dict(recheck_stage=stage, recheck_attempts=0, recheck_due_at=next_due(first, stage, days))

    if got.status in (404, 410):
        return gone(f"HTTP {got.status}", "gone")
    if got.error == REDIRECTED_AWAY:
        return gone(f"redirected to {got.final_url}", "redirected")
    if got.error or got.status != 200 or not got.html:
        attempts = page["recheck_attempts"] + 1
        values = advance() if attempts >= MAX_ATTEMPTS else dict(
            recheck_attempts=attempts, recheck_due_at=now + timedelta(hours=RETRY_HOURS))
        reason = FETCH_ERROR + (got.error or f"HTTP {got.status}")
        return write(lambda conn: ("error", values, reason, doc_id))

    with engine.connect() as conn:
        published = conn.scalar(sa.select(documents.c.published_at).where(documents.c.id == doc_id))
    result = extract(got.html, final or page["url"], published)
    if cancelled():
        return None
    if final:
        if urlsplit(final).path in ("", "/") or isinstance(result, Rejected):
            return gone(f"redirected to {final}", "redirected")
        with engine.connect() as conn:
            known = conn.execute(sa.select(crawled_pages.c.document_id, documents.c.article_id)
                .join(documents, documents.c.id == crawled_pages.c.document_id, isouter=True)
                .where(crawled_pages.c.canonical_url == canonical_url(final))).first()
            article = conn.scalar(sa.select(documents.c.article_id).where(documents.c.id == doc_id))
        if known is not None and known.article_id != article:
            return gone(f"redirect to known document {known.document_id}", "redirected")
    if isinstance(result, Rejected):
        return write(lambda conn: ("unchanged", advance(), f"refetch rejected: {result.reason}", doc_id))
    raw = gzip.compress(got.html.encode("utf-8"))
    candidate = Candidate(page["canonical_url"], page["outlet"], published, None)

    def store(conn):
        status, current = apply_text(conn, candidate, result, raw, now, doc_id, url=final)
        values = advance()
        if final:
            values["url"] = final
            moved = dict(url=final, outlet=page["outlet"], fetched_at=now, status="stored", document_id=current,
                         first_fetched_at=now, recheck_due_at=None)
            conn.execute(pg_insert(crawled_pages).values(canonical_url=canonical_url(final), **moved)
                         .on_conflict_do_update(index_elements=[crawled_pages.c.canonical_url], set_=moved))
        return ("unchanged" if status == "unchanged" else "edited"), values, None, current

    return write(store)
