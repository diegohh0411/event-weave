import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "tests"))
from fakes import SPECS  # noqa: E402
from weave_core.cache import cache_key  # noqa: E402
from weave_core.config import RunConfig  # noqa: E402
from weave_core.registry import Registry  # noqa: E402
from weave_core.runs import RunService, provide_hash  # noqa: E402
from weave_core.schema import jobs, ledger, run_deployments, stage_outputs  # noqa: E402
from weave_core.stages import Capability, JobStatus, Stage, Track, UnitKind  # noqa: E402

from fakes_inference import InfEmbedder, InfEnricher  # noqa: E402
from weave_inference.catalog import Catalog  # noqa: E402
from weave_inference.estimate import LedgerEstimator, store_readiness  # noqa: E402
from weave_inference.probe import Readiness  # noqa: E402
from weave_inference.types import Deployment, Model, Provider  # noqa: E402


def make_catalog(ready=True):
    c = Catalog(
        models={"m-s1": Model(id="m-s1", capability=Capability.system_one), "m-emb": Model(id="m-emb", capability=Capability.embeddings)},
        providers={"stub": Provider(id="stub", kind="stub", base_url="stub://")},
        deployments={"stub-s1": Deployment(id="stub-s1", model="m-s1", provider="stub"),
                     "stub-emb": Deployment(id="stub-emb", model="m-emb", provider="stub")})
    c.readiness = {"stub-s1": Readiness(True, server_version="v1", reported_model_id="x"),
                   "stub-emb": Readiness(False, reason="model missing")} if not ready else {k: Readiness(True) for k in c.deployments}
    return c


def make_registry():
    registry = Registry()
    for cls in SPECS:
        if cls.spec.stage not in (Stage.enrich, Stage.embed):
            registry.register(cls)
    registry.register(InfEnricher)
    registry.register(InfEmbedder)
    return registry


def config():
    deployments = {Stage.enrich: {Capability.system_one: "stub-s1"}, Stage.embed: {Capability.embeddings: "stub-emb"}}
    adapters = {cls.spec.stage: cls.spec.name for cls in SPECS}
    return RunConfig(name="e", track=Track.realistic, seed=1, stages={
        s: {"adapter": n, "deployments": deployments.get(s, {})} for s, n in adapters.items()})


def test_estimate_none_without_history_says_why(engine):
    est = LedgerEstimator(engine, make_registry()).estimate(config())
    assert est.amount is None and "no prior run" in est.reason
    # prior run exists but no ledger rows
    service = RunService(engine, make_registry(), make_catalog(True))
    run = service.create(config()).run_id
    service.start(run)
    from weave_core.config import validate
    est = LedgerEstimator(engine, make_registry()).estimate(validate(config(), service.registry, service.catalog))
    assert est.amount is None and est.reason == "no ledger history for fake-enricher@stub-s1"


def seed_ledger(conn, adapter, deployment, costs, status="ok"):
    for cost in costs:
        conn.execute(ledger.insert().values(run_id=None, stage=None, adapter=adapter, deployment=deployment, request_hash=uuid4().hex,
                                            cost=Decimal(cost), estimated=False, latency_ms=1, attempts=1, status=status, created_at=sa.func.now()))


def test_estimate_counts_only_uncached_units(engine):
    registry = make_registry()
    service = RunService(engine, registry, make_catalog(True))
    cfg = config()
    run = service.create(cfg).run_id
    service.start(run)
    from weave_core.config import validate
    cfg = validate(cfg, registry, service.catalog)
    enrich, embed = cfg.stages[Stage.enrich], cfg.stages[Stage.embed]
    espec, bspec = registry.get(Stage.enrich, enrich.adapter).spec, registry.get(Stage.embed, embed.adapter).spec
    with engine.begin() as conn:
        for i in range(4):
            for stage in (Stage.enrich, Stage.embed):
                conn.execute(jobs.insert().values(run_id=run, stage=stage, unit_kind=UnitKind.mention, unit_id=f"m{i}", unit_content_hash="h",
                                                  partition="p", order_key=[i], input_hash=f"in{i}", status=JobStatus.done, available_at=sa.func.now()))
        key = cache_key("in0", espec, enrich.settings, enrich.deployments)
        conn.execute(stage_outputs.insert().values(cache_key=key, stage="enrich", adapter=espec.name, adapter_version="1", output={}, emits=[], created_at=sa.func.now()))
        seed_ledger(conn, espec.name, "stub-s1", ["0.10", "0.30"])  # avg 0.20
        seed_ledger(conn, espec.name, "stub-s1", ["99"], status="error")  # ignored
        seed_ledger(conn, bspec.name, "stub-emb", ["0.01"])
    est = LedgerEstimator(engine, registry).estimate(cfg)
    assert est.reason is None
    assert est.amount == Decimal("3") * Decimal("0.20") + Decimal("4") * Decimal("0.01")


def test_start_stores_readiness_snapshot(engine):
    from functools import partial
    catalog = make_catalog(ready=True)
    service = RunService(engine, make_registry(), catalog, on_start=partial(store_readiness, catalog=catalog))
    run = service.create(config()).run_id
    service.start(run)
    with engine.connect() as conn:
        rows = {r.deployment: r for r in conn.execute(sa.select(run_deployments).where(run_deployments.c.run_id == run))}
    assert set(rows) == {"stub-s1", "stub-emb"} and all(r.ready for r in rows.values())
