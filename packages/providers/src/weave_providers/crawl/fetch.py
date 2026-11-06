"""Fetching seam for the crawler: a Fetcher returns a page or a reason it failed."""
import asyncio
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "EventWeaveBot/0.1 (+https://github.com/diegohh0411/event-weave)"
_PRODUCT_TOKEN = USER_AGENT.split("/")[0]
MAX_REDIRECTS = 5


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    html: str | None
    error: str | None
    final_url: str | None = None  # where redirects ended up, when known
    redirect_to: str | None = None  # absolute Location of an unfollowed 3xx


class Fetcher(Protocol):
    def fetch(self, url: str) -> Fetched: ...


class FixtureFetcher:
    """Serves saved files or canned responses; never touches the network."""

    def __init__(self, routes: dict[str, "Fetched | Path"]):
        self.routes = routes
        self.calls: list[str] = []

    def fetch(self, url: str) -> Fetched:
        self.calls.append(url)
        if url not in self.routes:
            raise AssertionError(f"unexpected fetch: {url}")
        route = self.routes[url]
        if isinstance(route, Path):
            return Fetched(url, 200, route.read_text(encoding="utf-8"), None)
        return route


class HttpFetcher:
    """Plain HTTP fetcher for robots.txt, sitemaps and feeds, which a browser would mangle.

    Never follows redirects itself: a 3xx comes back with `redirect_to` so PoliteFetcher can
    check each hop against robots.txt before requesting it."""

    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 20.0):
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False,
                                    timeout=timeout, transport=transport)

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def fetch(self, url: str) -> Fetched:
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            return Fetched(url, 0, None, f"{type(exc).__name__}: {exc}")
        location = response.headers.get("location") if response.is_redirect else None
        redirect_to = urljoin(str(response.url), location) if location else None
        return Fetched(url, response.status_code, response.text, None, str(response.url),
                       redirect_to)

    def close(self) -> None:
        self._client.close()


class Crawl4aiFetcher:
    """Real fetcher backed by crawl4ai (extra `crawl`).

    One browser stays open for the fetcher's lifetime, driven from a private event loop.
    Use as a context manager or call close(). Browser errors come back as Fetched.error.
    """

    def __init__(self, crawler_factory: Callable[[], Any] | None = None):
        self._crawler_factory = crawler_factory or _default_crawler
        self._loop: asyncio.AbstractEventLoop | None = None
        self._crawler: Any = None

    def __enter__(self) -> "Crawl4aiFetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def fetch(self, url: str) -> Fetched:
        try:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            return self._loop.run_until_complete(self._fetch(url))
        except Exception as exc:
            return Fetched(url, 0, None, f"{type(exc).__name__}: {exc}")

    async def _fetch(self, url: str) -> Fetched:
        from crawl4ai import CrawlerRunConfig

        if self._crawler is None:
            crawler = self._crawler_factory()
            await crawler.start()
            self._crawler = crawler
        result = await self._crawler.arun(url=url, config=CrawlerRunConfig())
        status = result.status_code or 0
        if not result.success:
            return Fetched(url, status, None, result.error_message or "fetch failed")
        return Fetched(url, status, result.html, None, getattr(result, "redirected_url", None))

    def close(self) -> None:
        if self._loop is None:
            return
        try:
            if self._crawler is not None:
                self._loop.run_until_complete(self._crawler.close())
        finally:
            self._crawler = None
            self._loop.close()
            self._loop = None


def _default_crawler() -> Any:
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    return AsyncWebCrawler(config=BrowserConfig(user_agent=USER_AGENT))


class PoliteFetcher:
    """Wraps fetchers: honours robots.txt and spaces requests per domain.

    `inner` fetches article pages. `meta` (default: `inner`) fetches robots.txt and
    sitemaps/feeds, which need raw bytes rather than a rendered DOM. Both share the
    per-domain interval.

    robots.txt policy: 4xx means no restrictions; 5xx or an unreachable file means
    everything on that domain is disallowed. A redirect to another origin, or to a
    disallowed URL, discards the response. Redirects are followed here (max 5 hops), each hop
    checked and throttled before it is requested; fetchers must not follow them on their own,
    except a browser fetcher's JS redirects, which are discarded after the fact.
    """

    def __init__(
        self,
        inner: Fetcher,
        meta: Fetcher | None = None,
        min_interval: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.inner = inner
        self.meta = meta or inner
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._robots: dict[str, RobotFileParser | bool] = {}  # True = allow all, False = deny all
        self._last_request: dict[str, float] = {}

    def allowed(self, url: str) -> bool:
        rules = self._rules_for(url)
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(_PRODUCT_TOKEN, url)

    def fetch(self, url: str) -> Fetched:
        if self.meta is self.inner:
            return self._guarded(self.inner, url)
        # Resolve the redirect chain with the plain HTTP fetcher, then give only the final,
        # allowed URL to the browser fetcher.
        probe = self._guarded(self.meta, url)
        if probe.error or probe.status == 0 or probe.status >= 400:
            return probe
        final = probe.final_url or url
        got = self._guarded(self.inner, final)
        if got.error == "robots disallowed" or got.final_url is None and final == url:
            return replace(got, url=url)
        return replace(got, url=url, final_url=got.final_url or final)

    def meta_view(self) -> Fetcher:
        """A Fetcher for sitemaps and feeds: same politeness, routed to `meta`."""
        return _MetaView(self)

    def _guarded(self, fetcher: Fetcher, url: str) -> Fetched:
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            if hop and _origin(current) != _origin(url):
                return Fetched(url, 0, None, "robots disallowed")
            if not self.allowed(current):
                return Fetched(url, 0, None, "robots disallowed")
            got = self._throttled_fetch(fetcher, current)
            if not got.redirect_to:
                break
            current = got.redirect_to
        else:
            return Fetched(url, 0, None, f"too many redirects (> {MAX_REDIRECTS})")
        if current != url:
            got = replace(got, url=url, final_url=current)
        final = got.final_url
        if final and final != url:
            if _origin(final) != _origin(url) or not self.allowed(final):
                return Fetched(url, 0, None, "robots disallowed")
        return got

    def _rules_for(self, url: str) -> "RobotFileParser | bool":
        origin = _origin(url)
        if origin not in self._robots:
            got = self._fetch_robots(f"{origin}/robots.txt")
            if got.html is not None and got.status == 200:
                parser = RobotFileParser()
                parser.parse(got.html.splitlines())
                self._robots[origin] = parser
            else:
                self._robots[origin] = 400 <= got.status < 500
        return self._robots[origin]

    def _fetch_robots(self, url: str) -> Fetched:
        """robots.txt itself is not subject to robots rules; follow its redirects (max 5 hops)."""
        got = self._throttled_fetch(self.meta, url)
        for _ in range(MAX_REDIRECTS):
            if not got.redirect_to:
                return got
            got = self._throttled_fetch(self.meta, got.redirect_to)
        return got if not got.redirect_to else Fetched(url, 0, None, "too many redirects")

    def _throttled_fetch(self, fetcher: Fetcher, url: str) -> Fetched:
        domain = urlsplit(url).netloc.lower()
        last = self._last_request.get(domain)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        try:
            return fetcher.fetch(url)
        finally:
            self._last_request[domain] = self._clock()


class _MetaView:
    def __init__(self, polite: PoliteFetcher):
        self._polite = polite

    def fetch(self, url: str) -> Fetched:
        return self._polite._guarded(self._polite.meta, url)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"
