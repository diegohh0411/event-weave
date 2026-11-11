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




def test_redirect_to_disallowed_path_is_discarded():
    pf, _ = polite(Redirecting("https://ex.com/private/a"))
    assert pf.fetch("https://ex.com/a") == Fetched("https://ex.com/a", 0, None, "robots disallowed")


def test_redirect_within_origin_to_allowed_path_is_kept():
    pf, _ = polite(Redirecting("https://ex.com/b"))
    got = pf.fetch("https://ex.com/a")
    assert got.status == 200 and got.html == "<html>secret</html>"


def test_http_fetcher_sends_user_agent_and_exposes_redirects_without_following():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers["user-agent"]))
        return httpx.Response(301, headers={"location": "/new"})

    with HttpFetcher(transport=httpx.MockTransport(handler)) as f:
        got = f.fetch("https://ex.com/old")
    assert got.status == 301 and got.redirect_to == "https://ex.com/new"
    assert seen == [("/old", USER_AGENT)]


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
        "https://ex.com/a": page("https://ex.com/a"),
    })
    pf, clock = polite(inner, meta=meta)
    assert pf.meta_view().fetch("https://ex.com/sitemap.xml").status == 200
    assert pf.meta_view().fetch("https://ex.com/private/feed").error == "robots disallowed"
    pf.fetch("https://ex.com/a")
    assert inner.calls == ["https://ex.com/a"]
    assert clock.sleeps == [2.0, 2.0]


def redirect(url, to):
    return Fetched(url, 302, None, None, url, to)


ROBOTS = robots("User-agent: *\nDisallow: /private")


def test_redirect_into_disallowed_path_never_requests_it():
    inner = FixtureFetcher({"https://ex.com/robots.txt": ROBOTS,
                            "https://ex.com/a": redirect("https://ex.com/a", "https://ex.com/private/x")})
    pf, _ = polite(inner)
    assert pf.fetch("https://ex.com/a") == Fetched("https://ex.com/a", 0, None, "robots disallowed")
    assert "https://ex.com/private/x" not in inner.calls


def test_redirect_chain_stops_at_disallowed_middle_hop():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": ROBOTS,
        "https://ex.com/a": redirect("https://ex.com/a", "https://ex.com/private/b"),
        "https://ex.com/private/b": redirect("https://ex.com/private/b", "https://ex.com/c"),
        "https://ex.com/c": page("https://ex.com/c"),
    })
    pf, _ = polite(inner)
    assert pf.fetch("https://ex.com/a").error == "robots disallowed"
    assert inner.calls == ["https://ex.com/robots.txt", "https://ex.com/a"]


def test_allowed_redirect_chain_reaches_article_and_reports_final_url():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": ROBOTS,
        "https://ex.com/a": redirect("https://ex.com/a", "https://ex.com/b"),
        "https://ex.com/b": redirect("https://ex.com/b", "https://ex.com/c"),
        "https://ex.com/c": page("https://ex.com/c"),
    })
    pf, _ = polite(inner)
    got = pf.fetch("https://ex.com/a")
    assert got.status == 200 and got.url == "https://ex.com/a" and got.final_url == "https://ex.com/c"
    assert inner.calls == ["https://ex.com/robots.txt", "https://ex.com/a",
                           "https://ex.com/b", "https://ex.com/c"]


def test_redirect_chain_to_disallowed_is_never_followed():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": ROBOTS,
        "https://ex.com/a": redirect("https://ex.com/a", "https://ex.com/private/b"),
    })
    pf, _ = polite(inner)
    assert pf.fetch("https://ex.com/a").error == "robots disallowed"
    assert "https://ex.com/private/b" not in inner.calls


def test_more_than_five_redirects_is_an_error():
    routes = {"https://ex.com/robots.txt": ROBOTS}
    for i in range(8):
        routes[f"https://ex.com/{i}"] = redirect(f"https://ex.com/{i}", f"https://ex.com/{i + 1}")
    inner = FixtureFetcher(routes)
    pf, _ = polite(inner)
    got = pf.fetch("https://ex.com/0")
    assert got.status == 0 and "too many redirects" in got.error
    assert "https://ex.com/6" not in inner.calls


def test_robots_txt_redirect_is_followed():
    inner = FixtureFetcher({
        "https://ex.com/robots.txt": redirect("https://ex.com/robots.txt", "https://ex.com/r2.txt"),
        "https://ex.com/r2.txt": robots("User-agent: *\nDisallow: /private"),
        "https://ex.com/ok": page("https://ex.com/ok"),
    })
    pf, _ = polite(inner)
    assert pf.fetch("https://ex.com/private/a").error == "robots disallowed"
    assert pf.fetch("https://ex.com/ok").status == 200


def test_validated_redirect_to_www_checks_its_robots():
    f = FixtureFetcher({'https://ex.com/robots.txt': robots('', 404),
                        'https://ex.com/a': redirect('https://ex.com/a', 'https://www.ex.com/a'),
                        'https://www.ex.com/robots.txt': robots('User-agent: *\nDisallow: /a')})
    pf, _ = polite(f, validator=lambda url: url, domain='ex.com')
    assert pf.fetch('https://ex.com/a').error == 'robots disallowed'
    assert 'https://www.ex.com/a' not in f.calls


