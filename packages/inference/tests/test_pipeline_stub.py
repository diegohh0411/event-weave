import socket
import sys
import threading
from pathlib import Path

import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "tests"))
from fakes import SPECS  # noqa: E402
from weave_core.config import RunConfig  # noqa: E402
from weave_core.registry import Registry  # noqa: E402
from weave_core.runs import RunService  # noqa: E402
from weave_core.schema import ledger  # noqa: E402
from weave_core.stages import Capability, JobStatus, RunState, Stage, Track  # noqa: E402
from weave_core.worker import run_worker  # noqa: E402
from weave_core.writer import run_writer  # noqa: E402

from fakes_inference import InfEmbedder, InfEnricher  # noqa: E402
from weave_inference.catalog import Catalog  # noqa: E402
from weave_inference.gateway import Inference  # noqa: E402
from weave_inference.probe import Readiness  # noqa: E402
from weave_inference.types import Deployment, Model, Provider  # noqa: E402

PARALLEL = [Stage.provide, Stage.extract, Stage.tag, Stage.enrich, Stage.embed]


@pytest.fixture
def no_network(monkeypatch):
    real = socket.socket.connect

    def guarded(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and tuple(address[:2]) != ("127.0.0.1", 5432):
            raise AssertionError(f"network access attempted: {address}")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def setup(engine, no_network):
    catalog = Catalog(
        models={"m-s1": Model(id="m-s1", capability=Capability.system_one),
                "m-emb": Model(id="m-emb", capability=Capability.embeddings)},
        providers={"stub": Provider(id="stub", kind="stub", base_url="stub://")},
        deployments={"stub-s1": Deployment(id="stub-s1", model="m-s1", provider="stub"),
                     "stub-emb": Deployment(id="stub-emb", model="m-emb", provider="stub")})
    catalog.readiness = {k: Readiness(ready=True) for k in catalog.deployments}
    Inference.configure(catalog, engine)
    registry = Registry()
    for cls in SPECS:
        if cls.spec.stage not in (Stage.enrich, Stage.embed):
            registry.register(cls)
    registry.register(InfEnricher)
    registry.register(InfEmbedder)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE fake_cluster_log (seq bigserial PRIMARY KEY, run_id uuid NOT NULL, "
                             "partition text NOT NULL, unit_id text NOT NULL, cluster integer NOT NULL)")
    InfEnricher.calls = InfEmbedder.calls = 0
    return RunService(engine, registry, catalog), registry


def config():
    deployments = {Stage.enrich: {Capability.system_one: "stub-s1"}, Stage.embed: {Capability.embeddings: "stub-emb"}}
    adapters = {cls.spec.stage: cls.spec.name for cls in SPECS}
    return RunConfig(name="e2e", track=Track.realistic, seed=7, stages={
        stage: {"adapter": name, "deployments": deployments.get(stage, {})} for stage, name in adapters.items()})


def drive(engine, registry):
    for _ in range(10):
        for st in PARALLEL:
            run_worker(engine, registry, st, "w", stop=threading.Event(), once=True)
    run_writer(engine, registry, Stage.cluster, "w", stop=threading.Event(), once=True)


def ledger_count(engine, run=None):
    with engine.connect() as conn:
        q = sa.select(sa.func.count()).select_from(ledger)
        return conn.scalar(q.where(ledger.c.run_id == run) if run else q)


def test_full_pipeline_with_stub_no_network(engine, setup):
    service, registry = setup
    run = service.create(config()).run_id
    service.start(run)
    drive(engine, registry)
    status = service.status(run)
    assert status.state == RunState.done
    mentions = status.stages[Stage.embed][JobStatus.done]
    assert mentions == 6 and ledger_count(engine, run) == mentions * 2
    with engine.connect() as conn:
        assert set(conn.scalars(sa.select(ledger.c.status))) == {"ok"}


def test_second_run_reuses_cache_and_adds_no_ledger_rows(engine, setup):   # Review Focus 3
    service, registry = setup
    first = service.create(config()).run_id
    service.start(first)
    drive(engine, registry)
    rows_before, calls_before = ledger_count(engine), (InfEnricher.calls, InfEmbedder.calls)
    other = config()
    other.stages[Stage.cluster].settings = {"window": 9}
    second = service.create(other).run_id
    service.start(second)
    drive(engine, registry)
    assert service.status(second).stages[Stage.embed] == {JobStatus.done: 6}
    assert (InfEnricher.calls, InfEmbedder.calls) == calls_before
    assert ledger_count(engine) == rows_before and ledger_count(engine, second) == 0
