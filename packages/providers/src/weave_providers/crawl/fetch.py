"""Fetching seam for the crawler: a Fetcher returns a page or a reason it failed."""
import asyncio
import contextlib
import os
import random
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
# Browser identity for crawl traffic: CDN WAFs block bot UAs and non-browser TLS fingerprints.
IMPERSONATE = "safari17_0"
BROWSER_LOCALE = os.environ.get("WEAVE_CRAWL_LOCALE", "es-MX")
BROWSER_TIMEZONE = os.environ.get("WEAVE_CRAWL_TIMEZONE", "America/Mexico_City")
ACCEPT_LANGUAGE = "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7"


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
    check each hop against robots.txt before requesting it.

    Without an explicit transport it impersonates Safari's TLS handshake and headers via
    curl_cffi (extra `crawl`): CDN WAFs such as CloudFront reject Python's TLS fingerprint
    outright, whatever User-Agent is sent."""

    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 20.0,
                 proxy: str | None = None):
        self._session = None
        self._client = None
        if transport is None:
            try:
                from curl_cffi import requests as curl_requests
            except ImportError:
                curl_requests = None
            if curl_requests is not None:
                self._session = curl_requests.Session(
                    impersonate=IMPERSONATE, timeout=timeout, trust_env=False,
                    allow_redirects=False, proxy=proxy,
                    headers={"Accept-Language": ACCEPT_LANGUAGE})
                return
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False,
                                    timeout=timeout, transport=transport, proxy=proxy, trust_env=False)

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def fetch(self, url: str) -> Fetched:
        if self._session is not None:
            return self._fetch_impersonated(url)
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            return Fetched(url, 0, None, f"{type(exc).__name__}: {exc}")
        location = response.headers.get("location") if response.is_redirect else None
        redirect_to = urljoin(str(response.url), location) if location else None
        return Fetched(url, response.status_code, response.text, None, str(response.url),
                       redirect_to)

    def _fetch_impersonated(self, url: str) -> Fetched:
        from curl_cffi.requests.exceptions import RequestException
        try:
            response = self._session.get(url)
        except RequestException as exc:
            return Fetched(url, 0, None, f"{type(exc).__name__}: {exc}")
        location = response.headers.get("location") if 300 <= response.status_code < 400 else None
        redirect_to = urljoin(str(response.url), location) if location else None
        return Fetched(url, response.status_code, response.text, None, str(response.url),
                       redirect_to)

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
        if self._client is not None:
            self._client.close()


class Crawl4aiFetcher:
    """Real fetcher backed by crawl4ai (extra `crawl`).

    One browser stays open for the fetcher's lifetime, driven from a private event loop.
    Use as a context manager or call close(). Browser errors come back as Fetched.error.
    """

    def __init__(self, crawler_factory: Callable[[], Any] | None = None, validator=None,
                 isolated_proxy: str | None = None):
        self.isolated_proxy = isolated_proxy
        # Hard cap per page (crawl4ai's page_timeout only covers navigation), so a wedged
        # page can never hold the renderer's single slot past the caller's own timeout.
        self.deadline = 70.0
        self.on_close_timeout: Callable[[], None] | None = None
        self.validator = validator
        self.navigation_guard = None
        self.cancelled: Callable[[], bool] = lambda: False
        self._crawler_factory = crawler_factory or (lambda: _default_crawler(public=bool(self.validator), isolated_proxy=self.isolated_proxy))
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
            return self._loop.run_until_complete(
                asyncio.wait_for(self._fetch(url), self.deadline))
        except TimeoutError:
            return Fetched(url, 0, None, f"render deadline exceeded ({self.deadline:.0f}s)")
        except Exception as exc:
            return Fetched(url, 0, None, f"{type(exc).__name__}: {exc}")

    async def _fetch(self, url: str) -> Fetched:
        from crawl4ai import CrawlerRunConfig

        if self._crawler is None:
            crawler = self._crawler_factory()
            if self.validator:
                async def install(page, *, context, **kwargs):
                    async def guard(route):
                        # Validate, then let Chromium's own network stack send the request.
                        # route.fetch() would re-issue it from Playwright's Node client, with a
                        # non-browser TLS fingerprint and no Sec-Fetch-* headers. The browser
                        # follows redirect hops itself: the egress proxy enforces public-only
                        # destinations on every hop, and PoliteFetcher discards off-domain final URLs.
                        try:
                            if self.cancelled():
                                raise ValueError('crawl cancelled')
                            current = route.request.url
                            self.validator(current)
                            if route.request.is_navigation_request() and self.navigation_guard:
                                if not self.navigation_guard(current):
                                    raise ValueError('navigation disallowed')
                        except Exception:
                            with contextlib.suppress(Exception):
                                await route.abort()
                            return
                        with contextlib.suppress(Exception):
                            await route.continue_()
                    await context.route('**/*', guard)
                    async def block_socket(socket):
                        await socket.close()
                    await context.route_web_socket('**/*', block_socket)
                crawler.crawler_strategy.set_hook('on_page_context_created', install)
            if self.isolated_proxy:
                crawler.crawler_strategy.set_hook('before_goto', _humanize_before_goto)
                crawler.crawler_strategy.set_hook('after_goto', _humanize_after_goto)
            await crawler.start()
            self._crawler = crawler
        if self.validator:
            self.validator(url)
        stealth = {}
        if self.isolated_proxy:
            # No magic/simulate_user/override_navigator: they inject a naive navigator
            # override (plugins=[1..5], languages=en-US) that is itself a bot tell.
            stealth = dict(locale=BROWSER_LOCALE, timezone_id=BROWSER_TIMEZONE)
        result = await self._crawler.arun(url=url, config=CrawlerRunConfig(
            check_robots_txt=False, page_timeout=45000,
            delay_before_return_html=2.0 if self.isolated_proxy else 0.1, **stealth))
        status = result.status_code or 0
        if not result.success:
            return Fetched(url, status, None, result.error_message or "fetch failed")
        return Fetched(url, status, result.html, None, getattr(result, "redirected_url", None))

    def close(self) -> None:
        if self._loop is None:
            return
        try:
            if self._crawler is not None:
                # Teardown errors (in-flight routes racing the close) must not discard a fetched page.
                try:
                    self._loop.run_until_complete(asyncio.wait_for(self._crawler.close(), 15))
                except TimeoutError:
                    if self.on_close_timeout:
                        self.on_close_timeout()
                except Exception:
                    pass
        finally:
            self._crawler = None
            self._loop.close()
            self._loop = None


async def _humanize_before_goto(page, *, context, **kwargs):
    # crawl4ai always plants a synthetic `cookiesEnabled` cookie; real browsers never send it.
    with contextlib.suppress(Exception):
        await context.clear_cookies(name="cookiesEnabled")
    return page


async def _humanize_after_goto(page, *, context, **kwargs):
    # Real input events (not JS-dispatched) that behavioural sensors look for.
    with contextlib.suppress(Exception):
        for _ in range(random.randint(2, 4)):
            await page.mouse.move(random.randint(80, 1500), random.randint(80, 900),
                                  steps=random.randint(8, 25))
            await asyncio.sleep(random.uniform(0.05, 0.3))
        await page.mouse.wheel(0, random.randint(250, 900))
    return page


_GPU_KILL_SWITCHES = {"--disable-gpu", "--disable-gpu-compositing", "--disable-software-rasterizer"}


def _strip_gpu_kill_switches(manager) -> None:
    """crawl4ai 0.9.5 hardcodes GPU-off flags at launch even with enable_stealth, which
    leaves WebGL unavailable (a headless tell). Drop them so SwiftShader WebGL works."""
    build = manager._build_browser_args

    def build_without_gpu_kill_switches():
        args = build()
        args["args"] = [a for a in args["args"] if a not in _GPU_KILL_SWITCHES]
        return args
    manager._build_browser_args = build_without_gpu_kill_switches


def _default_crawler(public: bool = False, isolated_proxy: str | None = None) -> Any:
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    if isolated_proxy:
        from crawl4ai import UndetectedAdapter
        from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
        # Stealth profile for the isolated renderer:
        # - patchright (UndetectedAdapter) patches CDP/webdriver leaks at the driver level;
        # - headful under Xvfb when a DISPLAY exists, so no "HeadlessChrome" brand/UA;
        # - user_agent="" keeps Chromium's own UA and client hints, which stay consistent
        #   with navigator.userAgentData (crawl4ai's default spoofs an old Chrome/116);
        # - enable_stealth keeps WebGL (SwiftShader) instead of --disable-gpu.
        headless = not os.environ.get("DISPLAY")
        config = BrowserConfig(
            headless=headless, java_script_enabled=True, enable_stealth=True,
            user_agent="", viewport_width=1920, viewport_height=1080,
            proxy_config={"server": isolated_proxy},
            extra_args=["--proxy-bypass-list=<-loopback>", "--disable-quic",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                        f"--lang={BROWSER_LOCALE}", "--window-size=1920,1080",
                        # Software WebGL: a context-less WebGL is a classic headless tell.
                        "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        config.headers.pop("sec-ch-ua", None)
        strategy = AsyncPlaywrightCrawlerStrategy(browser_config=config,
                                                   browser_adapter=UndetectedAdapter())
        _strip_gpu_kill_switches(strategy.browser_manager)
        return AsyncWebCrawler(config=config, crawler_strategy=strategy)
    # Only the isolated service can enable JS for guarded public crawls.
    return AsyncWebCrawler(config=BrowserConfig(user_agent=USER_AGENT, java_script_enabled=not public))


class RemoteBrowserFetcher:
    """Bounded RPC to the isolated renderer. Errors never trigger local fallback."""

    def __init__(self, endpoint: str, *, transport=None):
        self.guard = None
        self._client = httpx.Client(base_url=endpoint, timeout=90, transport=transport,
                                    trust_env=False, follow_redirects=False)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()

    def fetch(self, url):
        if self.guard is None or not self.guard.domain:
            return Fetched(url, 0, None, 'isolated browser requires a domain guard')
        if self.guard.cancelled():
            return Fetched(url, 0, None, 'crawl cancelled')
        try:
            with self._client.stream('POST', '/fetch', json={
                'url': url, 'domain': self.guard.domain,
                'respect_robots': self.guard.respect_robots,
            }) as response:
                response.raise_for_status()
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if self.guard.cancelled():
                        return Fetched(url, 0, None, 'crawl cancelled')
                    chunks.extend(chunk)
                    if len(chunks) > 12_000_000:
                        raise ValueError('browser response too large')
                import json
                return Fetched(**json.loads(chunks))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return Fetched(url, 0, None, f'isolated browser: {exc}')


def browser_fetcher_from_env():
    endpoint = os.environ.get('WEAVE_CRAWL_BROWSER_URL')
    from .public_urls import validate_public_url
    return RemoteBrowserFetcher(endpoint) if endpoint else Crawl4aiFetcher(validator=validate_public_url)


def respect_robots_from_env() -> bool:
    value = os.environ.get("WEAVE_CRAWL_RESPECT_ROBOTS", "false").strip().lower()
    if value not in ("true", "false", "1", "0"):
        raise ValueError("WEAVE_CRAWL_RESPECT_ROBOTS must be true or false")
    return value in ("true", "1")


REDIRECTED_AWAY = "redirect outside requested domain"


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
        validator=None,
        domain: str | None = None,
        respect_robots: bool = True,
        throttle=None,
    ):
        self.throttle = throttle
        self.respect_robots = respect_robots
        self.validator = validator
        self.domain = domain
        if isinstance(inner, RemoteBrowserFetcher):
            inner.guard = self
        self.cancelled: Callable[[], bool] = lambda: False
        if validator and isinstance(inner, Crawl4aiFetcher):
            if inner._crawler is not None:
                raise ValueError("cannot enable public guarding on an already started browser")
            inner.validator = validator
            inner.cancelled = lambda: self.cancelled()
            inner.navigation_guard = lambda url: self._in_domain(url) and self.allowed(url)
        self.inner = inner
        self.meta = meta or inner
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._robots: dict[str, RobotFileParser | bool] = {}  # True = allow all, False = deny all
        self._last_request: dict[str, float] = {}

    def _in_domain(self, url):
        host = (urlsplit(url).hostname or '').lower().rstrip('.')
        domain = (self.domain or '').lower().rstrip('.')
        # The commonplace www variant is equivalent to the requested apex.
        if domain.startswith('www.'):
            domain = domain[4:]
        return bool(domain and (host == domain or host.endswith('.' + domain)))

    def allowed(self, url: str) -> bool:
        if self.validator:
            try:
                self.validator(url)
            except ValueError:
                return False
        if not self.respect_robots:
            return True
        rules = self._rules_for(url)
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(_PRODUCT_TOKEN, url)

    def fetch(self, url: str) -> Fetched:
        got = self._guarded(self.inner, url)
        if got.error:
            return replace(got, url=url)
        return replace(got, url=url, final_url=got.final_url or url)

    def meta_view(self) -> Fetcher:
        """A Fetcher for sitemaps and feeds: same politeness, routed to `meta`."""
        return _MetaView(self)

    def _guarded(self, fetcher: Fetcher, url: str) -> Fetched:
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            if self.cancelled():
                return Fetched(url, 0, None, 'crawl cancelled')
            if self.validator:
                try:
                    self.validator(current)
                except ValueError as exc:
                    return Fetched(url, 0, None, str(exc))
            if hop and _origin(current) != _origin(url) and not self._in_domain(current):
                return Fetched(url, 0, None, REDIRECTED_AWAY, current)
            if not self.allowed(current):
                return Fetched(url, 0, None, "robots disallowed")
            got = self._safe_fetch(fetcher, current)
            if not got.redirect_to:
                break
            current = got.redirect_to
        else:
            return Fetched(url, 0, None, f"too many redirects (> {MAX_REDIRECTS})")
        if current != url:
            got = replace(got, url=url, final_url=current)
        final = got.final_url
        if final and final != url:
            if _origin(final) != _origin(url) and not self._in_domain(final):
                return Fetched(url, 0, None, REDIRECTED_AWAY, final)
            if not self.allowed(final):
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
        got = self._safe_fetch(self.meta, url)
        for _ in range(MAX_REDIRECTS):
            if not got.redirect_to:
                return got
            if _origin(got.redirect_to) != _origin(url) and not self._in_domain(got.redirect_to):
                return Fetched(url, 0, None, "robots disallowed")
            got = self._safe_fetch(self.meta, got.redirect_to)
        return got if not got.redirect_to else Fetched(url, 0, None, "too many redirects")

    def _safe_fetch(self, fetcher, url):
        return self._throttled_fetch(fetcher, url)

    def _throttled_fetch(self, fetcher: Fetcher, url: str) -> Fetched:
        if self.cancelled():
            return Fetched(url, 0, None, 'crawl cancelled')
        domain = urlsplit(url).netloc.lower()
        if self.throttle is not None:
            with self.throttle.acquire((urlsplit(url).hostname or "").lower()):
                if self.cancelled():
                    return Fetched(url, 0, None, "crawl cancelled")
                if self.validator:
                    try:
                        self.validator(url)
                    except ValueError as exc:
                        return Fetched(url, 0, None, str(exc))
                return fetcher.fetch(url)
        last = self._last_request.get(domain)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        if self.cancelled():
            return Fetched(url, 0, None, 'crawl cancelled')
        if self.validator:
            try:
                self.validator(url)
            except ValueError as exc:
                return Fetched(url, 0, None, str(exc))
        if self.cancelled():
            return Fetched(url, 0, None, 'crawl cancelled')
        try:
            return fetcher.fetch(url)
        finally:
            self._last_request[domain] = self._clock()


class _MetaView:
    def __init__(self, polite: PoliteFetcher):
        self._polite = polite

    def fetch(self, url: str) -> Fetched:
        return self._polite._guarded(self._polite.meta, url)

    def fetch_robots(self, url: str) -> Fetched:
        return self._polite._fetch_robots(url)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"
