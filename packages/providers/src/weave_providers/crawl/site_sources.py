"""Find declared feeds and sitemaps without following arbitrary page links."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin


@dataclass(frozen=True)
class Source:
    kind: str
    url: str


@dataclass
class SourceResolution:
    sources: list[Source] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


class _Feeds(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag.lower() == 'link' and 'alternate' in a.get('rel', '').lower().split() and a.get('type', '').lower().split(';')[0] in ('application/rss+xml', 'application/atom+xml') and a.get('href'):
            self.urls.append(a['href'])


def resolve_sources(site, fetcher, cancelled=None) -> SourceResolution:
    """Resolve explicit or public site-advertised sources.

    ``cancelled`` is checked between discovery requests so a worker that lost its
    lease does not keep probing a site. URL transport validation remains the
    fetcher's responsibility; this function never opens a connection itself.
    """
    result = SourceResolution()
    stopped = cancelled or (lambda: False)
    def add(kind, url):
        source = Source(kind, url)
        if not any(existing.url == url for existing in result.sources) and len(result.sources) < 100:
            result.sources.append(source)
    for url in site.sitemaps:
        if stopped():
            return result
        add('sitemap', url)
    for url in site.feeds:
        if stopped():
            return result
        add('feed', url)
    if result.sources:
        return result
    base = f'https://{site.domain}'
    for path in ('/robots.txt', '/'):
        if stopped():
            return result
        url = base + path
        # robots.txt is not subject to its own rules. The polite meta view
        # exposes a separate guarded path for fetching it.
        fetch = getattr(fetcher, 'fetch_robots', fetcher.fetch) if path == '/robots.txt' else fetcher.fetch
        got = fetch(url)
        if stopped():
            return result
        if got.status != 200 or not got.html:
            result.errors.append((url, got.error or f'HTTP {got.status}'))
            continue
        if path == '/robots.txt':
            for line in got.html.splitlines():
                key, sep, value = line.partition(':')
                if sep and key.strip().lower() == 'sitemap' and value.strip():
                    add('sitemap', urljoin(url, value.strip()))
        else:
            parser = _Feeds()
            parser.feed(got.html)
            for link in parser.urls:
                add('feed', urljoin(got.final_url or url, link))
    if not result.sources:
        for path in ('/sitemap.xml', '/sitemap_index.xml'):
            if stopped():
                return result
            add('sitemap', base + path)
        for path in ('/feed', '/rss'):
            if stopped():
                return result
            add('feed', base + path)
    return result
