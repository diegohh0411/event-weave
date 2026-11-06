from __future__ import annotations

import math
import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .ledger import request_hash
from .types import Deployment, Provider, ProviderError, RateLimit, RateLimited

MAX_RETRY_AFTER = 300.0


class RateLimiter:
    """Semaphore for concurrency plus a spacing-based token bucket for rps."""

    def __init__(self, rate: RateLimit, *, clock=time.monotonic, sleep=time.sleep):
        self._sem = threading.BoundedSemaphore(rate.concurrency)
        self._interval = 1.0 / rate.rps if rate.rps else 0.0
        self._next = 0.0
        self._lock = threading.Lock()
        self._clock, self._sleep = clock, sleep

    def __enter__(self):
        self._sem.acquire()
        if self._interval:
            with self._lock:
                now = self._clock()
                slot = max(now, self._next)
                self._next = slot + self._interval
            if slot > now:
                self._sleep(slot - now)
        return self

    def __exit__(self, *exc):
        self._sem.release()


_limiters: dict[str, RateLimiter] = {}
_limiters_lock = threading.Lock()


def limiter_for(deployment: Deployment, provider: Provider | None = None) -> RateLimiter:
    with _limiters_lock:
        if deployment.id not in _limiters:
            _limiters[deployment.id] = RateLimiter(deployment.rate or (provider.rate if provider else None) or RateLimit())
        return _limiters[deployment.id]


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def send_with_retries(client: httpx.Client, request: httpx.Request, limiter, *, attempts: int = 3, sleep=time.sleep) -> tuple[httpx.Response, int]:
    rng = random.Random(request_hash({"m": request.method, "u": str(request.url), "b": request.content.decode(errors="replace")}))
    last: Exception | None = None
    for n in range(1, attempts + 1):
        backoff = 0.5 * 2 ** (n - 1) * rng.uniform(0.8, 1.2)
        wait = backoff
        try:
            with limiter:
                response = client.send(request)
        except httpx.TransportError as error:
            last = ProviderError(0, f"{type(error).__name__}: {error}")
            last.retryable = True
        else:
            code = response.status_code
            if code < 400:
                return response, n
            if code == 429:
                retry_after = parse_retry_after(response.headers.get("retry-after"))
                last = RateLimited(retry_after)
                if retry_after is not None:
                    wait = retry_after
            elif code >= 500:
                last = ProviderError(code, response.text)
            else:
                error = ProviderError(code, response.text)
                error.attempts = n
                raise error
        if n < attempts:
            sleep(wait)
    assert last is not None
    last.attempts = attempts
    raise last
