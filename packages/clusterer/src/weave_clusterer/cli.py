"""`weave clusterer ...` commands."""
from uuid import UUID

import typer
from weave_core.db import make_engine
from weave_core.settings import Settings

app = typer.Typer(help="Clusterer tools (spec 06).", no_args_is_help=True)


@app.callback()
def _group() -> None:
    """Keeps `clusterer` a command group even while it has one command."""


@app.command("report")
def report(run_id: str, k: int = typer.Option(None, "--k", help="Re-slice recorded candidates to the top N.")) -> None:
    """Counts and outcomes of a run's cluster stage, and retrieval recall at K per language."""
    from .report import recall_at_k, summary
    engine = make_engine(Settings.from_env().database_url)
    try:
        s = summary(engine, UUID(run_id))
        rows = recall_at_k(engine, UUID(run_id), k)
    finally:
        engine.dispose()
    typer.echo(f"mentions {s.mentions}  clustered {s.clustered}  failed {s.failed}  clusters {s.clusters}  "
               f"singletons {s.singletons}  decision calls {s.decision_calls}")
    for reason, n in s.outcomes.items():
        typer.echo(f"  {reason}: {n}")
    head = ("language", "with_antecedent", "hits", f"recall@{k if k else 'K'}")
    body = [(r.language, str(r.mentions_with_gold_antecedent), str(r.hits), f"{r.recall:.3f}") for r in rows]
    widths = [max(len(row[i]) for row in [head, *body]) for i in range(len(head))]
    for row in [head, *body]:
        typer.echo("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
