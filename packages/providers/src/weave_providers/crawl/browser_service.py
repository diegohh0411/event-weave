"""Narrow browser RPC, deployed only on the isolated internal network.

No caller-supplied scripts, browser arguments, proxy settings or hooks are accepted.
Each request gets a fresh browser, and concurrent work is bounded to one crawl.
"""
import os
import signal
from dataclasses import asdict
from threading import BoundedSemaphore

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .fetch import Crawl4aiFetcher, HttpFetcher, PoliteFetcher
from .public_urls import validate_public_url

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
slot = BoundedSemaphore(1)


def validate_target(url):
    # Internal Podman networks do not forward public DNS. The egress gateway
    # owns DNS validation and pins the resulting address to the socket.
    return validate_public_url(url, resolver=lambda host, port: ['93.184.215.14'])


class FetchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=8192)
    domain: str = Field(min_length=1, max_length=253, pattern=r'^[A-Za-z0-9.-]+$')
    respect_robots: StrictBool


def kill_browsers():
    """Last resort when a browser will not close: this container serves one fetch at a
    time, so any Chromium left running belongs to the request that just finished."""
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/comm') as f:
                name = f.read().strip()
            if name.startswith(('chrome', 'chromium', 'headless_shell')):
                os.kill(int(pid), signal.SIGKILL)
        except (OSError, ValueError):
            pass


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/fetch')
def fetch(request: FetchRequest):
    proxy = os.environ.get('WEAVE_CRAWL_EGRESS_PROXY')
    if not proxy:
        raise HTTPException(503, 'isolated egress proxy is required')
    try:
        validate_target(request.url)
    except ValueError as exc:
        raise HTTPException(400, 'unsafe target') from exc
    if not slot.acquire(blocking=False):
        raise HTTPException(503, 'browser busy')
    try:
        with Crawl4aiFetcher(validator=validate_target, isolated_proxy=proxy) as browser, \
                HttpFetcher(proxy=proxy) as http:
            browser.on_close_timeout = kill_browsers
            guard = PoliteFetcher(browser, meta=http, validator=validate_target,
                                  domain=request.domain, respect_robots=request.respect_robots)
            if not guard._in_domain(request.url):
                raise HTTPException(400, 'target outside requested domain')
            result = guard.fetch(request.url)
            if result.html is not None and len(result.html.encode('utf-8')) > 2_000_000:
                raise HTTPException(502, 'rendered page too large')
            return asdict(result)
    finally:
        slot.release()