def test_www_to_apex_redirect_succeeds_after_robots_checks():
    f = FixtureFetcher({'https://www.ex.com/robots.txt': robots('', 404),
                        'https://www.ex.com/a': redirect('https://www.ex.com/a', 'https://ex.com/a'),
                        'https://ex.com/robots.txt': robots('', 404),
                        'https://ex.com/a': page('https://ex.com/a')})
    pf, _ = polite(f, validator=lambda url: url, domain='www.ex.com')
    assert pf.fetch('https://www.ex.com/a').status == 200
    assert 'https://ex.com/robots.txt' in f.calls


def test_unsafe_initial_url_never_reaches_transport():
    f = FixtureFetcher({})
    def deny(url):
        raise ValueError('unsafe URL')
    pf, _ = polite(f, validator=deny, domain='ex.com')
    assert pf.fetch('http://127.0.0.1/a').status == 0
    assert f.calls == []


def test_cancel_after_redirect_stops_before_next_transport():
    f = FixtureFetcher({'https://ex.com/robots.txt': robots('', 404),
                        'https://ex.com/a': redirect('https://ex.com/a', 'https://ex.com/b')})
    pf, _ = polite(f)
    pf.cancelled = lambda: 'https://ex.com/a' in f.calls
    assert pf.fetch('https://ex.com/a').status == 0
    assert f.calls == ['https://ex.com/robots.txt', 'https://ex.com/a']


def test_disabled_robots_does_not_fetch_rules_but_keeps_validation_and_redirect_guards():
    source = 'https://ex.com/private/a'
    inner = FixtureFetcher({source: redirect(source, 'https://elsewhere.com/a')})
    pf = PoliteFetcher(inner, respect_robots=False, min_interval=0,
                       validator=lambda u: u, domain='ex.com')
    assert pf.fetch(source).error == 'redirect outside requested domain'
    assert inner.calls == [source]
    def deny(url):
        raise ValueError('unsafe URL')
    pf.validator = deny
    assert pf.fetch(source).error == 'unsafe URL'
    assert inner.calls == [source]


def test_disabled_robots_allows_disallowed_path_and_preserves_spacing():
    url = 'https://ex.com/private/a'
    inner = FixtureFetcher({url: page(url)})
    pf, clock = polite(inner, respect_robots=False)
    assert pf.fetch(url).status == 200
    assert pf.meta_view().fetch(url).status == 200
    assert clock.sleeps == [2.0]
    assert inner.calls == [url, url]


def test_robots_environment_defaults_disabled_and_rejects_typos(monkeypatch):
    from weave_providers.crawl.fetch import respect_robots_from_env
    monkeypatch.delenv('WEAVE_CRAWL_RESPECT_ROBOTS', raising=False)
    assert respect_robots_from_env() is False
    monkeypatch.setenv('WEAVE_CRAWL_RESPECT_ROBOTS', 'true')
    assert respect_robots_from_env() is True
    monkeypatch.setenv('WEAVE_CRAWL_RESPECT_ROBOTS', 'treu')
    import pytest
    with pytest.raises(ValueError):
        respect_robots_from_env()


def test_offsite_redirect_distinct_from_robots():
    from weave_providers.crawl.fetch import REDIRECTED_AWAY
    inner = FixtureFetcher({"https://ex.com/robots.txt": ROBOTS,
                            "https://ex.com/x": redirect("https://ex.com/x", "https://b.com/y")})
    pf, _ = polite(inner)
    got = pf.fetch("https://ex.com/x")
    assert (got.error, got.final_url) == (REDIRECTED_AWAY, "https://b.com/y")
    assert "https://b.com/y" not in inner.calls
    inner = FixtureFetcher({"https://ex.com/robots.txt": ROBOTS,
                            "https://ex.com/x": redirect("https://ex.com/x", "https://ex.com/private")})
    pf, _ = polite(inner)
    assert pf.fetch("https://ex.com/x").error == "robots disallowed"


def test_browser_offsite_final_url_is_navigated_away():
    from weave_providers.crawl.fetch import NAVIGATED_AWAY
    pf, _ = polite(Redirecting("https://elsewhere.com/a"))
    assert pf.fetch("https://ex.com/a") == Fetched("https://ex.com/a", 0, None, NAVIGATED_AWAY, "https://elsewhere.com/a")


def test_same_domain_redirect_keeps_final_url():
    inner = FixtureFetcher({"https://ex.com/robots.txt": ROBOTS,
                            "https://ex.com/old": redirect("https://ex.com/old", "https://ex.com/new-slug"),
                            "https://ex.com/new-slug": page("https://ex.com/new-slug")})
    pf, _ = polite(inner)
    got = pf.fetch("https://ex.com/old")
    assert (got.status, got.final_url) == (200, "https://ex.com/new-slug")
