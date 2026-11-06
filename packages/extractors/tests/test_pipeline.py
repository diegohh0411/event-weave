"""End to end: stored documents -> each extractor (stanza via fixtures, models via stub) -> finished run."""
import json
import threading

import pytest
import sqlalchemy as sa
from ext_helpers import make_doc, store
from pydantic import BaseModel
from stanza_fixtures import POLICE, POLICE_PARSE
from typer.testing import CliRunner
from weave_core.adapters import AdapterSpec, StageResult, UnitRef
from weave_core.config import RunConfig
from weave_core.registry import Registry
from weave_core.runs import RunService
from weave_core.schema import documents, gold_mentions, jobs, mentions
from weave_core.stages import Capability, JobStatus, RunState, Stage, Track, UnitKind
from weave_core.worker import run_worker
from weave_core.writer import run_writer
from weave_extractors import rules, system_one
from weave_extractors.gold import GoldExtractor
from weave_extractors.nuextract import NuExtract3Extractor
from weave_extractors.report import extract_report
from weave_extractors.rules import StanzaRulesExtractor
from weave_extractors.stanza_client import FixtureStanzaClient
from weave_extractors.system_one import SystemOneExtractor
from weave_extractors.tagging.adapter import SignificanceTagger
from weave_inference.clients.stub import set_stub_script

SECOND = "Rain fell."


class Empty(BaseModel):
    pass


class StoredDocs:
    spec = AdapterSpec("stored-docs", "1", Stage.provide, UnitKind.run, Empty, cacheable=False)

    def __init__(self, settings, ctx):
        pass

    def process(self, unit, upstream):
        engine = sa.create_engine(__import__("os").environ["WEAVE_DATABASE_URL"])
        with engine.connect() as c:
            rows = c.execute(sa.select(documents.c.id, documents.c.content_hash, documents.c.order_key)
                             .order_by(documents.c.order_key)).all()
        engine.dispose()
        return StageResult({}, [UnitRef(UnitKind.document, r.id, r.content_hash, tuple(r.order_key)) for r in rows])


def passthrough(stage, name):
    class Pass:
        spec = AdapterSpec(name, "1", stage, UnitKind.mention, Empty)

        def __init__(self, settings, ctx):
            pass

        def process(self, unit, upstream, *ordered):
            return StageResult({"seen": unit.id})
    return Pass


@pytest.fixture
def pipeline(db, stub_inference, tmp_path, monkeypatch):
    catalog = stub_inference()
    police, rain = make_doc(POLICE, doc_id="fake:police"), make_doc(SECOND, doc_id="fake:rain")
    store(db, police)
    store(db, rain)
    with db.begin() as c:
        c.execute(sa.update(documents).where(documents.c.id == "fake:rain").values(order_key=[1]))
        c.execute(sa.insert(gold_mentions), [
            {"document_id": police.id, "mention_id": "T1", "start": 7, "end": 11, "text": "said"},
            {"document_id": rain.id, "mention_id": "T2", "start": 0, "end": 4, "text": "Rain"}])
    client = FixtureStanzaClient(tmp_path)
    client.save(POLICE, "en", POLICE_PARSE)
    from stanza_fixtures import parse
    client.save(SECOND, "en", parse(SECOND, [("Rain", "NOUN", "rain", "nsubj", 2), ("fell", "VERB", "fall", "root", 0),
                                             (".", "PUNCT", ".", "punct", 2)]))
    monkeypatch.setattr(rules, "HttpStanzaClient", lambda url: client)
    monkeypatch.setattr(system_one, "HttpStanzaClient", lambda url: client)
    registry = Registry()
    for cls in (StoredDocs, GoldExtractor, StanzaRulesExtractor, SystemOneExtractor, NuExtract3Extractor,
                SignificanceTagger, passthrough(Stage.enrich, "pass-enrich"), passthrough(Stage.embed, "pass-embed"),
                passthrough(Stage.cluster, "pass-cluster")):
        registry.register(cls)
    return RunService(db, registry, catalog), registry


def run(db, service, registry, extractor, settings=None, deployments=None, tag=False):
    stages = {Stage.provide: {"adapter": "stored-docs"},
              Stage.extract: {"adapter": extractor, "settings": settings or {}, "deployments": deployments or {}},
              Stage.enrich: {"adapter": "pass-enrich"}, Stage.embed: {"adapter": "pass-embed"},
              Stage.cluster: {"adapter": "pass-cluster"}}
    if tag:
        stages[Stage.tag] = {"adapter": "significance", "deployments": {Capability.system_one: "stub-s1"}}
    run_id = service.create(RunConfig(name=extractor, track=Track.realistic, seed=1, stages=stages)).run_id
    service.start(run_id)
    for _ in range(8):
        for st in (Stage.provide, Stage.extract, Stage.tag, Stage.enrich, Stage.embed):
            run_worker(db, registry, st, "w", stop=threading.Event(), once=True)
    run_writer(db, registry, Stage.cluster, "w", stop=threading.Event(), once=True)
    assert service.status(run_id).state == RunState.done
    return run_id


def enrich_units(db, run_id):
    with db.connect() as c:
        return set(c.execute(sa.select(jobs.c.unit_id, jobs.c.unit_content_hash)
                             .where(jobs.c.run_id == run_id, jobs.c.stage == Stage.enrich)).all())


def all_mentions_slice(db):
    with db.connect() as c:
        rows = c.execute(sa.select(mentions.c.text, documents.c.text.label("doc"), mentions.c.start, mentions.c.end)
                         .join(documents, documents.c.id == mentions.c.document_id)).all()
    assert rows and all(r.doc[r.start:r.end] == r.text for r in rows)


def test_each_extractor_runs_end_to_end(db, pipeline):
    service, registry = pipeline
    set_stub_script("stub", {f"c{i}": 0.9 for i in range(10)})
    runs = {
        "gold": run(db, service, registry, "gold"),
        "stanza-rules": run(db, service, registry, "stanza-rules"),
        "system-one": run(db, service, registry, "system-one", {"threshold": 0.5}, {Capability.system_one: "stub-s1"}),
        "nuextract3": run(db, service, registry, "nuextract3", {}, {Capability.llm: "stub-llm"}),
    }
    all_mentions_slice(db)
    gold = extract_report(db, runs["gold"])
    assert gold == {"documents": 2, "mentions": 2, "misaligned": 0, "unlocated": 0, "unlocated_windows": 0,
                    "failed_jobs": 0}
    assert len(enrich_units(db, runs["gold"])) == 2
    nu = extract_report(db, runs["nuextract3"])
    assert nu["mentions"] == 0 and nu["unlocated_windows"] == 2   # stub chat replies "stub": not JSON


def test_tag_off_same_downstream(db, pipeline):
    service, registry = pipeline
    off = run(db, service, registry, "gold")
    on = run(db, service, registry, "gold", tag=True)
    assert enrich_units(db, on) == enrich_units(db, off)
    with db.connect() as c:
        assert c.scalar(sa.text("select count(*) from mention_tags")) == 2


def test_report_cli(db, pipeline):
    service, registry = pipeline
    run_id = run(db, service, registry, "gold")
    from weave_extractors import cli
    result = CliRunner().invoke(cli.app, ["report", str(run_id)])
    assert result.exit_code == 0, result.output
    assert "mentions" in result.output and "2" in result.output
