from weave_providers.crawl.fetch import Fetched, FixtureFetcher, PoliteFetcher, USER_AGENT


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
