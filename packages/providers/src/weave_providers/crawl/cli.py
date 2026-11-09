"""`weave crawl <outlets.yaml> [--days 7]`."""
import os
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path

import typer

from weave_core.db import make_engine
from weave_core.settings import Settings
from weave_providers.crawl.fetch import (Crawl4aiFetcher, HttpFetcher, PoliteFetcher,
    RemoteBrowserFetcher, respect_robots_from_env)
from weave_providers.crawl.public_urls import validate_public_url
from weave_providers.crawl.outlets import load_outlets
from weave_providers.crawl.store import crawl
from weave_providers.crawl.worker import run_crawl_worker


def _require_crawl_extra():
    try:
        import crawl4ai  # noqa: F401
        import defusedxml  # noqa: F401
        import feedparser  # noqa: F401
    except ImportError:
        typer.echo("weave crawl needs crawl4ai, feedparser and defusedxml: install weave-providers[crawl]", err=True)
        raise typer.Exit(1)


def crawl_command(outlets: Path, days: int = typer.Option(7, min=1)) -> None:
    _require_crawl_extra()
    engine = make_engine(Settings.from_env().database_url)
    try:
        endpoint = os.environ.get('WEAVE_CRAWL_BROWSER_URL')
        browser_context = RemoteBrowserFetcher(endpoint) if endpoint else Crawl4aiFetcher()
        with browser_context as browser, HttpFetcher(
                proxy=os.environ.get('WEAVE_CRAWL_EGRESS_PROXY')) as http:
            guard = PoliteFetcher(browser, meta=http, validator=validate_public_url,
                                  respect_robots=respect_robots_from_env())
            report = crawl(engine, load_outlets(outlets), guard, datetime.now(timezone.utc), days,
                           meta_fetcher=http)
    finally:
        engine.dispose()
    typer.echo(f"discovered={report.discovered} fetched={report.fetched} stored={report.stored} "
               f"duplicates={report.duplicates}")
    for reason, count in sorted(report.rejected.items()):
        typer.echo(f"rejected {reason}: {count}")
    for outlet, count in sorted(report.discovery_errors.items()):
        typer.echo(f"discovery errors {outlet}: {count}")


def crawl_worker_command(once: bool = typer.Option(False, help='Process at most one crawl and exit.')) -> None:
    _require_crawl_extra()
    engine = make_engine(Settings.from_env().database_url)
    stop = threading.Event()
    previous = {}
    def shutdown(signum, frame):
        stop.set()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, shutdown)
        run_crawl_worker(engine, stop, once=once)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        engine.dispose()
