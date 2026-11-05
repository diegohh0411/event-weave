from __future__ import annotations

import os
from pathlib import Path

import typer
from weave_core.adapters import AdapterContext
from weave_core.db import make_engine
from weave_core.schema import ledger
from weave_core.settings import Settings
from weave_core.stages import Capability

from .catalog import CatalogError, load_catalog
from .gateway import Inference
from .probe import probe
from .types import (ChatRequest, Choice, EmbeddingsRequest, ExtractionRequest, SystemOneRequest)

inference_app = typer.Typer(help='Inference deployments: readiness and smoke tests.')


def _load():
    path = os.environ.get('WEAVE_INFERENCE_FILE')
    if not path:
        typer.echo('WEAVE_INFERENCE_FILE is not set', err=True)
        raise typer.Exit(2)
    try:
        return load_catalog(Path(path))
    except CatalogError as exc:
        for problem in exc.problems:
            typer.echo(problem, err=True)
        raise typer.Exit(2)


def _request(capability: Capability):
    if capability == Capability.system_one:
        return SystemOneRequest(state='smoke test', questions=[Choice(id='q', prompt='Is this a test?', options=['yes', 'no'])])
    if capability == Capability.llm:
        return ChatRequest(messages=[{'role': 'user', 'content': 'Reply with the word ok.'}], max_tokens=8)
    if capability == Capability.embeddings:
        return EmbeddingsRequest(texts=['smoke test'])
    return ExtractionRequest(text='Smoke test.', fields={'word': 'a word'})


@inference_app.command('probe')
def probe_command():
    catalog = _load()
    probe(catalog)
    for dep_id in catalog.deployments:
        r = catalog.readiness[dep_id]
        typer.echo(f'{dep_id}\t{catalog.capability_of(dep_id).value}\t{r.render()}')


@inference_app.command('smoke')
def smoke(deployment: str):
    catalog = _load()
    if deployment not in catalog.deployments:
        typer.echo(f"unknown deployment '{deployment}'", err=True)
        raise typer.Exit(1)
    probe(catalog)
    capability = catalog.capability_of(deployment)
    engine = make_engine(Settings.from_env().database_url)
    try:
        Inference.configure(catalog, engine)
        gateway = Inference.for_context(AdapterContext(None, 0, {capability: deployment}, None, 'smoke'))
        call = {Capability.system_one: gateway.system_one, Capability.llm: gateway.chat,
                Capability.embeddings: gateway.embed, Capability.extraction: gateway.extract}[capability]
        try:
            result = call(_request(capability))
        except Exception as exc:
            typer.echo(f'error: {exc}', err=True)
            raise typer.Exit(1)
        import sqlalchemy as sa
        with engine.connect() as conn:
            row = conn.execute(sa.select(ledger).where(ledger.c.run_id.is_(None), ledger.c.adapter == 'smoke',
                                                       ledger.c.deployment == deployment).order_by(ledger.c.id.desc()).limit(1)).first()
        typer.echo(f'{deployment} ok: {str(result.value)[:200]}')
        typer.echo(f'cost ${row.cost} latency {row.latency_ms}ms' + (' (estimated)' if row.estimated else ''))
    finally:
        engine.dispose()
