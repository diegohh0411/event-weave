import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import sqlalchemy as sa

from . import queue as queue_module
from .adapters import AdapterContext, AdapterFailure
from .cache import cache_key, lookup
from .config import RunConfig, version_mismatch
from .db import session_scope
from .queue import Queue
from .registry import Registry
from .schema import runs
from .stages import ORDERED_STAGES, Stage


logger = logging.getLogger(__name__)


def build_adapter(session, run_id, stage, registry):
    """Build an adapter and its choice from the run's frozen configuration."""
    cfg = RunConfig.model_validate(session.scalar(
        sa.select(runs.c.config).where(runs.c.id == run_id)))
    choice = cfg.stages[stage]
    cls = registry.get(stage, choice.adapter)
    if choice.version is not None and choice.version != cls.spec.version:
        raise AdapterFailure(version_mismatch(choice, cls.spec.version), retryable=False)
    if cls.spec.max_concurrency < 1:
        raise ValueError("max_concurrency must be positive")
    adapter = cls(cls.spec.settings_model.model_validate(choice.settings),
                  AdapterContext(run_id, cfg.seed, choice.deployments, stage, choice.adapter))
    return adapter, choice


def run_worker(engine: sa.Engine, registry: Registry, stage: Stage, worker_id: str, *,
               threads: int = 8, poll_interval: float = 1.0,
               stop: threading.Event, once: bool = False) -> None:
    if stage in ORDERED_STAGES:
        raise ValueError("ordered stages require a writer")
    if threads < 1:
        raise ValueError("threads must be positive")
    queue = Queue(engine)
    adapters = {}
    closing = threading.Event()

    health = threading.Condition()
    heartbeat_error = None
    terminal_error = None

    def heartbeat():
        nonlocal heartbeat_error, terminal_error
        failures = 0
        while not closing.wait(queue_module.HEARTBEAT_EVERY.total_seconds()):
            try:
                queue.heartbeat(worker_id)
            except Exception as exc:
                failures += 1
                with health:
                    heartbeat_error = exc
                    if failures >= 3:
                        terminal_error = exc
                    health.notify_all()
                logger.exception("Worker %s heartbeat failed (attempt %s/3)",
                                 worker_id, failures)
                if failures >= 3:
                    return
            else:
                failures = 0
                with health:
                    heartbeat_error = None
                    health.notify_all()

    def execute(job, entry):
        adapter, choice, semaphore = entry
        try:
            key, result = None, None
            if adapter.spec.cacheable:
                key = cache_key(job.input_hash, adapter.spec, choice.settings, choice.deployments)
                with session_scope(engine) as session:
                    result = lookup(session, key)
            if result is None:
                with semaphore:
                    result = adapter.process(job.unit, queue.upstream(job))
            queue.complete(job, result, key, spec=adapter.spec)
        except AdapterFailure as exc:
            queue.fail(job, exc)
        except Exception as exc:
            queue.fail(job, AdapterFailure(f"{type(exc).__name__}: {exc}"))

    heart = threading.Thread(target=heartbeat, daemon=True)
    heart.start()
    try:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            while not stop.is_set():
                with health:
                    while heartbeat_error is not None and terminal_error is None and not stop.is_set():
                        health.wait(timeout=0.1)
                    if terminal_error is not None:
                        raise RuntimeError("worker heartbeat failed after 3 attempts") from terminal_error
                    if stop.is_set():
                        break
                    queue.release_stale()
                    jobs = queue.claim(stage, threads, worker_id)
                if not jobs:
                    if once:
                        break
                    stop.wait(poll_interval)
                    continue
                futures = []
                for job in jobs:
                    try:
                        if job.run_id not in adapters:
                            with session_scope(engine) as session:
                                adapter, choice = build_adapter(session, job.run_id, stage, registry)
                            adapters[job.run_id] = (
                                adapter, choice, threading.Semaphore(adapter.spec.max_concurrency))
                        futures.append(pool.submit(execute, job, adapters[job.run_id]))
                    except AdapterFailure as exc:
                        queue.fail(job, exc)
                    except Exception as exc:
                        queue.fail(job, AdapterFailure(f"{type(exc).__name__}: {exc}"))
                for future in futures:
                    future.result()
    finally:
        closing.set()
        heart.join()
    # Also surface failures that arrived during the final batch or shutdown.
    if terminal_error is not None:
        raise RuntimeError("worker heartbeat failed after 3 attempts") from terminal_error
