from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from weave_core.db import make_engine
from weave_core.schema import crawled_pages, documents, page_rechecks
from weave_providers.crawl.fetch import Fetched, FixtureFetcher, PoliteFetcher
from weave_providers.crawl.outlets import Outlet
from weave_providers.crawl.recheck import MAX_ATTEMPTS, RETRY_HOURS, next_due, recheck_days, recheck_page
from weave_providers.crawl.store import crawl

FIX = Path(__file__).parent / "fixtures"
BASE = "https://www.example-news.test"
URL = f"{BASE}/a/water"
NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
DAYS = [1, 7, 30]
OUTLETS = [Outlet("example-news", "example-news.test", "en", sitemaps=[f"{BASE}/sitemap.xml"])]
ARTICLE = (FIX / "reuters" / "article.html").read_text(encoding="utf-8")
OTHER = (FIX / "elpais" / "article.html").read_text(encoding="utf-8")
SECTION = (FIX / "pages" / "empty.html").read_text(encoding="utf-8")


def ok(html):
    return Fetched("x", 200, html, None)


def moved(to):
    return Fetched("x", 301, None, None, None, to)


def sitemap(*paths):
    rows = "".join(f"<url><loc>{BASE}{p}</loc><lastmod>2026-11-04T10:00:00Z</lastmod></url>" for p in paths)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'


def polite(routes):
    inner = FixtureFetcher({f"{BASE}/robots.txt": ok("User-agent: *\nAllow: /\n"), **routes})
    return PoliteFetcher(inner, min_interval=0, clock=lambda: 0.0, sleep=lambda s: None), inner


@pytest.fixture
def engine(database_url):
    engine = make_engine(database_url)
    crawl(engine, OUTLETS, polite({f"{BASE}/sitemap.xml": ok(sitemap("/a/water")), URL: ok(ARTICLE)})[0], NOW)
    yield engine
    engine.dispose()


def page(engine, url=URL):
    with engine.connect() as conn:
        return conn.execute(sa.select(crawled_pages).where(crawled_pages.c.canonical_url == url)).mappings().one()


def log(engine):
    with engine.connect() as conn:
        return conn.execute(sa.select(page_rechecks).order_by(page_rechecks.c.id)).mappings().all()


def run(engine, response, at=NOW + timedelta(days=1), extra=None, guard=None):
    fetcher, inner = polite({URL: response, **(extra or {})})
    return recheck_page(engine, page(engine), fetcher, at, DAYS, None, guard, lambda: False), inner


def test_store_schedules_first_recheck(engine):
    row = page(engine)
    assert row["first_fetched_at"] == NOW
    assert (row["recheck_stage"], row["recheck_due_at"]) == (0, NOW + timedelta(days=1))


def test_unchanged_advances_stage(engine):
    outcome, _ = run(engine, ok(ARTICLE))
    row = page(engine)
    assert outcome["status"] == "unchanged"
    assert (row["recheck_stage"], row["recheck_due_at"]) == (1, NOW + timedelta(days=7))
    [entry] = log(engine)
    assert (entry["outcome"], entry["stage"], entry["http_status"]) == ("unchanged", 0, 200)


def test_edit_creates_version(engine):
    v1 = page(engine)["document_id"]
    outcome, _ = run(engine, ok(ARTICLE.replace("regional water board", "regional water authority")))
    assert outcome["status"] == "updated"
    with engine.connect() as conn:
        versions = conn.execute(sa.select(documents.c.id, documents.c.version, documents.c.superseded_at)
                                .order_by(documents.c.version)).all()
    assert [v.version for v in versions] == [1, 2] and versions[0].id == v1
    assert versions[0].superseded_at is not None and versions[1].superseded_at is None
    assert log(engine)[0]["outcome"] == "edited" and log(engine)[0]["document_id"] == versions[1].id
    assert page(engine)["recheck_stage"] == 1


def test_byline_change_no_version(engine):
    outcome, _ = run(engine, ok(ARTICLE.replace('content="Priya Nair"', 'content="P. Nair"')
                                  .replace('"name": "Priya Nair"', '"name": "P. Nair"')))
    assert outcome["status"] == "unchanged"
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(documents)) == 1
        assert conn.scalar(sa.select(documents.c.byline)) == "P. Nair"


@pytest.mark.parametrize("status", [404, 410])
def test_http_gone_marks_gone(engine, status):
    outcome, _ = run(engine, Fetched("x", status, None, None))
    row = page(engine)
    assert outcome["status"] == "removed"
    assert (row["status"], row["gone_reason"], row["recheck_due_at"]) == ("gone", f"HTTP {status}", None)
    assert row["gone_at"] == NOW + timedelta(days=1)
    assert log(engine)[0]["outcome"] == "gone"


def test_offsite_redirect_marks_removed(engine):
    outcome, inner = run(engine, moved("https://elsewhere.test/x"))
    row = page(engine)
    assert outcome["status"] == "removed" and row["status"] == "gone"
    assert row["gone_reason"] == "redirected to https://elsewhere.test/x"
    assert "https://elsewhere.test/x" not in inner.calls
    assert log(engine)[0]["final_url"] == "https://elsewhere.test/x"


