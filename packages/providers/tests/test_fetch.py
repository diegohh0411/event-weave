import httpx

from weave_providers.crawl.fetch import (
    Crawl4aiFetcher, Fetched, FixtureFetcher, HttpFetcher, PoliteFetcher, USER_AGENT)


def robots(body: str, status: int = 200) -> Fetched:
    return Fetched("https://ex.com/robots.txt", status, body, None)


def page(url: str) -> Fetched:
    return Fetched(url, 200, "<html></html>", None)


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def polite(inner, clock=None, **kw):
    clock = clock or FakeClock()
    return PoliteFetcher(inner, clock=clock, sleep=clock.sleep, **kw), clock


def test_user_agent_names_no_person():
    assert USER_AGENT == "EventWeaveBot/0.1 (+https://github.com/diegohh0411/event-weave)"


def test_disallowed_url_never_reaches_inner():
    inner = FixtureFetcher({"https://ex.com/robots.txt": robots("User-agent: *\nDisallow: /private")})
    pf, _ = polite(inner)
    result = pf.fetch("https://ex.com/private/a")
    assert inner.calls == ["https://ex.com/robots.txt"]
    assert result == Fetched("https://ex.com/private/a", 0, None, "robots disallowed")


def test_rules_for_our_product_token_apply():
    body = "User-agent: EventWeaveBot\nDisallow: /\n\nUser-agent: *\nAllow: /"
    inner = FixtureFetcher({"https://ex.com/robots.txt": robots(body)})
    pf, _ = polite(inner)
    assert pf.allowed("https://ex.com/a") is False


def test_allowed_url_is_fetched():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": robots("User-agent: *\nDisallow: /private"),
        "https://ex.com/a": page("https://ex.com/a"),
    })
    pf, _ = polite(inner)
    assert pf.fetch("https://ex.com/a").status == 200


def test_robots_404_allows_all():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": robots("", status=404),
        "https://ex.com/a": page("https://ex.com/a"),
    })
    pf, _ = polite(inner)
    assert pf.allowed("https://ex.com/a") is True


def test_robots_5xx_disallows_all():
    inner = FixtureFetcher({"https://ex.com/robots.txt": robots("", status=503)})
    pf, _ = polite(inner)
    assert pf.allowed("https://ex.com/a") is False
    assert pf.fetch("https://ex.com/a").error == "robots disallowed"


def test_robots_unreachable_disallows_all():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": Fetched("https://ex.com/robots.txt", 0, None, "timeout"),
    })
    pf, _ = polite(inner)
    assert pf.allowed("https://ex.com/a") is False


def test_robots_fetched_once_per_domain():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": robots("User-agent: *\nAllow: /"),
        "https://ex.com/a": page("https://ex.com/a"),
        "https://ex.com/b": page("https://ex.com/b"),
    })
    pf, _ = polite(inner)
    pf.fetch("https://ex.com/a")
    pf.fetch("https://ex.com/b")
    assert inner.calls.count("https://ex.com/robots.txt") == 1


def test_per_domain_interval():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": robots("User-agent: *\nAllow: /"),
        "https://ex.com/a": page("https://ex.com/a"),
        "https://ex.com/b": page("https://ex.com/b"),
        "https://other.com/robots.txt": Fetched("https://other.com/robots.txt", 404, None, None),
        "https://other.com/a": page("https://other.com/a"),
    })
    pf, clock = polite(inner)
    pf.fetch("https://ex.com/a")   # robots then page: page waits out the robots request
    pf.fetch("https://ex.com/b")
    assert clock.sleeps == [2.0, 2.0]
    clock.sleeps.clear()
    pf.allowed("https://other.com/a")  # other domain's first request: no wait
    assert clock.sleeps == []


def test_no_sleep_when_interval_already_elapsed():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": robots("User-agent: *\nAllow: /"),
        "https://ex.com/a": page("https://ex.com/a"),
        "https://ex.com/b": page("https://ex.com/b"),
    })
    pf, clock = polite(inner)
    pf.fetch("https://ex.com/a")
    clock.sleeps.clear()
    clock.now += 5
    pf.fetch("https://ex.com/b")
    assert clock.sleeps == []


class FakeResult:
    def __init__(self, html="<html></html>"):
        self.success, self.status_code, self.html, self.error_message = True, 200, html, None


class FakeCrawler:
    def __init__(self, fail_with: Exception | None = None):
        self.fail_with, self.started, self.closed, self.urls = fail_with, 0, 0, []

    async def start(self):
        self.started += 1

    async def arun(self, url, config):
        self.urls.append(url)
        if self.fail_with:
            raise self.fail_with
        return FakeResult()

    async def close(self):
        self.closed += 1


