"""MCECR provide stage through the real core worker and Postgres, then a fake extract."""
import json
import shutil
import threading
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import BaseModel

from weave_core.adapters import AdapterSpec, StageResult, UnitRef
from weave_core.config import RunConfig, StaticCatalog
from weave_core.db import make_engine, migrate
from weave_core.registry import Registry
from weave_core.runs import RunService
from weave_core.schema import documents, gold_documents, gold_mentions, gold_pairs, jobs
from weave_core.stages import JobStatus, Stage, UnitKind
from weave_core.worker import run_worker
from weave_providers.mcecr.adapter import McecrProvider

FIX = Path(__file__).parent / "fixtures" / "mcecr"


class Empty(BaseModel):
    pass


def fake(stage, name, kind):
    class Fake:
        spec = AdapterSpec(name, "1", stage, kind, Empty)

        def __init__(self, settings, ctx):
            pass

        def process(self, unit, upstream):
            if stage == Stage.extract:
                return StageResult({}, [UnitRef(UnitKind.mention, f"{unit.id}#0", unit.content_hash + "0")])
            return StageResult({})
    return Fake


def test_mcecr_run_provides_documents_gold_and_feeds_extract(database_url):
    registry = Registry()
    registry.register(McecrProvider)
    for stage, name, kind in [(Stage.extract, "x", UnitKind.document), (Stage.enrich, "e", UnitKind.mention),
                              (Stage.embed, "m", UnitKind.mention), (Stage.cluster, "c", UnitKind.mention)]:
        registry.register(fake(stage, name, kind))
    cfg = RunConfig(name="t", track="paper", seed=1, stages={
        Stage.provide: {"adapter": "mcecr", "settings": {"path": str(FIX), "splits": ["test", "train"]}},
        Stage.extract: {"adapter": "x"}, Stage.enrich: {"adapter": "e"},
        Stage.embed: {"adapter": "m"}, Stage.cluster: {"adapter": "c"}})
    engine = make_engine(database_url)
    service = RunService(engine, registry, StaticCatalog())
    run = service.create(cfg).run_id
    service.start(run)
    for stage in (Stage.provide, Stage.extract):
        run_worker(engine, registry, stage, "w", stop=threading.Event(), once=True)
    with engine.connect() as c:
        docs = c.execute(sa.select(documents)).mappings().all()
        assert len(docs) == 2 and all(d["provider"] == "mcecr" for d in docs)
        assert c.execute(sa.select(sa.func.count()).select_from(gold_documents)).scalar() == 2
        assert c.execute(sa.select(sa.func.count()).select_from(gold_mentions)).scalar() > 0
        assert c.execute(sa.select(sa.func.count()).select_from(gold_pairs)).scalar() > 0
        parts = {r[0] for r in c.execute(sa.select(jobs.c.partition).where(jobs.c.stage == Stage.extract))}
        assert parts == {"en/t1"}
        done = c.execute(sa.select(sa.func.count()).select_from(jobs).where(
            jobs.c.stage == Stage.extract, jobs.c.status == JobStatus.done)).scalar()
        assert done == 2
    engine.dispose()


def _many_topics(tmp_path):
    """The fixture dataset with six test topics, so the seeded topic shuffle has room to differ."""
    root = tmp_path / "mcecr"
    shutil.copytree(FIX / "english" / "events", root / "english" / "events")
    (root / "english" / "withinDoc").mkdir(parents=True)
    events = root / "english" / "events"
    source = (events / "t1.json").read_bytes()
    for n in range(1, 7):
        (events / f"t{n}.json").write_bytes(source)
    pair = json.loads((FIX / "english" / "withinDoc" / "train.json").read_text())[0]
    (root / "english" / "withinDoc" / "test.json").write_text(
        json.dumps([{**pair, "topic_id": f"t{n}"} for n in range(1, 7)]))
    return root


def test_different_seeds_give_different_order_despite_identical_settings(database_url, tmp_path):
    data = _many_topics(tmp_path)
    registry = Registry()
    registry.register(McecrProvider)
    for stage, name, kind in [(Stage.extract, "x", UnitKind.document), (Stage.enrich, "e", UnitKind.mention),
                              (Stage.embed, "m", UnitKind.mention), (Stage.cluster, "c", UnitKind.mention)]:
        registry.register(fake(stage, name, kind))
    engine = make_engine(database_url)
    service = RunService(engine, registry, StaticCatalog())
    orders = {}
    for seed in (1, 2):
        cfg = RunConfig(name=f"s{seed}", track="paper", seed=seed, stages={
            Stage.provide: {"adapter": "mcecr", "settings": {"path": str(data), "splits": ["test"]}},
            Stage.extract: {"adapter": "x"}, Stage.enrich: {"adapter": "e"},
            Stage.embed: {"adapter": "m"}, Stage.cluster: {"adapter": "c"}})
        run = service.create(cfg).run_id
        service.start(run)
        run_worker(engine, registry, Stage.provide, "w", stop=threading.Event(), once=True)
        with engine.connect() as c:
            rows = c.execute(sa.select(jobs.c.unit_id, jobs.c.order_key).where(
                jobs.c.run_id == run, jobs.c.stage == Stage.extract)).all()
        orders[seed] = [r[0] for r in sorted(rows, key=lambda r: tuple(r[1]))]
    engine.dispose()
    assert len(orders[1]) == len(orders[2]) == 12
    assert orders[1] != orders[2]
