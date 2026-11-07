"""`weave eval <run>`."""
from __future__ import annotations

import os
from uuid import UUID

import typer


def eval_command(
    run_id: UUID,
    split: str = typer.Option("test", "--split", help="Gold split to score (tune on dev, report on test)."),
    resamples: int = typer.Option(10_000, "--resamples", min=1, help="Bootstrap resamples."),
    workers: int = typer.Option(None, "--workers", min=1, help="Bootstrap processes (default: all cores)."),
) -> None:
    """Score a finished run against MCECR gold, store the scores and print the report."""
    from weave_core.db import make_engine
    from weave_core.settings import Settings

    from weave_eval.evaluate import Refused, evaluate
    from weave_eval.paper import load_paper
    from weave_eval.render import render
    from weave_eval.store import save

    engine = make_engine(Settings.from_env().database_url)
    try:
        try:
            ev = evaluate(engine, run_id, split=split, resamples=resamples, workers=workers or os.cpu_count())
        except Refused as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)
        evaluation_id = save(engine, ev)
    finally:
        engine.dispose()
    typer.echo(render(ev, load_paper()), nl=False)
    typer.echo(f"evaluation {evaluation_id}")
