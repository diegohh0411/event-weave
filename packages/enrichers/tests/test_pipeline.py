"""End to end: stored documents -> replay -> gold extractor -> each enricher (stub models, fixture Stanza) -> report."""
import threading

import pytest
import sqlalchemy as sa
from helpers import make_doc
from pydantic import BaseModel
from stanza_fixture import ARREST, ARREST_PARSE, parse
from typer.testing import CliRunner
from weave_core.adapters import AdapterSpec, StageResult
from weave_core.config import RunConfig
from weave_core.registry import Registry
from weave_core.runs import RunService
from weave_core.schema import documents, enrichments, gold_mentions, mentions
from weave_core.stages import Capability, RunState, Stage, Track, UnitKind
from weave_core.worker import run_worker
from weave_core.writer import run_writer
from weave_enrichers import cli, system_one
from weave_enrichers._shared import FixtureStanzaClient
from weave_enrichers.base import check_slices
from weave_enrichers.clairic import ClairicEnricher, clear_cache as clear_clairic
from weave_enrichers.nuextract import NuExtract3Enricher
from weave_enrichers.report import coverage, sample
from weave_enrichers.shape import Enrichment
from weave_enrichers.system_one import SystemOneEnricher
from weave_extractors.gold import GoldExtractor
from weave_inference.clients.stub import set_stub_script
from weave_inference.probe import Readiness
from weave_inference.types import Deployment, Limits, Model
from weave_providers.replay import ReplayProvider

SECOND = "Rain fell."


class Empty(BaseModel):
    pass


def passthrough(stage, name):
    class Pass:
        spec = AdapterSpec(name, "1", stage, UnitKind.mention, Empty)

        def __init__(self, settings, ctx):
            pass

        def process(self, unit, upstream, *ordered):
            return StageResult({"seen": unit.id})
    return Pass


def store(db, doc, language="en"):
    provider, external = doc.id.split(":", 1)
    with db.begin() as c:
        c.execute(sa.insert(documents).values(
            id=doc.id, article_id=doc.id, provider=provider, external_id=external, language=language, title="Title", byline="",
            outlet="", url="", published_at=None, text=doc.text, token_offsets=[list(t) for t in doc.token_offsets],
            sentence_offsets=[list(s) for s in doc.sentence_offsets], content_hash=doc.content_hash,
            order_key=[0 if doc.id == "fake:arrest" else 1]))


@pytest.fixture
def pipeline(db, stub_inference, tmp_path, monkeypatch):
    catalog = stub_inference()
    catalog.models["m-x"] = Model(id="m-x", capability=Capability.extraction)
    catalog.deployments["stub-x"] = Deployment(id="stub-x", model="m-x", provider="stub", limits=Limits())
    catalog.readiness["stub-x"] = Readiness(ready=True)
    clear_clairic()
    system_one.clear_cache()
    arrest, rain = make_doc(ARREST, doc_id="fake:arrest"), make_doc(SECOND, doc_id="fake:rain")
    store(db, arrest)
    store(db, rain, "es")
    with db.begin() as c:
        c.execute(sa.insert(gold_mentions), [
            {"document_id": arrest.id, "mention_id": "T1", "start": 7, "end": 15, "text": "arrested"},
            {"document_id": rain.id, "mention_id": "T2", "start": 5, "end": 9, "text": "fell"}])
    client = FixtureStanzaClient(tmp_path)
    client.save(ARREST, "en", ARREST_PARSE, ner=True)
    client.save(SECOND, "es", parse(SECOND, [("Rain", "NOUN", "rain", "nsubj", 2), ("fell", "VERB", "fall", "root", 0),
                                             (".", "PUNCT", ".", "punct", 2)]), ner=True)
    monkeypatch.setattr(SystemOneEnricher, "client", lambda self: client)
    registry = Registry()
    for cls in (passthrough(Stage.embed, "pass-embed"), passthrough(Stage.cluster, "pass-cluster"), ReplayProvider, GoldExtractor, ClairicEnricher, NuExtract3Enricher, SystemOneEnricher):
        registry.register(cls)
    return RunService(db, registry, catalog), registry