def test_homepage_redirect_marks_removed(engine):
    outcome, _ = run(engine, moved(f"{BASE}/"), extra={f"{BASE}/": ok(OTHER)})
    assert outcome["status"] == "removed"
    assert page(engine)["gone_reason"] == f"redirected to {BASE}/"


def test_section_redirect_marks_removed(engine):
    outcome, _ = run(engine, moved(f"{BASE}/world"), extra={f"{BASE}/world": ok(SECTION)})
    assert outcome["status"] == "removed" and page(engine)["status"] == "gone"


def test_slug_change_keeps_document(engine):
    new = f"{BASE}/a/water-plan"
    before = page(engine)
    outcome, _ = run(engine, moved(new), extra={new: ok(ARTICLE)})
    assert outcome["status"] == "unchanged" and outcome["document_id"] == before["document_id"]
    with engine.connect() as conn:
        assert conn.scalar(sa.select(documents.c.url)) == new
        assert conn.scalar(sa.select(sa.func.count()).select_from(documents)) == 1
    moved_row = page(engine, new)
    assert (moved_row["document_id"], moved_row["recheck_due_at"]) == (before["document_id"], None)
    old = page(engine)
    assert (old["status"], old["url"], old["recheck_stage"]) == ("stored", new, 1)


def test_slug_change_onto_known_url_marks_removed(engine):
    other = f"{BASE}/a/other"
    crawl(engine, OUTLETS, polite({f"{BASE}/sitemap.xml": ok(sitemap("/a/other")), other: ok(OTHER)})[0], NOW)
    known = page(engine, other)["document_id"]
    outcome, _ = run(engine, moved(other), extra={other: ok(OTHER)})
    assert outcome["status"] == "removed" and page(engine)["status"] == "gone"
    assert log(engine)[0]["reason"] == f"redirect to known document {known}"


def test_transient_error_retries_then_advances(engine):
    at = NOW + timedelta(days=1)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        outcome, _ = run(engine, Fetched("x", 503, None, None), at=at)
        assert outcome["status"] == "error"
        row = page(engine)
        if attempt < MAX_ATTEMPTS:
            assert (row["recheck_stage"], row["recheck_attempts"]) == (0, attempt)
            assert row["recheck_due_at"] == at + timedelta(hours=RETRY_HOURS)
        at += timedelta(hours=RETRY_HOURS)
    assert (row["recheck_stage"], row["recheck_attempts"], row["status"]) == (1, 0, "stored")
    assert row["recheck_due_at"] == NOW + timedelta(days=7)
    assert [e["outcome"] for e in log(engine)] == ["error"] * MAX_ATTEMPTS


def test_last_stage_clears_due(engine):
    with engine.begin() as conn:
        conn.execute(crawled_pages.update().values(recheck_stage=2))
    run(engine, ok(ARTICLE))
    assert page(engine)["recheck_due_at"] is None


def test_lease_guard_honoured(engine):
    class Lost(Exception):
        pass

    def guard(conn):
        raise Lost()

    before = page(engine)
    with pytest.raises(Lost):
        run(engine, ok(ARTICLE), guard=guard)
    assert log(engine) == []
    assert page(engine)["recheck_stage"] == before["recheck_stage"]


def test_recheck_days_parsing(monkeypatch):
    monkeypatch.delenv("WEAVE_RECHECK_DAYS", raising=False)
    assert recheck_days() == [1, 7, 30]
    monkeypatch.setenv("WEAVE_RECHECK_DAYS", "2, 5")
    assert recheck_days() == [2, 5]
    for bad in ("7,1", "0", "x", "", "1,1"):
        monkeypatch.setenv("WEAVE_RECHECK_DAYS", bad)
        with pytest.raises(ValueError):
            recheck_days()


def test_next_due():
    assert next_due(NOW, 0, DAYS) == NOW + timedelta(days=1)
    assert next_due(NOW, 3, DAYS) is None


def test_slug_change_onto_rejected_duplicate_row_follows(engine):
    new = f"{BASE}/a/water-plan"
    with engine.begin() as conn:
        conn.execute(crawled_pages.insert().values(canonical_url=new, url=new, outlet="example-news", fetched_at=NOW,
                                                   status="rejected", reason="duplicate of x", document_id=None))
    before = page(engine)
    outcome, _ = run(engine, moved(new), extra={new: ok(ARTICLE)})
    assert outcome["status"] == "unchanged" and outcome["document_id"] == before["document_id"]
    assert page(engine)["status"] == "stored"
    assert (page(engine, new)["status"], page(engine, new)["document_id"]) == ("stored", before["document_id"])


def test_browser_offsite_navigation_is_error_not_removed(engine):
    class Browser:
        def fetch(self, url):
            if url.endswith("/robots.txt"):
                return ok("User-agent: *\nAllow: /\n")
            return Fetched(url, 200, ARTICLE, None, "https://consent.example.org/wall")
    fetcher = PoliteFetcher(Browser(), min_interval=0, clock=lambda: 0.0, sleep=lambda s: None)
    outcome = recheck_page(engine, page(engine), fetcher, NOW + timedelta(days=1), DAYS, None, None, lambda: False)
    assert outcome["status"] == "error"
    assert (page(engine)["status"], page(engine)["recheck_attempts"]) == ("stored", 1)
