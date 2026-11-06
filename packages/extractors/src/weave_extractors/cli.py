"""`weave extractors ...` commands."""
from pathlib import Path

import typer
from weave_core.db import make_engine
from weave_core.settings import Settings

from .event_nouns import build_event_nouns
from .stanza_client import HttpStanzaClient

app = typer.Typer(help="Mention extractor tools (spec 04).", no_args_is_help=True)


@app.callback()
def _group() -> None:
    """Keeps `extractors` a command group even while it has one command."""


DEFAULT_OUT = Path(__file__).parent / "data" / "event_nouns"


@app.command("event-nouns")
def event_nouns(stanza_url: str = typer.Option(None, help="Defaults to WEAVE_STANZA_URL."),
                out: Path = typer.Option(DEFAULT_OUT, help="Directory for <lang>.txt files.")) -> None:
    """Build event-noun lemma lists from MCECR train-split gold triggers already in the database."""
    engine = make_engine(Settings.from_env().database_url)
    try:
        lists = build_event_nouns(engine, HttpStanzaClient(stanza_url))
    finally:
        engine.dispose()
    out.mkdir(parents=True, exist_ok=True)
    for lang, lemmas in sorted(lists.items()):
        (out / f"{lang}.txt").write_text("".join(f"{lemma}\n" for lemma in lemmas), encoding="utf-8")
        typer.echo(f"{lang}: {len(lemmas)}")


@app.command("report")
def report(run_id: str) -> None:
    """Counts for a run's extract stage: documents, mentions, and everything dropped."""
    from uuid import UUID

    from .report import extract_report
    engine = make_engine(Settings.from_env().database_url)
    try:
        counts = extract_report(engine, UUID(run_id))
    finally:
        engine.dispose()
    width = max(len(k) for k in counts)
    for key, value in counts.items():
        typer.echo(f"{key:<{width}}  {value}")
