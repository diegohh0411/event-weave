"""End to end: stored documents -> replay -> gold extractor -> stub enricher -> embedding-gemma -> each cluster adapter."""
import hashlib
import re
import threading

import pytest
import sqlalchemy as sa
from pydantic import BaseModel
from weave_clusterer.embed import EmbeddingGemmaEmbedder
from weave_clusterer.report import recall_at_k, summary
from weave_clusterer.system_one import SystemOneClusterer
from weave_clusterer.threshold import ThresholdClusterer
from weave_core.adapters import AdapterSpec, StageResult
from weave_core.config import RunConfig
from weave_core.registry import Registry
from weave_core.runs import RunService
from weave_core.schema import cluster_decisions, cluster_members, documents, gold_mentions, gold_pairs, mentions
from weave_core.stages import Capability, RunState, Stage, Track, UnitKind
from weave_core.worker import run_worker
from weave_core.writer import run_writer
from weave_extractors.gold import GoldExtractor
from weave_inference.catalog import Catalog
from weave_inference.clients.stub import clear_stub_scripts, set_stub_script
from weave_inference.gateway import Inference
from weave_inference.probe import Readiness
from weave_inference.types import Deployment, Model, Provider
from weave_providers.replay import ReplayProvider

DOCS = {"fake:a": "Police arrested two men.", "fake:b": "Officers arrested two men.", "fake:c": "Rain fell."}
FIELDS = {"arrested": {"action": "arrested", "participants": "two men"}}  # "fell" yields nothing
SETTINGS = {"k": 3, "weights": {"action": 1.0, "participants": 1.0, "location": 1.0, "time": 1.0}, "threshold": 0.9}


class Empty(BaseModel):
    pass


class StubEnricher:
    """Scripted enrichment keyed by the mention's trigger text; writes the same row shape as the real enrichers."""
    spec = AdapterSpec("stub-enricher", "1", Stage.enrich, UnitKind.mention, Empty)

    def __init__(self, settings, ctx):
        pass

    def process(self, unit, upstream):
        m = next(m for m in upstream[Stage.extract]["mentions"] if hashlib.sha256(
            f"{m['document_id']}:{m['start']}:{m['end']}:{m['extractor']}:{m['extractor_version']}".encode()
        ).hexdigest()[:32] == unit.id)
        values = FIELDS.get(m["text"], {})
        enrichment = {f: {"text": values.get(f), "spans": [[0, 1]] if values.get(f) else [], "confidence": 1.0 if values.get(f) else None}
                      for f in ("action", "participants", "location", "time")}
        eid = "e-" + unit.id
        row = {"id": eid, "mention_id": unit.id, "enricher": "stub-enricher", "enricher_version": "1", **{f: v for f, v in enrichment.items()}}
        return StageResult({"enrichment": enrichment, "enrichment_id": eid}, emits=[unit], rows={"enrichments": [row]})


@pytest.fixture
def pipeline(db):
    catalog = Catalog(
        models={"embeddinggemma-2": Model(id="embeddinggemma-2", capability=Capability.embeddings),
                "m-s1": Model(id="m-s1", capability=Capability.system_one)},
        providers={"stub": Provider(id="stub", kind="stub", base_url="stub://")},
        deployments={"stub-emb": Deployment(id="stub-emb", model="embeddinggemma-2", provider="stub"),
                     "stub-s1": Deployment(id="stub-s1", model="m-s1", provider="stub")})
    catalog.readiness = {k: Readiness(ready=True) for k in catalog.deployments}
    Inference.configure(catalog, db)
    set_stub_script("stub", {"same": 0.95})
    with db.begin() as c:
        for i, (doc_id, text) in enumerate(DOCS.items()):
            provider, external = doc_id.split(":", 1)
            tokens = [list(m.span()) for m in re.finditer(r"\w+|[^\w\s]", text)]
            c.execute(sa.insert(documents).values(
                id=doc_id, article_id=doc_id, provider=provider, external_id=external, language="en", title="Title", byline="", outlet="",
                url="", published_at=None, text=text, token_offsets=tokens, sentence_offsets=[[0, len(text)]],
                content_hash=hashlib.sha256(text.encode()).hexdigest(), order_key=[i]))
            trigger = "arrested" if "arrested" in text else "fell"
            start = text.index(trigger)
            c.execute(sa.insert(gold_mentions).values(document_id=doc_id, mention_id="T" + external, start=start,
                                                      end=start + len(trigger), text=trigger))
        c.execute(sa.insert(gold_pairs).values(language="en", kind="cross", split="dev", topic="t",
                                               source_document_id="fake:a", source_mention_id="Ta",
                                               target_document_id="fake:b", target_mention_id="Tb", label=1))
    registry = Registry()
    for cls in (ReplayProvider, GoldExtractor, StubEnricher, EmbeddingGemmaEmbedder, ThresholdClusterer, SystemOneClusterer):
        registry.register(cls)
    yield RunService(db, registry, catalog), registry
    clear_stub_scripts()


def run(db, service, registry, adapter, deployments, threshold):
    stages = {Stage.provide: {"adapter": "replay", "settings": {"provider": "fake"}},
              Stage.extract: {"adapter": "gold"},
              Stage.enrich: {"adapter": "stub-enricher"},
              Stage.embed: {"adapter": "embedding-gemma", "settings": {"dimension": 8},
                            "deployments": {Capability.embeddings: "stub-emb"}},
              Stage.cluster: {"adapter": adapter, "settings": {**SETTINGS, "threshold": threshold}, "deployments": deployments}}
    run_id = service.create(RunConfig(name=adapter, track=Track.realistic, seed=1, stages=stages)).run_id
    service.start(run_id)
    for _ in range(8):
        for st in (Stage.provide, Stage.extract, Stage.enrich, Stage.embed):
            run_worker(db, registry, st, "w", stop=threading.Event(), once=True)
    run_writer(db, registry, Stage.cluster, "w", stop=threading.Event(), once=True)
    assert service.status(run_id).state == RunState.done
    return run_id


# Stub vectors are hashes, so cosine scores are arbitrary and the baseline may not join; System One is scripted to (Noul 0.95).
@pytest.mark.parametrize("adapter,deployments,calls,threshold", [
    ("threshold", {}, 0, 0.9), ("system-one", {Capability.system_one: "stub-s1"}, 1, 0.9)])
def test_each_cluster_adapter_runs_end_to_end(db, pipeline, adapter, deployments, calls, threshold):
    service, registry = pipeline
    run_id = run(db, service, registry, adapter, deployments, threshold)
    with db.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(mentions)) == 3
        members = dict(c.execute(sa.select(cluster_members.c.mention_id, cluster_members.c.cluster_id)
                                 .where(cluster_members.c.run_id == run_id)).all())
        reasons = {r.mention_id: r.reason for r in c.execute(sa.select(cluster_decisions).where(cluster_decisions.c.run_id == run_id))}
    assert len(members) == 3  # every mention clustered
    assert sorted(reasons.values()).count("no_fields") == 1
    s = summary(db, run_id)
    assert (s.mentions, s.clustered, s.failed, s.decision_calls) == (3, 3, 0, calls)
    if adapter == "system-one":  # the scripted Noul joins the two "arrested" mentions; "fell" has no fields
        assert (s.clusters, s.singletons) == (2, 1) and len(set(members.values())) == 2
    # The gold pair links the two "arrested" mentions: the second finds the first's cluster among its candidates.
    [row] = recall_at_k(db, run_id, 1)
    assert (row.language, row.mentions_with_gold_antecedent, row.hits) == ("en", 1, 1)
