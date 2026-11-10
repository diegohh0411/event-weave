from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from weave_core.db import make_engine
from weave_providers.crawl.jobs import (claim_crawl, crawl_counters, crawl_counters_many, create_crawl,
                                        finish_crawl, heartbeat, record_article, record_source)
from weave_providers.crawl.requests import CrawlRequest


def test_claim_reclaim_and_owner_writes(database_url):
    engine = make_engine(database_url)
    request = CrawlRequest(sites=[{"domain": "Example.COM", "language": "en"}])
    crawl_id = create_crawl(engine, request)
    now = datetime.now(timezone.utc)
    first = claim_crawl(engine, now)
    assert first.id == crawl_id and first.request.sites[0].domain == "example.com"
    assert claim_crawl(engine, now) is None
    with engine.begin() as conn:
        conn.execute(sa.text("insert into documents (id, article_id, provider, external_id, language, title, byline, outlet, url, text, token_offsets, sentence_offsets, content_hash) values ('d', 'd', 'p', 'e', 'en', 'title', '', 'outlet', 'https://example.com/a', 'body', '[]', '[]', 'hash')"))
    outcome = dict(canonical_url="https://example.com/a", url="https://example.com/a", outlet="example.com", status="stored", document_id="d", fetched=True)
    assert record_article(engine, crawl_id, first.token, outcome)
    assert record_article(engine, crawl_id, first.token, outcome)
    second = claim_crawl(engine, now + timedelta(seconds=121))
    assert second.id == crawl_id and second.token != first.token
    assert not heartbeat(engine, crawl_id, first.token, now)
    assert not record_article(engine, crawl_id, first.token, {**outcome, "status": "error", "document_id": None})
    assert not record_source(engine, crawl_id, first.token, dict(url="https://example.com/feed", site="example.com", kind="feed", status="error"))
    assert not finish_crawl(engine, crawl_id, first.token)
    assert heartbeat(engine, crawl_id, second.token, now + timedelta(seconds=121))
    assert record_source(engine, crawl_id, second.token, dict(url="https://example.com/feed", site="example.com", kind="feed", status="error", error="unavailable"))
    with engine.connect() as conn:
        rows = conn.execute(sa.text("select status, document_id from crawl_articles where crawl_id=:id"), {"id": crawl_id}).all()
        assert rows == [("stored", "d")]
        assert conn.scalar(sa.text("select text from documents where id='d'")) == "body"
    assert finish_crawl(engine, crawl_id, second.token)
    assert not heartbeat(engine, crawl_id, second.token, now)


@pytest.mark.parametrize("site", [
    {"domain": "127.0.0.1", "language": "en"},
    {"domain": "user@example.com", "language": "en"},
    {"domain": "example.com:8080", "language": "en"},
    {"domain": "bad_domain", "language": "en"},
    {"domain": "example.com", "language": "xx"},
    {"domain": "example.com", "language": "en", "feeds": ["http://127.0.0.1/feed"]},
    {"domain": "example.com", "language": "en", "feeds": ["https://u:p@example.com/feed"]},
    {"domain": "example.com", "language": "en", "per_day": 201},
])
def test_rejects_invalid_sites(site):
    with pytest.raises(ValidationError):
        CrawlRequest(sites=[site])


def test_request_bounds_and_duplicate_domains():
    with pytest.raises(ValidationError):
        CrawlRequest(sites=[{"domain": "EXAMPLE.com", "language": "en"}, {"domain": "example.com", "language": "en"}])
    for days in (0, 31):
        with pytest.raises(ValidationError):
            CrawlRequest(sites=[{"domain": "example.com", "language": "en"}], days=days)


def test_counters_derive_from_unique_persisted_outcomes(database_url):
    from weave_providers.crawl.jobs import crawl_counters
    engine = make_engine(database_url)
    crawl_id = create_crawl(engine, CrawlRequest(sites=[{"domain": "example.com", "language": "en"}]))
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    outcome = dict(canonical_url="https://example.com/a", url="https://example.com/a", outlet="example.com", status="rejected", reason="paywall", fetched=True)
    record_article(engine, crawl_id, claim.token, outcome)
    record_article(engine, crawl_id, claim.token, outcome)
    with engine.connect() as conn:
        assert crawl_counters(conn, crawl_id) == {"discovered": 1, "fetched": 1, "stored": 0, "existing": 0, "duplicates": 0, "rejected": 1, "errors": 0, "reasons": {"paywall": 1}}
    assert finish_crawl(engine, crawl_id, claim.token, error="fatal")
    with engine.connect() as conn:
        assert conn.execute(sa.text("select status, error from crawl_runs where id=:id"), {"id": crawl_id}).one() == ("failed", "fatal")


