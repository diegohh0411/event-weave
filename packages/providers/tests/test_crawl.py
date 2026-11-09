"""Crawl store against real Postgres; all fetching is served from saved fixtures."""
import gzip
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from weave_core.db import make_engine
from weave_core.schema import crawled_pages, documents
from weave_providers.crawl.fetch import Fetched, FixtureFetcher, PoliteFetcher
import logging
from weave_providers.crawl.outlets import Outlet
from weave_providers.crawl.store import crawl

FIX = Path(__file__).parent / "fixtures"
BASE = "https://www.example-news.test"
NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
OUTLETS = [Outlet("example-news", "example-news.test", "en", sitemaps=[f"{BASE}/sitemap.xml"])]


def sitemap(*entries):
    rows = "".join(f"<url><loc>{BASE}{path}</loc><lastmod>{date}</lastmod></url>" for path, date in entries)
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{rows}</urlset>")


def sitemap_undated(*paths):
    rows = "".join(f"<url><loc>{BASE}{p}</loc></url>" for p in paths)
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{rows}</urlset>")


ENTRIES = [("/a/reuters", "2026-11-03T10:00:00Z"), ("/a/elpais", "2026-11-04T10:00:00Z"),
           ("/a/reuters-copy", "2026-11-04T11:00:00Z"), ("/a/paywalled", "2026-11-03T08:00:00Z"),
           ("/a/empty", "2026-11-02T08:00:00Z"), ("/private/secret", "2026-11-02T09:00:00Z")]


def routes(entries=ENTRIES):
    html = lambda p: p.read_text(encoding="utf-8")
    page = lambda text: Fetched("x", 200, text, None)
    return {
        f"{BASE}/robots.txt": page("User-agent: *\nDisallow: /private/\n"),
        f"{BASE}/sitemap.xml": page(sitemap(*entries)),
        f"{BASE}/a/reuters": page(html(FIX / "reuters" / "article.html")),
        f"{BASE}/a/reuters-copy": page(html(FIX / "reuters" / "article.html")),
        f"{BASE}/a/elpais": page(html(FIX / "elpais" / "article.html")),
        f"{BASE}/a/paywalled": page(html(FIX / "pages" / "paywalled.html")),
        f"{BASE}/a/empty": page(html(FIX / "pages" / "empty.html")),
    }


def polite(route_map=None):
    inner = FixtureFetcher(route_map or routes())
    return PoliteFetcher(inner, min_interval=0, clock=lambda: 0.0, sleep=lambda s: None), inner


@pytest.fixture
def engine(database_url):
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


def rows(engine, sql):
    with engine.connect() as c:
        return [tuple(r) for r in c.execute(sa.text(sql))]


def test_crawl_twice_adds_no_duplicates(engine):
    fetcher, inner = polite()
    first = crawl(engine, OUTLETS, fetcher, NOW)
    assert (first.discovered, first.fetched, first.stored, first.duplicates) == (6, 5, 2, 1)
    before = len(inner.calls)
    second = crawl(engine, OUTLETS, fetcher, NOW)
    assert second.stored == 0 and second.fetched == 0 and second.duplicates == 0
    assert rows(engine, "select count(*) from documents") == [(first.stored,)]
    assert not [u for u in inner.calls[before:] if "/a/" in u]  # nothing re-fetched


def test_stored_document_shape(engine):
    crawl(engine, OUTLETS, polite()[0], NOW)
    [doc] = [r for r in rows(engine, "select provider, language, outlet, url, order_key, id, external_id "
                                     "from documents where url like '%/a/elpais'")]
    assert doc[:4] == ("crawl", "en", "example-news", f"{BASE}/a/elpais")
    assert doc[4] == [int(datetime.fromisoformat("2026-11-03T18:30:00+01:00").timestamp())]
    assert doc[5] == f"crawl:{doc[6]}" and "elpais" not in doc[6]


