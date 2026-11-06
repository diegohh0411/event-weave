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


def _sitemap_entries(xml: str) -> tuple[list[str], list[tuple[str, datetime | None, str | None]]]:
    """Return (child sitemap URLs, [(url, date, title)]). Raises on malformed or hostile XML."""
    from defusedxml import ElementTree as SafeET  # extra `crawl`; sitemaps are untrusted

    root = SafeET.fromstring(xml)
    children = [loc for s in root.findall(f"{SM}sitemap") if (loc := _text(s, f"{SM}loc"))]
    entries = []
    for u in root.findall(f"{SM}url"):
        loc = _text(u, f"{SM}loc")
        if not loc:
            continue
        date = (_parse_date(_text(u, f"{NEWS}news/{NEWS}publication_date"))
                or _parse_date(_text(u, f"{SM}lastmod")))
        entries.append((loc, date, _text(u, f"{NEWS}news/{NEWS}title")))
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


def _from_sitemap(fetcher: Fetcher, url: str, on_error: ErrorSink):
    xml = _fetch_text(fetcher, url, on_error)
    if xml is None:
        return []
    children, entries = _parse_sitemap(xml, url, on_error)
    for child in children:  # one level only
        child_xml = _fetch_text(fetcher, child, on_error)
        if child_xml is not None:
            entries.extend(_parse_sitemap(child_xml, child, on_error)[1])
    return entries


def _from_feed(fetcher: Fetcher, url: str, on_error: ErrorSink):
    body = _fetch_text(fetcher, url, on_error)
    if body is None:
        return []
    import feedparser  # extra `crawl`; imported lazily so `weave crawl` can explain its absence

    out = []
    for e in feedparser.parse(body).entries:
        parsed = e.get("published_parsed") or e.get("updated_parsed")  # feedparser yields UTC
        date = datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None
        if e.get("link"):
            out.append((e["link"], date, e.get("title")))
    return out


def _on_domain(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


def discover(outlet: Outlet, fetcher: Fetcher, window: tuple[datetime, datetime],
             on_error: ErrorSink | None = None) -> list[Candidate]:
    """`on_error(url, reason)` is called for each sitemap/feed that fails to fetch or parse."""
    start, end = _utc(window[0]), _utc(window[1])
    sink = on_error or (lambda url, reason: None)
    sources = ([_from_sitemap(fetcher, u, sink) for u in outlet.sitemaps]
               + [_from_feed(fetcher, u, sink) for u in outlet.feeds])
    found: dict[str, Candidate] = {}
    for entries in sources:
        has_dates = any(d for _, d, _ in entries)
        for url, date, title in entries:
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
                found[key] = Candidate(key, outlet.name, date, title)
    result = sorted(found.values(), key=lambda c: (c.published_at is None,
                    c.published_at or start, c.url))
    if outlet.per_day is not None:
        kept: dict = {}
        for c in sorted((c for c in result if c.published_at),
                        key=lambda c: (c.published_at, c.url), reverse=True):
            day = kept.setdefault(c.published_at.date(), [])
            if len(day) < outlet.per_day:
                day.append(c)
        result = sorted([c for d in kept.values() for c in d]
                        + [c for c in result if c.published_at is None],
                        key=lambda c: (c.published_at is None, c.published_at or start, c.url))
    return result