def test_retry_updates_failed_article_without_losing_stored_outcome(database_url):
    engine = make_engine(database_url)
    crawl_id = create_crawl(engine, CrawlRequest(sites=[{"domain": "example.com", "language": "en"}]))
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    outcome = dict(canonical_url="https://example.com/a", url="https://example.com/a", outlet="example.com", status="error", reason="temporary", fetched=False)
    assert record_article(engine, crawl_id, claim.token, outcome)
    assert record_article(engine, crawl_id, claim.token, {**outcome, "status": "rejected", "reason": "paywall", "fetched": True})
    with engine.connect() as conn:
        assert conn.execute(sa.text("select status, reason, fetched from crawl_articles where crawl_id=:id"), {"id": crawl_id}).one() == ("rejected", "paywall", True)


def test_successful_retries_clear_previous_failure_details(database_url):
    from weave_providers.crawl.jobs import crawl_counters
    engine = make_engine(database_url)
    crawl_id = create_crawl(engine, CrawlRequest(sites=[{"domain": "example.com", "language": "en"}]))
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    with engine.begin() as conn:
        conn.execute(sa.text("insert into documents (id, article_id, provider, external_id, language, title, byline, outlet, url, text, token_offsets, sentence_offsets, content_hash) values ('d', 'd', 'p', 'e', 'en', 'title', '', 'outlet', 'https://example.com/a', 'body', '[]', '[]', 'hash')"))
    article = dict(canonical_url="https://example.com/a", url="https://example.com/a", outlet="example.com", status="error", reason="temporary")
    record_article(engine, crawl_id, claim.token, article)
    record_article(engine, crawl_id, claim.token, {k: v for k, v in {**article, "status": "stored", "document_id": "d", "fetched": True}.items() if k != "reason"})
    source = dict(url="https://example.com/feed", site="example.com", kind="feed", status="error", error="temporary")
    record_source(engine, crawl_id, claim.token, source)
    record_source(engine, crawl_id, claim.token, {k: v for k, v in {**source, "status": "ok"}.items() if k != "error"})
    with engine.connect() as conn:
        assert crawl_counters(conn, crawl_id)["reasons"] == {}
        assert conn.scalar(sa.text("select error from crawl_sources where crawl_id=:id"), {"id": crawl_id}) is None


def test_heartbeat_renews_the_configured_lease(database_url):
    engine = make_engine(database_url)
    crawl_id = create_crawl(engine, CrawlRequest(sites=[{"domain": "example.com", "language": "en"}]))
    now = datetime.now(timezone.utc)
    claim = claim_crawl(engine, now, lease_seconds=30)
    assert heartbeat(engine, crawl_id, claim.token, now, lease_seconds=300)
    with engine.connect() as conn:
        assert conn.scalar(sa.text("select lease_until from crawl_runs where id=:id"), {"id": crawl_id}) == now + timedelta(seconds=300)


def test_crawl_counters_many_batches_counts_by_crawl(database_url):
    engine = make_engine(database_url)
    first = create_crawl(engine, CrawlRequest(sites=[{"domain": "example.com", "language": "en"}]))
    second = create_crawl(engine, CrawlRequest(sites=[{"domain": "example.org", "language": "en"}]))
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    record_article(engine, first, claim.token, dict(canonical_url="https://example.com/a", url="https://example.com/a", outlet="example.com", status="rejected", reason="paywall", fetched=True))
    assert finish_crawl(engine, first, claim.token)
    claim = claim_crawl(engine, datetime.now(timezone.utc))
    record_article(engine, second, claim.token, dict(canonical_url="https://example.org/a", url="https://example.org/a", outlet="example.org", status="error", reason="HTTP 500", fetched=False))
    with engine.connect() as conn:
        counters = crawl_counters_many(conn, [first, second])
        assert counters[first] == crawl_counters(conn, first)
        assert counters[second] == crawl_counters(conn, second)
        assert counters[first]["rejected"] == 1 and counters[second]["errors"] == 1
        assert crawl_counters_many(conn, []) == {}