def test_same_text_two_urls_stored_once(engine):
    report = crawl(engine, OUTLETS, polite()[0], NOW)
    assert report.duplicates == 1
    reason = rows(engine, "select reason from crawled_pages where url like '%/a/reuters-copy'")[0][0]
    original = rows(engine, "select id from documents where url like '%/a/reuters'")[0][0]
    assert reason == f"duplicate of {original}"
    assert rows(engine, "select count(*) from documents where url like '%reuters-copy'") == [(0,)]


def test_rejected_pages_recorded_not_stored(engine):
    report = crawl(engine, OUTLETS, polite()[0], NOW)
    got = rows(engine, "select reason from crawled_pages where status='rejected' "
                       "and reason not like 'duplicate%' order by canonical_url")
    assert got == [("no body",), ("paywalled",), ("robots disallowed",)]
    assert report.rejected == {"paywalled": 1, "no body": 1, "robots disallowed": 1}
    assert rows(engine, "select count(*) from crawled_pages where status='rejected' and document_id is not null") == [(0,)]


def test_raw_html_kept_gzipped(engine):
    crawl(engine, OUTLETS, polite()[0], NOW)
    with engine.connect() as c:
        raw = c.execute(sa.select(crawled_pages.c.raw_html_gz).where(
            crawled_pages.c.url == f"{BASE}/a/paywalled")).scalar()
    assert gzip.decompress(raw).decode().startswith("<!doctype html>")


def test_robots_disallowed_url_never_fetched(engine):
    fetcher, inner = polite()
    crawl(engine, OUTLETS, fetcher, NOW)
    assert f"{BASE}/private/secret" not in inner.calls


def test_fetch_errors_are_recorded_and_retried(engine):
    broken = routes()
    broken[f"{BASE}/a/elpais"] = Fetched("x", 503, None, None)
    first = crawl(engine, OUTLETS, polite(broken)[0], NOW)
    assert first.stored == 1 and first.rejected["fetch error"] == 1
    assert rows(engine, "select reason from crawled_pages where url like '%/a/elpais'") == [("fetch error: HTTP 503",)]
    second = crawl(engine, OUTLETS, polite()[0], NOW)  # site recovered
    assert second.stored == 1
    assert rows(engine, "select status, reason from crawled_pages where url like '%/a/elpais'") == [("stored", None)]


def test_replay_after_crawl_orders_by_time_then_url(engine, monkeypatch):
    from uuid import uuid4
    from weave_core.adapters import AdapterContext, UnitRef
    from weave_core.stages import UnitKind
    from weave_providers.replay import ReplayProvider, ReplaySettings

    monkeypatch.setenv("WEAVE_DATABASE_URL", engine.url.render_as_string(hide_password=False))
    crawl(engine, OUTLETS, polite()[0], NOW)
    provider = ReplayProvider(ReplaySettings(), AdapterContext(uuid4(), 1, {}))
    emitted = provider.process(UnitRef(UnitKind.run, "r", "h"), {}).emits
    urls = dict(rows(engine, "select id, url from documents"))
    assert [urls[u.id] for u in emitted] == [f"{BASE}/a/reuters", f"{BASE}/a/elpais"]
    assert [u.order_key for u in emitted] == sorted(u.order_key for u in emitted)


def test_cli_crawl_prints_report(tmp_path, monkeypatch, database_url):
    from weave_core.cli import app
    from weave_providers.crawl import cli

    outlets = tmp_path / "outlets.yaml"
    outlets.write_text("outlets:\n  - name: example-news\n    domain: example-news.test\n    language: en\n"
                       f"    sitemaps: ['{BASE}/sitemap.xml']\n")
    monkeypatch.setenv("WEAVE_DATABASE_URL", database_url)

    class FakeBrowser(PoliteFetcher):
        def __init__(self):
            super().__init__(FixtureFetcher(routes()), min_interval=0, clock=lambda: 0.0, sleep=lambda s: None)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    class FakeHttp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(cli, "Crawl4aiFetcher", FakeBrowser)
    monkeypatch.setattr(cli, "HttpFetcher", FakeHttp)
    monkeypatch.setattr(cli, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))
    result = CliRunner().invoke(app, ["crawl", str(outlets), "--days", "7"])
    assert result.exit_code == 0, result.output
    assert "discovered=6 fetched=5 stored=2 duplicates=1" in result.output
    assert "rejected paywalled: 1" in result.output


