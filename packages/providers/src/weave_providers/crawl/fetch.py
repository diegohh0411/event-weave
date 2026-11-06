"""Fetching seam for the crawler: a Fetcher returns a page or a reason it failed."""
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "EventWeaveBot/0.1 (+https://github.com/diegohh0411/event-weave)"
_PRODUCT_TOKEN = USER_AGENT.split("/")[0]


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    html: str | None
    error: str | None


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
        return Fetched(url, status, result.html, None)

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
    """Wraps a Fetcher: honours robots.txt and spaces requests per domain.

    robots.txt policy: 4xx means no restrictions; 5xx or an unreachable file means
    everything on that domain is disallowed. Every inner request, robots.txt included,
    counts toward the per-domain interval.
    """

    def __init__(
        self,
        inner: Fetcher,
        min_interval: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.inner = inner
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
        if not self.allowed(url):
            return Fetched(url, 0, None, "robots disallowed")
        return self._throttled_fetch(url)

    def _rules_for(self, url: str) -> "RobotFileParser | bool":
        parts = urlsplit(url)
        origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
        if origin not in self._robots:
            got = self._throttled_fetch(f"{origin}/robots.txt")
            if got.html is not None and got.status == 200:
                parser = RobotFileParser()
                parser.parse(got.html.splitlines())
                self._robots[origin] = parser
            else:
                self._robots[origin] = 400 <= got.status < 500
        return self._robots[origin]

    def _throttled_fetch(self, url: str) -> Fetched:
        domain = urlsplit(url).netloc.lower()
        last = self._last_request.get(domain)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        try:
            return self.inner.fetch(url)
        finally:
            self._last_request[domain] = self._clock()
