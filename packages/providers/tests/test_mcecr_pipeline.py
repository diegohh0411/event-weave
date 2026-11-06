"""MCECR provide stage through the real core worker and Postgres, then a fake extract."""
import os
import threading
import uuid
from contextlib import contextmanager
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

ADMIN_URL = os.environ.get(
    "WEAVE_TEST_DATABASE_URL", "postgresql+psycopg://weave:weave@127.0.0.1:5432/postgres")
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


@contextmanager
def database():
    name = "weave_mcecr_" + uuid.uuid4().hex[:8]
    admin = sa.create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    url = sa.engine.make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}"')
    try:
        migrate(url)
        yield url
    finally:
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.dispose()


def test_mcecr_run_provides_documents_gold_and_feeds_extract():
    registry = Registry()
    registry.register(McecrProvider)
    for stage, name, kind in [(Stage.extract, "x", UnitKind.document), (Stage.enrich, "e", UnitKind.mention),
                              (Stage.embed, "m", UnitKind.mention), (Stage.cluster, "c", UnitKind.mention)]:
        registry.register(fake(stage, name, kind))
    cfg = RunConfig(name="t", track="paper", seed=1, stages={
        Stage.provide: {"adapter": "mcecr", "settings": {"path": str(FIX), "splits": ["test", "train"]}},
        Stage.extract: {"adapter": "x"}, Stage.enrich: {"adapter": "e"},
        Stage.embed: {"adapter": "m"}, Stage.cluster: {"adapter": "c"}})
    with database() as url:
        engine = make_engine(url)
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
