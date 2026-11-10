"""URL discovery from sitemaps and feeds only; links in pages are never followed."""
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from weave_providers.crawl.fetch import Fetcher
from weave_providers.crawl.outlets import Outlet

SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
NEWS = "{http://www.google.com/schemas/sitemap-news/0.9}"
_TRACKING = {"fbclid", "gclid"}


@dataclass(frozen=True)
class Candidate:
    url: str
    outlet: str
    published_at: datetime | None
    title: str | None
    updated_at: datetime | None = None


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.startswith("utm_") and k not in _TRACKING]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), urlencode(query), ""))


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _parse_date(text: str | None) -> datetime | None:
    if not text or not text.strip():
        return None
    try:
        return _utc(datetime.fromisoformat(text.strip()))
    except ValueError:
        return None


def _text(el: ET.Element, path: str) -> str | None:
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


ErrorSink = Callable[[str, str], None]  # (url, reason)


def _sitemap_entries(xml: str) -> tuple[list[tuple[str, datetime | None]], list[tuple[str, datetime | None, str | None, datetime | None]]]:
    """Return (child sitemap URLs, [(url, date, title)]). Raises on malformed or hostile XML."""
    from defusedxml import ElementTree as SafeET  # extra `crawl`; sitemaps are untrusted

    root = SafeET.fromstring(xml)
    children = [(loc, _parse_date(_text(s, f"{SM}lastmod"))) for s in root.findall(f"{SM}sitemap") if (loc := _text(s, f"{SM}loc"))]
    entries = []
    for u in root.findall(f"{SM}url"):
        loc = _text(u, f"{SM}loc")
        if not loc:
            continue
        date = (_parse_date(_text(u, f"{NEWS}news/{NEWS}publication_date"))
                or _parse_date(_text(u, f"{SM}lastmod")))
        entries.append((loc, date, _text(u, f"{NEWS}news/{NEWS}title"), _parse_date(_text(u, f"{SM}lastmod"))))
    return children, entries


def _fetch_text(fetcher: Fetcher, url: str, on_error: ErrorSink) -> str | None:
    got = fetcher.fetch(url)
    if got.status == 200 and got.html:
        return got.html
    on_error(url, got.error or ("empty response" if got.status == 200 else f"HTTP {got.status}"))
    return None


def _parse_sitemap(xml: str, url: str, on_error: ErrorSink):
    from defusedxml import DefusedXmlException

    try:
        return _sitemap_entries(xml)
    except (ET.ParseError, DefusedXmlException) as exc:
        on_error(url, f"XML {type(exc).__name__}: {exc}")
        return [], []


def _from_sitemap(fetcher: Fetcher, url: str, on_error: ErrorSink, on_source=None, seen=None,
                  cancelled=None):
    seen = seen if seen is not None else set()
    stopped = cancelled or (lambda: False)
    entries = []
    queue = [(url, 0)]
    while queue and len(seen) < 100 and not stopped():
        current, depth = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        failed = []
        def error(u, reason):
            failed.append(reason)
            on_error(u, reason)
        xml = _fetch_text(fetcher, current, error)
        if stopped():
            break
        children, found = _parse_sitemap(xml, current, error) if xml is not None else ([], [])
        if on_source:
            on_source(current, 'sitemap', failed[0] if failed else None)
        entries.extend(found)
        if depth < 2:
            queue.extend((child, depth + 1) for child, _ in children)
    return entries


def _from_feed(fetcher: Fetcher, url: str, on_error: ErrorSink):
    body = _fetch_text(fetcher, url, on_error)
    if body is None:
        return []
    import feedparser  # extra `crawl`; imported lazily so `weave crawl` can explain its absence

    parsed_feed = feedparser.parse(body)
    if not parsed_feed.version:
        on_error(url, 'invalid feed')
        return []
    out = []
    for e in parsed_feed.entries:
        parsed = e.get("published_parsed") or e.get("updated_parsed")  # feedparser yields UTC
        date = datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None
        if e.get("link"):
            updated = e.get("updated_parsed")
            out.append((e["link"], date, e.get("title"), datetime(*updated[:6], tzinfo=timezone.utc) if updated else None))
    return out


def _on_domain(url: str, domain: str) -> bool:
    try:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or parts.username is not None or parts.password is not None:
            return False
        host = (parts.hostname or "").lower()
        _ = parts.port  # reject malformed ports before canonicalization
    except ValueError:
        return False
    domain = domain.lower().rstrip('.')
    if domain.startswith('www.'):
        domain = domain[4:]
    return host.rstrip('.') == domain or host.rstrip('.').endswith("." + domain)


def discover(outlet: Outlet, fetcher: Fetcher, window: tuple[datetime, datetime],
             on_error: ErrorSink | None = None, on_source=None, source_budget=None,
             cancelled=None) -> list[Candidate]:
    """`on_error(url, reason)` is called for each sitemap/feed that fails to fetch or parse."""
    start, end = _utc(window[0]), _utc(window[1])
    sink = on_error or (lambda url, reason: None)
    seen = source_budget if source_budget is not None else set()
    stopped = cancelled or (lambda: False)
    sources = []
    for u in outlet.sitemaps:
        if stopped():
            break
        sources.append(_from_sitemap(fetcher, u, sink, on_source, seen, stopped))
    for u in outlet.feeds:
        if stopped():
            break
        if u in seen or len(seen) >= 100:
            continue
        seen.add(u)
        failures = []
        def feed_error(url, reason):
            failures.append(reason)
            sink(url, reason)
        sources.append(_from_feed(fetcher, u, feed_error))
        if stopped():
            break
        if on_source:
            on_source(u, 'feed', failures[0] if failures else None)
    found: dict[str, Candidate] = {}
    for entries in sources:
        has_dates = any(d for _, d, _, _ in entries)
        for url, date, title, updated in entries:
            if not _on_domain(url, outlet.domain):
                continue
            if date is None:
                if has_dates:  # undated rows only count from sources that give no dates at all
                    continue
            elif not start <= date <= end:
                continue
            key = canonical_url(url)
            prior = found.get(key)
            if prior is None or (prior.published_at is None and date is not None):
                found[key] = Candidate(key, outlet.name, date, title, updated)
    result = sorted(found.values(), key=lambda c: (c.published_at is None,
                    c.published_at or start, c.url))
    if outlet.per_day is not None:
        kept: dict = {}
        for c in sorted((c for c in result if c.published_at),
                        key=lambda c: (c.published_at, c.url), reverse=True):
            day = kept.setdefault(c.published_at.date(), [])
            if len(day) < outlet.per_day:
                day.append(c)
        undated = [c for c in result if c.published_at is None][:outlet.per_day]
        result = sorted([c for d in kept.values() for c in d] + undated,
                        key=lambda c: (c.published_at is None, c.published_at or start, c.url))
    return result