def run(db, service, registry, enricher, settings=None, deployments=None):
    stages = {Stage.provide: {"adapter": "replay", "settings": {"provider": "fake"}},
              Stage.extract: {"adapter": "gold"},
              Stage.enrich: {"adapter": enricher, "settings": settings or {}, "deployments": deployments or {}},
              Stage.embed: {"adapter": "pass-embed"}, Stage.cluster: {"adapter": "pass-cluster"}}
    run_id = service.create(RunConfig(name=enricher, track=Track.realistic, seed=1, stages=stages)).run_id
    service.start(run_id)
    for _ in range(8):
        for st in (Stage.provide, Stage.extract, Stage.enrich, Stage.embed):
            run_worker(db, registry, st, "w", stop=threading.Event(), once=True)
    run_writer(db, registry, Stage.cluster, "w", stop=threading.Event(), once=True)
    assert service.status(run_id).state == RunState.done
    return run_id


def assert_rows_and_slices(db, enricher):
    with db.connect() as c:
        rows = c.execute(sa.select(enrichments, documents.c.text.label("doc")).join(
            mentions, mentions.c.id == enrichments.c.mention_id).join(
            documents, documents.c.id == mentions.c.document_id).where(enrichments.c.enricher == enricher)).all()
        assert len(rows) == c.scalar(sa.select(sa.func.count()).select_from(mentions)) == 2  # one per mention
    for r in rows:
        check_slices(r.doc, Enrichment.model_validate({n: getattr(r, n) for n in ("action", "participants", "location", "time")}))
    return rows


def runs(db, service, registry):
    set_stub_script("stub", {"p0": 0.9, "p1": 0.2, "loc_stated": 0.8, "time_stated": 0.7})
    return {
        "system-one": run(db, service, registry, "system-one",
                          {"participant_threshold": 0.5, "stated_threshold": 0.5}, {Capability.system_one: "stub-s1"}),
        "nuextract3": run(db, service, registry, "nuextract3", {}, {Capability.llm: "stub-llm"}),
        "clairic": run(db, service, registry, "clairic", {}, {Capability.extraction: "stub-x"}),
    }


def test_each_enricher_runs_end_to_end(db, pipeline):
    service, registry = pipeline
    ids = runs(db, service, registry)
    for name in ids:
        assert_rows_and_slices(db, name)


def test_coverage_matches_hand_count(db, pipeline):
    service, registry = pipeline
    ids = runs(db, service, registry)
    by = {(r.language, r.field): r for r in coverage(db, ids["system-one"])}
    # "Police arrested two men in Madrid on Monday": p0 passes, p1 does not; Madrid and Monday are stated.
    assert by["en", "action"].mentions == 1 and by["en", "action"].share == 1.0
    assert by["en", "participants"].filled == 1 and by["en", "location"].share == 1.0
    assert by["en", "time"].share == 1.0
    # "Rain fell.": no candidates for location or time.
    assert by["es", "location"].filled == 0 and by["es", "location"].share == 0
    nu = {(r.language, r.field): r for r in coverage(db, ids["nuextract3"])}
    assert all(r.filled == 0 for r in nu.values() if r.field in ("action", "participants", "location", "time"))
    assert nu["en", "unparsed"].discarded == 1


def test_sample_deterministic_over_pipeline(db, pipeline):
    service, registry = pipeline
    run_id = runs(db, service, registry)["system-one"]
    assert sample(db, run_id, 2, seed=7) == sample(db, run_id, 2, seed=7)
    assert {s["sentence"] for s in sample(db, run_id, 2, seed=7)} == {"Police ⟦arrested⟧ two men in Madrid on Monday.", "Rain ⟦fell⟧."}


def test_report_cli(db, pipeline):
    service, registry = pipeline
    run_id = runs(db, service, registry)["system-one"]
    result = CliRunner().invoke(cli.app, ["report", str(run_id)])
    assert result.exit_code == 0, result.output
    assert "system-one" in result.output and "participants" in result.output and "share" in result.output
