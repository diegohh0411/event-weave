from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from weave_core.cache import cache_key
from weave_core.config import RunConfig
from weave_core.runs import Estimate, provide_hash
from weave_core.schema import jobs, ledger, run_deployments, runs, stage_outputs
from weave_core.stages import PIPELINE


class LedgerEstimator:
    """Estimate = sum over stages of (uncached jobs of the last run with the same provider) x (average ledger cost per call)."""

    def __init__(self, engine, registry):
        self.engine, self.registry = engine, registry

    def _average(self, conn, stage, adapter: str, deployment: str) -> Decimal | None:
        """Cost per job: ok ledger cost of runs that used (adapter, deployment) / done jobs of the stage in those runs."""
        match = (ledger.c.adapter == adapter, ledger.c.deployment == deployment, ledger.c.status == 'ok', ledger.c.run_id.is_not(None))
        total = conn.execute(sa.select(sa.func.sum(ledger.c.cost)).where(*match)).scalar()
        if total is None:
            return None
        run_ids = sa.select(ledger.c.run_id).where(*match)
        count = conn.execute(sa.select(sa.func.count()).where(
            jobs.c.stage == stage, jobs.c.status == 'done', jobs.c.run_id.in_(run_ids))).scalar()
        return Decimal(total) / count if count else None

    def estimate(self, cfg: RunConfig) -> Estimate:
        with self.engine.connect() as conn:
            prior = _prior_run(conn, cfg)
            if prior is None:
                return Estimate(None, 'no prior run with the same provider')
            amount = Decimal(0)
            for stage in PIPELINE:
                choice = cfg.stages.get(stage)
                if choice is None or not choice.deployments:
                    continue
                avgs = []
                for deployment in sorted(choice.deployments.values()):
                    avg = self._average(conn, stage, choice.adapter, deployment)
                    if avg is None:
                        return Estimate(None, f'no ledger history for {choice.adapter}@{deployment}')
                    avgs.append(avg)
                keys = _stage_keys(conn, self.registry, prior, stage, choice)
                if not keys:
                    return Estimate(None, f'prior run has no {stage} jobs')
                amount += (len(keys) - _cached_count(conn, keys)) * sum(avgs)
        return Estimate(amount)


def _prior_run(conn, cfg: RunConfig):
    """The most recent run that used the same provider: its jobs are the best guide to this run's units."""
    return conn.execute(sa.select(runs.c.id).join(jobs, jobs.c.run_id == runs.c.id).where(
        jobs.c.stage == 'provide', jobs.c.input_hash == provide_hash(cfg)).order_by(runs.c.created_at.desc()).limit(1)).scalar()


def _stage_keys(conn, registry, prior, stage, choice) -> list[str]:
    """Cache key of each unit the prior run processed in this stage, under this run's adapter, settings and deployments."""
    spec = registry.get(stage, choice.adapter).spec
    hashes = conn.scalars(sa.select(jobs.c.input_hash).where(jobs.c.run_id == prior, jobs.c.stage == stage)).all()
    return [cache_key(h, spec, choice.settings, choice.deployments) for h in hashes]


def _cached_count(conn, keys: list[str]) -> int:
    cached = set(conn.scalars(sa.select(stage_outputs.c.cache_key).where(stage_outputs.c.cache_key.in_(keys))))
    return sum(1 for k in keys if k in cached)


def cache_coverage(engine, registry, cfg: RunConfig) -> dict[Stage, tuple[int, int]] | None:
    """(cached, total) units per cacheable stage, from the last run with the same provider; None when there is none."""
    with engine.connect() as conn:
        prior = _prior_run(conn, cfg)
        if prior is None:
            return None
        coverage = {}
        for stage in PIPELINE:
            choice = cfg.stages.get(stage)
            if choice is None or not registry.get(stage, choice.adapter).spec.cacheable:
                continue
            keys = _stage_keys(conn, registry, prior, stage, choice)
            if keys:
                coverage[stage] = (_cached_count(conn, keys), len(keys))
        return coverage


def store_readiness(session, run_id, cfg: RunConfig, *, catalog) -> None:
    now = datetime.now(timezone.utc)
    names = sorted({d for choice in cfg.stages.values() for d in choice.deployments.values()})
    for name in names:
        r = catalog.readiness.get(name)
        session.execute(run_deployments.insert().values(
            run_id=run_id, deployment=name, ready=bool(r and r.ready), reason=(r.reason if r else 'not probed'),
            server_version=r.server_version if r else None, reported_model_id=r.reported_model_id if r else None, probed_at=now))