def test_crawl4ai_fetcher_reuses_one_browser_and_closes_it():
    made = []

    def factory():
        made.append(FakeCrawler())
        return made[-1]

    with Crawl4aiFetcher(crawler_factory=factory) as f:
        assert f.fetch("https://ex.com/a") == Fetched("https://ex.com/a", 200, "<html></html>", None)
        f.fetch("https://ex.com/b")
    assert len(made) == 1 and made[0].started == 1 and made[0].closed == 1
    assert made[0].urls == ["https://ex.com/a", "https://ex.com/b"]


def test_crawl4ai_fetcher_returns_browser_errors_instead_of_raising():
    crawler = FakeCrawler(fail_with=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))
    with Crawl4aiFetcher(crawler_factory=lambda: crawler) as f:
        got = f.fetch("https://ex.com/a")
    assert got == Fetched("https://ex.com/a", 0, None, "RuntimeError: net::ERR_NAME_NOT_RESOLVED")


def test_crawl4ai_fetcher_start_failure_is_a_fetched_error():
    class Broken(FakeCrawler):
        async def start(self):
            raise OSError("no browser")

    with Crawl4aiFetcher(crawler_factory=Broken) as f:
        assert f.fetch("https://ex.com/a").error == "OSError: no browser"


def test_crawl4ai_fetcher_close_without_fetch_is_safe():
    Crawl4aiFetcher(crawler_factory=lambda: 1 / 0).close()


def test_origin_key_ignores_scheme_and_host_case():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": robots("User-agent: *\nDisallow: /private"),
        "https://Ex.COM/ok": page("https://Ex.COM/ok"),
    })
    pf, _ = polite(inner)
    assert pf.fetch("HTTPS://EX.com/private/a").error == "robots disallowed"
    assert pf.fetch("https://Ex.COM/ok").status == 200
    assert inner.calls == ["https://ex.com/robots.txt", "https://Ex.COM/ok"]


class Redirecting:
    def __init__(self, final_url):
        self.final_url, self.calls = final_url, []

    def fetch(self, url):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return robots("User-agent: *\nDisallow: /private")
        return Fetched(url, 200, "<html>secret</html>", None, self.final_url)


def test_redirect_to_other_origin_is_discarded():
    pf, _ = polite(Redirecting("https://elsewhere.com/a"))
    assert pf.fetch("https://ex.com/a") == Fetched("https://ex.com/a", 0, None, "robots disallowed")


def test_redirect_to_disallowed_path_is_discarded():
    pf, _ = polite(Redirecting("https://ex.com/private/a"))
    assert pf.fetch("https://ex.com/a") == Fetched("https://ex.com/a", 0, None, "robots disallowed")


def test_redirect_within_origin_to_allowed_path_is_kept():
    pf, _ = polite(Redirecting("https://ex.com/b"))
    got = pf.fetch("https://ex.com/a")
    assert got.status == 200 and got.html == "<html>secret</html>"


def test_http_fetcher_sends_user_agent_and_follows_redirects():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://ex.com/new"})
        return httpx.Response(200, text="User-agent: *\nDisallow: /x")

    with HttpFetcher(transport=httpx.MockTransport(handler)) as f:
        got = f.fetch("https://ex.com/old")
    assert got == Fetched("https://ex.com/old", 200, "User-agent: *\nDisallow: /x", None, "https://ex.com/new")
    assert seen == [USER_AGENT, USER_AGENT]


def test_http_fetcher_returns_errors_and_statuses():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/boom":
            raise httpx.ConnectTimeout("slow")
        return httpx.Response(404, text="nope")

    with HttpFetcher(transport=httpx.MockTransport(handler)) as f:
        assert f.fetch("https://ex.com/boom").error.startswith("ConnectTimeout")
        assert f.fetch("https://ex.com/missing").status == 404


def test_crawl4ai_fetcher_fills_final_url():
    class Redirected(FakeResult):
        redirected_url = "https://ex.com/b"

    class C(FakeCrawler):
        async def arun(self, url, config):
            return Redirected()

    with Crawl4aiFetcher(crawler_factory=C) as f:
        assert f.fetch("https://ex.com/a").final_url == "https://ex.com/b"


def test_meta_view_uses_meta_fetcher_but_same_politeness():
    inner = FixtureFetcher({"https://ex.com/a": page("https://ex.com/a")})
    meta = FixtureFetcher({
        "https://ex.com/robots.txt": robots("User-agent: *\nDisallow: /private"),
        "https://ex.com/sitemap.xml": page("https://ex.com/sitemap.xml"),
    })
    pf, clock = polite(inner, meta=meta)
    assert pf.meta_view().fetch("https://ex.com/sitemap.xml").status == 200
    assert pf.meta_view().fetch("https://ex.com/private/feed").error == "robots disallowed"
    pf.fetch("https://ex.com/a")
    assert inner.calls == ["https://ex.com/a"]
    assert clock.sleeps == [2.0, 2.0]
