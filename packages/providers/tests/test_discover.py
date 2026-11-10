from datetime import datetime, timezone
from pathlib import Path

import pytest

from weave_providers.crawl.discover import canonical_url, discover
from weave_providers.crawl.fetch import Fetched, FixtureFetcher
from weave_providers.crawl.outlets import Outlet, load_outlets

FIX = Path(__file__).parent / "fixtures" / "example-news"
BASE = "https://www.example-news.test"
WINDOW = (datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 5, tzinfo=timezone.utc))


def outlet(**kw):
    kw.setdefault("sitemaps", [f"{BASE}/sitemap.xml"])
    return Outlet(name="example-news", domain="example-news.test", language="en", **kw)


def test_news_sitemap_dates_and_window():
    f = FixtureFetcher({f"{BASE}/sitemap.xml": FIX / "sitemap.xml"})
    got = {c.url: c for c in discover(outlet(), f, WINDOW)}
    quake = got[f"{BASE}/world/quake-hits-coast"]
    assert quake.published_at == datetime(2026, 11, 3, 8, 30, tzinfo=timezone.utc)  # not lastmod
    assert quake.title == "Quake hits coast"
    assert got[f"{BASE}/politics/vote-delayed"].published_at == datetime(2026, 11, 4, tzinfo=timezone.utc)
    assert f"{BASE}/world/old-story" not in got  # outside window


def test_sitemap_index_followed_one_level():
    f = FixtureFetcher({f"{BASE}/index.xml": FIX / "sitemap_index.xml",
                        f"{BASE}/sitemap-2026-11.xml": FIX / "sitemap_child.xml"})
    got = discover(outlet(sitemaps=[f"{BASE}/index.xml"]), f, WINDOW)
    assert {c.url for c in got} == {f"{BASE}/a/one", f"{BASE}/a/two", f"{BASE}/a/three"}
    assert f.calls == [f"{BASE}/index.xml", f"{BASE}/sitemap-2026-11.xml"]


def test_rss_and_atom():
    f = FixtureFetcher({f"{BASE}/feed.rss": FIX / "feed.rss", f"{BASE}/feed.atom": FIX / "feed.atom"})
    got = {c.url: c for c in discover(outlet(sitemaps=[], feeds=[f"{BASE}/feed.rss", f"{BASE}/feed.atom"]), f, WINDOW)}
    assert got[f"{BASE}/business/markets-rally"].published_at == datetime(2026, 11, 3, 14, tzinfo=timezone.utc)
    assert got[f"{BASE}/business/markets-rally"].title == "Markets rally"
    assert got[f"{BASE}/local/council-votes"].published_at == datetime(2026, 11, 4, 7, tzinfo=timezone.utc)
    assert len(got) == 2  # off-domain feed item dropped


def test_naive_dates_are_utc():
    f = FixtureFetcher({f"{BASE}/sitemap.xml": FIX / "sitemap_naive.xml"})
    [c] = discover(outlet(), f, WINDOW)
    assert c.published_at == datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc)
    assert c.published_at.utcoffset().total_seconds() == 0


def test_off_domain_urls_dropped():
    f = FixtureFetcher({f"{BASE}/sitemap.xml": FIX / "sitemap.xml"})
    assert all("other-site" not in c.url for c in discover(outlet(), f, WINDOW))


def test_per_day_limit_keeps_newest_per_day():
    f = FixtureFetcher({f"{BASE}/index.xml": FIX / "sitemap_index.xml",
                        f"{BASE}/sitemap-2026-11.xml": FIX / "sitemap_child.xml"})
    got = discover(outlet(sitemaps=[f"{BASE}/index.xml"], per_day=2), f, WINDOW)
    assert {c.url for c in got} == {f"{BASE}/a/two", f"{BASE}/a/three"}  # 18:00 and 12:00 beat 10:00


def test_undated_kept_only_when_source_has_no_dates():
    f = FixtureFetcher({f"{BASE}/u.xml": FIX / "sitemap_undated.xml", f"{BASE}/sitemap.xml": FIX / "sitemap.xml"})
    assert [c.url for c in discover(outlet(sitemaps=[f"{BASE}/u.xml"]), f, WINDOW)] == [f"{BASE}/u/undated-one"]
    undated_in_dated = Fetched("x", 200, (FIX / "sitemap.xml").read_text().replace(
        "</urlset>", "<url><loc>https://www.example-news.test/no-date</loc></url></urlset>"), None)
    f2 = FixtureFetcher({f"{BASE}/sitemap.xml": undated_in_dated})
    assert f"{BASE}/no-date" not in {c.url for c in discover(outlet(), f2, WINDOW)}


def test_duplicates_collapse_and_failed_fetch_is_skipped():
    f = FixtureFetcher({f"{BASE}/sitemap.xml": FIX / "sitemap.xml", f"{BASE}/feed.rss": Fetched("x", 404, None, "not found")})
    got = discover(outlet(feeds=[f"{BASE}/feed.rss"]), f, WINDOW)
    assert len({c.url for c in got}) == len(got)


def test_canonical_url_strips_tracking():
    assert canonical_url("https://EX.com/a/?utm_source=x&id=2#top") == "https://ex.com/a?id=2"
    assert canonical_url("https://ex.com/a?fbclid=1&gclid=2") == "https://ex.com/a"


def test_outlet_requires_a_source_and_loads_from_yaml(tmp_path):
    with pytest.raises(ValueError):
        Outlet(name="x", domain="x.test", language="en")
    p = tmp_path / "o.yaml"
    p.write_text("outlets:\n  - {name: a, domain: a.test, language: en, feeds: [https://a.test/f], per_day: 3}\n")
    [o] = load_outlets(p)
    assert o.feeds == ["https://a.test/f"] and o.per_day == 3
    assert len(load_outlets(Path(__file__).parents[3] / "outlets.example.yaml")) == 1


def test_fixture_fetcher_unknown_url_asserts():
    with pytest.raises(AssertionError):
        FixtureFetcher({}).fetch("https://nope.test/")


def test_sitemap_with_entity_expansion_is_rejected_not_expanded():
    bomb = ('<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>&b;</loc></url></urlset>')
    errors = []
    f = FixtureFetcher({f"{BASE}/sitemap.xml": Fetched("x", 200, bomb, None)})
    assert discover(outlet(), f, WINDOW, lambda url, reason: errors.append((url, reason))) == []
    assert len(errors) == 1 and errors[0][0] == f"{BASE}/sitemap.xml"


def test_sitemap_lastmod_is_exposed_as_updated_at():
    xml = '''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/a</loc><lastmod>2025-01-02T00:00:00Z</lastmod></url></urlset>'''
    from weave_providers.crawl.discover import _sitemap_entries
    assert _sitemap_entries(xml)[1][0][3].year == 2025
