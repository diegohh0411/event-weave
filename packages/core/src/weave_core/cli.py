import os
import signal
import threading
from pathlib import Path
from uuid import UUID, uuid4

import typer
import yaml

from .config import ConfigError, RunConfig, StaticCatalog
from .db import make_engine, migrate
from .registry import Registry
from .runs import RunService, RunStateError
from .settings import Settings
from .stages import Capability, JobStatus, ORDERED_STAGES, PIPELINE, Stage
from .worker import run_worker
from .writer import run_writer

app = typer.Typer()
db_app = typer.Typer()
run_app = typer.Typer()
app.add_typer(db_app, name='db')
app.add_typer(run_app, name='run')


def _service():
    settings = Settings.from_env()
    registry = Registry()
    registry.load_entry_points()
    path = os.environ.get('WEAVE_DEPLOYMENTS_FILE')
    data = yaml.safe_load(Path(path).read_text()) if path else {}
    catalog = StaticCatalog({key: (Capability(value[0]), value[1]) for key, value in (data or {}).items()})
    return RunService(make_engine(settings.database_url), registry, catalog)


@db_app.command('migrate')
def db_migrate():
    typer.echo(f'migrated to {migrate(Settings.from_env().database_url)}')


@run_app.command('create')
def create(config: Path):
    service = _service()
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
    typer.echo('estimate: none yet' if created.estimate is None else f'estimate ${created.estimate}')


def _transition(action: str, run_id: UUID):
    service = _service()
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
    service = _service()
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
    service = _service()
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
