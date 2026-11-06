import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from weave_inference.http import RateLimiter, limiter_for, parse_retry_after, send_with_retries
from weave_inference.types import Deployment, ProviderError, RateLimit, RateLimited


def client(*responses):
    queue = list(responses)
    calls = []

    def handler(request):
        calls.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    c = httpx.Client(transport=httpx.MockTransport(handler))
    c.calls = calls
    return c


def req():
    return httpx.Request("POST", "http://x/v1", json={"a": 1})


def lim():
    return RateLimiter(RateLimit(concurrency=4))


def ok():
    return httpx.Response(200, json={})


def test_429_retry_after_seconds_is_honoured():
    slept = []
    resp, attempts = send_with_retries(client(httpx.Response(429, headers={"retry-after": "7"}), ok()), req(), lim(), sleep=slept.append)
    assert slept == [7.0] and attempts == 2 and resp.status_code == 200


def test_retry_after_http_date_parsed_and_capped_at_300():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert parse_retry_after(format_datetime(now + timedelta(seconds=30), usegmt=True), now=now) == 30.0
    assert parse_retry_after(format_datetime(now + timedelta(hours=1), usegmt=True), now=now) == 300.0
    assert parse_retry_after("9999", now=now) == 300.0
    assert parse_retry_after(format_datetime(now - timedelta(hours=1), usegmt=True), now=now) == 0.0
    assert parse_retry_after("garbage", now=now) is None
    slept = []
    future = format_datetime(datetime.now(timezone.utc) + timedelta(hours=1), usegmt=True)
    send_with_retries(client(httpx.Response(429, headers={"retry-after": future}), ok()), req(), lim(), sleep=slept.append)
    assert slept == [300.0]


def test_429_exhausted_raises_rate_limited():
    r = lambda: httpx.Response(429, headers={"retry-after": "1"})
    with pytest.raises(RateLimited) as e:
        send_with_retries(client(r(), r(), r()), req(), lim(), sleep=lambda s: None)
    assert e.value.retry_after == 1.0


def test_5xx_retries_with_backoff_then_raises_provider_error():
    slept = []
    c = client(*[httpx.Response(503, text="down") for _ in range(3)])
    with pytest.raises(ProviderError) as e:
        send_with_retries(c, req(), lim(), sleep=slept.append)
    assert len(c.calls) == 3 and len(slept) == 2
    assert 0.4 <= slept[0] <= 0.6 and 0.8 <= slept[1] <= 1.2 and slept[1] > slept[0]
    assert e.value.status == 503 and e.value.body == "down"
    # deterministic jitter
    again = []
    with pytest.raises(ProviderError):
        send_with_retries(client(*[httpx.Response(503) for _ in range(3)]), req(), lim(), sleep=again.append)
    assert again == slept


def test_timeout_retries_then_succeeds():
    slept = []
    resp, attempts = send_with_retries(client(httpx.ReadTimeout("t"), httpx.ConnectError("c"), ok()), req(), lim(), sleep=slept.append)
    assert attempts == 3 and len(slept) == 2


def test_400_is_not_retried():
    c = client(httpx.Response(400, text="bad"), ok())
    with pytest.raises(ProviderError) as e:
        send_with_retries(c, req(), lim(), sleep=lambda s: None)
    assert len(c.calls) == 1 and e.value.status == 400 and e.value.retryable is False


def test_rate_limiter_caps_concurrency():
    limiter = RateLimiter(RateLimit(concurrency=2))
    state = {"now": 0, "max": 0}
    guard = threading.Lock()

    def work():
        with limiter:
            with guard:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            time.sleep(0.05)
            with guard:
                state["now"] -= 1

    threads = [threading.Thread(target=work) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert state["max"] == 2


def test_rps_spaces_calls():
    t = {"v": 0.0}
    slept = []
    limiter = RateLimiter(RateLimit(concurrency=4, rps=2), clock=lambda: t["v"], sleep=lambda s: (slept.append(s), t.__setitem__("v", t["v"] + s)))
    for _ in range(3):
        with limiter:
            pass
    assert slept == [0.5, 0.5]


def test_limiter_for_is_per_deployment_and_process_wide():
    a = Deployment(id="a1", model="m", provider="p")
    b = Deployment(id="b1", model="m", provider="p", rate=RateLimit(concurrency=1))
    assert limiter_for(a) is limiter_for(a)
    assert limiter_for(a) is not limiter_for(b)


def test_other_transport_errors_retry_then_typed_provider_error():
    c = client(httpx.ReadError("boom"), httpx.RemoteProtocolError("bad"), httpx.ReadError("boom"))
    with pytest.raises(ProviderError) as e:
        send_with_retries(c, req(), lim(), sleep=lambda s: None)
    assert len(c.calls) == 3 and e.value.retryable is True


def test_retry_after_nan_is_ignored():
    assert parse_retry_after("nan") is None
    assert parse_retry_after("inf") is None