def replay_urls(engine, monkeypatch):
    from uuid import uuid4
    from weave_core.adapters import AdapterContext, UnitRef
    from weave_core.stages import UnitKind
    from weave_providers.replay import ReplayProvider, ReplaySettings

    monkeypatch.setenv("WEAVE_DATABASE_URL", engine.url.render_as_string(hide_password=False))
    emitted = ReplayProvider(ReplaySettings(), AdapterContext(uuid4(), 1, {})).process(
        UnitRef(UnitKind.run, "r", "h"), {}).emits
    urls = dict(rows(engine, "select id, url from documents"))
    return [urls[u.id] for u in emitted]


def test_undated_page_gets_max_order_key_and_sorts_last(engine, monkeypatch):
    r = routes()
    # undated sitemap (no dates at all) holds the page without a date of its own
    r[f"{BASE}/sitemap.xml"] = Fetched("x", 200, sitemap_undated("/a/nodate", "/a/elpais"), None)
    r[f"{BASE}/a/nodate"] = Fetched("x", 200, (FIX / "pages" / "no_date.html").read_text(encoding="utf-8"), None)
    crawl(engine, OUTLETS, polite(r)[0], NOW)
    keys = dict(rows(engine, "select url, order_key from documents"))
    assert keys[f"{BASE}/a/nodate"] == [2**62]
    assert keys[f"{BASE}/a/elpais"] != [2**62]
    assert replay_urls(engine, monkeypatch) == [f"{BASE}/a/elpais", f"{BASE}/a/nodate"]


def test_equal_timestamps_replay_in_url_order(engine, monkeypatch):
    base = (FIX / "reuters" / "article.html").read_text(encoding="utf-8")
    r = routes()
    r[f"{BASE}/sitemap.xml"] = Fetched("x", 200, sitemap(("/a/beta", "2026-11-03T10:00:00Z"),
                                                       ("/a/alpha", "2026-11-03T10:00:00Z")), None)
    r[f"{BASE}/a/beta"] = Fetched("x", 200, base.replace("replace roughly forty", "replace roughly fifty"), None)
    r[f"{BASE}/a/alpha"] = Fetched("x", 200, base, None)
    crawl(engine, OUTLETS, polite(r)[0], NOW)
    keys = {tuple(k) for _, k in rows(engine, "select url, order_key from documents")}
    assert len(keys) == 1  # same page timestamp
    # ids sort opposite to URLs here (the id of alpha sorts after beta's), so only the url tie-break gives this order
    assert replay_urls(engine, monkeypatch) == [f"{BASE}/a/alpha", f"{BASE}/a/beta"]


@pytest.mark.parametrize("body", [None, ""])
def test_empty_200_response_is_a_fetch_error(engine, body):
    r = routes([("/a/blank", "2026-11-03T10:00:00Z")])
    r[f"{BASE}/a/blank"] = Fetched("x", 200, body, None)
    crawl(engine, OUTLETS, polite(r)[0], NOW)
    assert rows(engine, "select reason from crawled_pages") == [("fetch error: empty response",)]


