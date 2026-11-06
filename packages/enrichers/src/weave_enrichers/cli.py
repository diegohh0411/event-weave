"""`weave enrichers ...` commands."""
from uuid import UUID

import typer
from weave_core.db import make_engine
from weave_core.settings import Settings

app = typer.Typer(help="Enricher tools (spec 05).", no_args_is_help=True)


@app.callback()
def _group() -> None:
    """Keeps `enrichers` a command group even while it has one command."""


@app.command("report")
def report(run_id: str) -> None:
    """Coverage for a run's enrich stage: filled share per enricher, language and field, and what was discarded."""
    from .report import coverage
    engine = make_engine(Settings.from_env().database_url)
    try:
        rows = coverage(engine, UUID(run_id))
    finally:
        engine.dispose()
    head = ("enricher", "language", "field", "mentions", "filled", "share", "discarded")
    body = [(r.enricher, r.language, r.field, str(r.mentions), str(r.filled), f"{r.share:.2f}", str(r.discarded))
            for r in rows]
    widths = [max(len(row[i]) for row in [head, *body]) for i in range(len(head))]
    for row in [head, *body]:
        typer.echo("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
