"""`weave crawl <outlets.yaml> [--days 7]`."""
from datetime import datetime, timezone
from pathlib import Path

import typer

from weave_core.db import make_engine
from weave_core.settings import Settings
from weave_providers.crawl.fetch import Crawl4aiFetcher
from weave_providers.crawl.outlets import load_outlets
from weave_providers.crawl.store import crawl


def crawl_command(outlets: Path, days: int = typer.Option(7, min=1)) -> None:
    engine = make_engine(Settings.from_env().database_url)
    try:
        with Crawl4aiFetcher() as fetcher:
            report = crawl(engine, load_outlets(outlets), fetcher, datetime.now(timezone.utc), days)
    finally:
        engine.dispose()
    typer.echo(f"discovered={report.discovered} fetched={report.fetched} stored={report.stored} "
               f"duplicates={report.duplicates}")
    for reason, count in sorted(report.rejected.items()):
        typer.echo(f"rejected {reason}: {count}")