def test_weave_crawl_explains_missing_extra(monkeypatch):
    import importlib
    import sys

    for name in ("feedparser", "crawl4ai"):
        monkeypatch.setitem(sys.modules, name, None)
    for name in [m for m in sys.modules if m == "weave_core.cli" or m.startswith("weave_providers.crawl")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    cli_module = importlib.import_module("weave_core.cli")  # fresh import, extras blocked
    result = CliRunner().invoke(cli_module.app, ["crawl", "outlets.yaml"])
    assert result.exit_code == 1, result.output
    assert "install weave-providers[crawl]" in result.output


def test_meta_urls_go_to_meta_fetcher_and_articles_to_article_fetcher(engine):
    # a browser would hand back robots.txt wrapped in <pre>; it must never be what the parser sees
    wrapped = "<html><body><pre>User-agent: *\nDisallow: /private/</pre></body></html>"
    r = routes()
    meta_routes = {u: r.pop(u) for u in (f"{BASE}/robots.txt", f"{BASE}/sitemap.xml")}
    meta_routes.update(r)  # the HTTP fetcher resolves each article's redirect chain first
    browser_routes = dict(r)
    browser_routes[f"{BASE}/robots.txt"] = Fetched("x", 200, wrapped, None)  # traps a wrong path
    browser, meta = FixtureFetcher(browser_routes), FixtureFetcher(meta_routes)
    report = crawl(engine, OUTLETS, PoliteFetcher(browser, meta=meta, min_interval=0,
                   clock=lambda: 0.0, sleep=lambda s: None), NOW)
    assert meta.calls[:2] == [f"{BASE}/robots.txt", f"{BASE}/sitemap.xml"]
    assert meta.calls.count(f"{BASE}/robots.txt") == 1  # article probes follow, one per page
    assert all("/a/" in u for u in browser.calls) and f"{BASE}/robots.txt" not in browser.calls
    assert f"{BASE}/private/secret" not in browser.calls
    assert report.stored == 2


def test_crawl_builds_polite_fetcher_with_meta(engine):
    r = routes()
    meta_routes = {u: r.pop(u) for u in (f"{BASE}/robots.txt", f"{BASE}/sitemap.xml")}
    meta = FixtureFetcher({**meta_routes, **r})
    article = FixtureFetcher(r)
    report = crawl(engine, OUTLETS, article, NOW, meta_fetcher=meta)
    assert report.stored == 2 and f"{BASE}/robots.txt" not in article.calls


def test_discovery_errors_counted_and_logged(engine, caplog):
    r = routes()
    r[f"{BASE}/sitemap.xml"] = Fetched("x", 503, None, None)
    outlets = [Outlet("example-news", "example-news.test", "en", sitemaps=[f"{BASE}/sitemap.xml"],
                      feeds=[f"{BASE}/feed.rss"])]
    r[f"{BASE}/feed.rss"] = Fetched("x", 0, None, "timeout")
    with caplog.at_level(logging.WARNING):
        report = crawl(engine, outlets, polite(r)[0], NOW)
    assert report.discovery_errors == {"example-news": 2}
    assert f"{BASE}/sitemap.xml" in caplog.text and "HTTP 503" in caplog.text and "timeout" in caplog.text


def test_malformed_sitemap_counted_as_discovery_error(engine):
    r = routes()
    r[f"{BASE}/sitemap.xml"] = Fetched("x", 200, "<urlset><url>", None)
    report = crawl(engine, OUTLETS, polite(r)[0], NOW)
    assert report.discovery_errors == {"example-news": 1} and report.discovered == 0


def test_repeat_crawl_emits_document_membership(engine):
    events = []
    def observe(kind, event):
        if kind == 'article':
            with engine.connect() as conn:
                assert conn.scalar(sa.select(sa.func.count()).select_from(crawled_pages).where(
                    crawled_pages.c.canonical_url == event['canonical_url'])) == 1
        events.append((kind, event))
    crawl(engine, OUTLETS, polite()[0], NOW, observer=observe)
    articles = [e for k, e in events if k == 'article']
    duplicate = next(e for e in articles if e['status'] == 'duplicate')
    assert duplicate['document_id'] is not None
    events.clear()
    crawl(engine, OUTLETS, polite()[0], NOW, observer=lambda kind, event: events.append((kind, event)))
    second_articles = [e for k, e in events if k == 'article']
    assert any(e['status'] == 'existing' and e['document_id'] for e in second_articles)
    repeated_duplicate = next(e for e in second_articles if e['status'] == 'duplicate')
    assert repeated_duplicate['document_id'] == duplicate['document_id']
    assert repeated_duplicate['fetched'] is False


def test_missing_feed_is_reported(engine):
    r = {f'{BASE}/robots.txt': Fetched('x', 404, None, None),
         f'{BASE}/feed': Fetched('x', 200, '<html>not a feed</html>', None)}
    events = []
    outlet = Outlet('example-news', 'example-news.test', 'en', feeds=[f'{BASE}/feed'])
    report = crawl(engine, [outlet], polite(r)[0], NOW, observer=lambda k, e: events.append((k, e)))
    assert report.discovery_errors == {'example-news': 1}
    assert [e['status'] for k, e in events if k == 'source'] == ['error']


def test_all_paywalled_outcomes_and_window_exclusion(engine):
    paths = [f'/a/wall{i}' for i in range(4)]
    r = routes([(p, '2026-11-04T10:00:00Z') for p in paths] + [('/a/old', '2020-01-01T00:00:00Z')])
    for p in paths:
        r[BASE + p] = Fetched('x', 200, (FIX / 'pages' / 'paywalled.html').read_text(), None)
    events = []
    report = crawl(engine, OUTLETS, polite(r)[0], NOW, observer=lambda k, e: events.append((k, e)))
    articles = [e for k, e in events if k == 'article']
    assert (report.discovered, report.fetched, report.stored) == (4, 4, 0)
    assert [(a['status'], a['reason'], a['fetched']) for a in articles] == [('rejected', 'paywalled', True)] * 4
    assert next(e for k, e in events if k == 'source')['status'] == 'completed'


def test_empty_time_window_keeps_successful_source_outcome(engine):
    r = routes([('/a/old', '2020-01-01T00:00:00Z')])
    events = []
    report = crawl(engine, OUTLETS, polite(r)[0], NOW, observer=lambda k, e: events.append((k, e)))
    assert report.discovered == report.stored == 0
    assert [(e['status'], e['error']) for k, e in events if k == 'source'] == [('completed', None)]
    assert not [e for k, e in events if k == 'article']


def test_automatic_discovery_reports_probe_errors_and_fallbacks(engine):
    from weave_providers.crawl.requests import CrawlSite
    base = 'https://example-news.test'
    r = {base + '/robots.txt': Fetched('x', 404, None, None),
         base + '/': Fetched('x', 503, None, None)}
    for path in ('/sitemap.xml', '/sitemap_index.xml', '/feed', '/rss'):
        r[base + path] = Fetched('x', 404, None, None)
    events = []
    crawl(engine, [CrawlSite(domain='example-news.test', language='en')], polite(r)[0], NOW,
          observer=lambda k, e: events.append((k, e)))
    sources = [e for k, e in events if k == 'source']
    assert len(sources) == 6
    assert all(e['status'] == 'error' for e in sources)
    assert any(e['url'] == base + '/' and '503' in e['error'] for e in sources)


def test_cancel_during_discovery_stops_further_network_and_events(engine):
    events = []
    r = routes()
    inner = FixtureFetcher(r)
    stopped = lambda: f'{BASE}/sitemap.xml' in inner.calls
    fetcher = PoliteFetcher(inner, min_interval=0)
    crawl(engine, OUTLETS, fetcher, NOW, observer=lambda k, e: events.append((k, e)), cancelled=stopped)
    assert inner.calls == [f'{BASE}/robots.txt', f'{BASE}/sitemap.xml']
    assert events == []
    assert rows(engine, 'select count(*) from crawled_pages') == [(0,)]


def test_cancel_during_article_fetch_prevents_persistence_and_events(engine):
    r = routes([('/a/reuters', '2026-11-03T10:00:00Z')])
    inner = FixtureFetcher(r)
    events = []
    crawl(engine, OUTLETS, PoliteFetcher(inner, min_interval=0), NOW,
          observer=lambda k, e: events.append((k, e)),
          cancelled=lambda: f'{BASE}/a/reuters' in inner.calls)
    assert not [e for k, e in events if k == 'article']
    assert rows(engine, 'select count(*) from crawled_pages') == [(0,)]
    assert rows(engine, 'select count(*) from documents') == [(0,)]
