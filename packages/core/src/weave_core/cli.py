import signal
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import typer
import yaml

from .config import ConfigError, RunConfig
from .db import migrate
from .runs import RunStateError
from .service import build_service
from .settings import Settings
from .stages import JobStatus, ORDERED_STAGES, PIPELINE, Stage
from .worker import run_worker
from .writer import run_writer

app = typer.Typer()
db_app = typer.Typer()
run_app = typer.Typer()
app.add_typer(db_app, name='db')
app.add_typer(run_app, name='run')


def _inference():
    try:
        from weave_inference.catalog import CatalogError, load_catalog
        from weave_inference.cli import inference_app
        from weave_inference.estimate import LedgerEstimator, store_readiness
        from weave_inference.gateway import Inference
        from weave_inference.probe import probe
    except ImportError:
        return None
    return SimpleNamespace(CatalogError=CatalogError, load_catalog=load_catalog, inference_app=inference_app, LedgerEstimator=LedgerEstimator,
                           store_readiness=store_readiness, Inference=Inference, probe=probe)


_inf = _inference()
if _inf is not None:
    app.add_typer(_inf.inference_app, name='inference')


def _crawl():
    try:
        from weave_providers.crawl.cli import crawl_command, crawl_worker_command
    except ImportError:  # weave-providers or its `crawl` extra is not installed
        return None
    return crawl_command, crawl_worker_command


_crawl_commands = _crawl()
if _crawl_commands is not None:
    app.command('crawl')(_crawl_commands[0])
    app.command('crawl-worker')(_crawl_commands[1])


def _eval():
    try:
        from weave_eval.cli import eval_command
    except ImportError:  # weave-eval is not installed
        return None
    return eval_command


_eval_command = _eval()
if _eval_command is not None:
    app.command('eval')(_eval_command)


def _extractors():
    try:
        from weave_extractors.cli import app as extractors_app
    except ImportError:  # weave-extractors is not installed
        return None
    return extractors_app


_extractors_app = _extractors()
if _extractors_app is not None:
    app.add_typer(_extractors_app, name='extractors')


def _enrichers():
    try:
        from weave_enrichers.cli import app as enrichers_app
    except ImportError:  # weave-enrichers is not installed
        return None
    return enrichers_app


_enrichers_app = _enrichers()
if _enrichers_app is not None:
    app.add_typer(_enrichers_app, name='enrichers')


def _clusterer():
    try:
        from weave_clusterer.cli import app as clusterer_app
    except ImportError:  # weave-clusterer is not installed
        return None
    return clusterer_app


_clusterer_app = _clusterer()
if _clusterer_app is not None:
    app.add_typer(_clusterer_app, name='clusterer')


def _build(probe: bool = True):
    try:
        from weave_inference.catalog import CatalogError
    except ImportError:  # weave-inference is not installed; build_service cannot raise it
        return build_service(probe)
    try:
        return build_service(probe)
    except CatalogError as exc:
        for problem in exc.problems:
            typer.echo(problem, err=True)
        raise typer.Exit(2)


@db_app.command('migrate')
def db_migrate():
    typer.echo(f'migrated to {migrate(Settings.from_env().database_url)}')


@run_app.command('create')
def create(config: Path):
    service = _build()
    try:
        created = service.create(RunConfig.model_validate(yaml.safe_load(config.read_text())))
    except ConfigError as exc:
        for problem in exc.problems:
            typer.echo(problem, err=True)
        raise typer.Exit(2)
    finally:
        service.engine.dispose()
    typer.echo(f'run {created.run_id}')
    typer.echo(f'hash {created.config_hash}')
    est = created.estimate
    typer.echo(f'estimate: none yet ({est.reason})' if est.amount is None else f'estimate ~${est.amount:.4f} (from ledger averages)')


def _transition(action: str, run_id: UUID):
    service = _build(probe=action == "start")
    try:
        getattr(service, action)(run_id)
    except RunStateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    finally:
        service.engine.dispose()


@run_app.command('start')
def start(run_id: UUID):
    _transition('start', run_id)


@run_app.command('cancel')
def cancel(run_id: UUID):
    _transition('cancel', run_id)


@run_app.command('resume')
def resume(run_id: UUID):
    _transition('resume', run_id)


@run_app.command('status')
def status(run_id: UUID):
    service = _build(probe=False)
    try:
        result = service.status(run_id)
    finally:
        service.engine.dispose()
    typer.echo(result.state)
    for stage in PIPELINE:
        counts = result.stages.get(stage, {})
        typer.echo(f'{stage}: ' + ' '.join(f'{s}={counts.get(s, 0)}' for s in JobStatus))


@app.command('worker')
def worker(stage: Stage = typer.Option(...), threads: int = typer.Option(8, min=1)):
    service = _build()
    stop = threading.Event()
    previous = {}
    def shutdown(signum, frame):
        stop.set()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, shutdown)
        runner = run_writer if stage in ORDERED_STAGES else run_worker
        runner(service.engine, service.registry, stage, str(uuid4()), threads=threads, stop=stop)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        service.engine.dispose()
