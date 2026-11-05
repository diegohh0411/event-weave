from hashlib import sha256

import sqlalchemy as sa

from pydantic import BaseModel, Field
from weave_core.adapters import AdapterSpec, AdapterFailure, DocumentRecord, StageResult, UnitRef
from weave_core.stages import Stage, UnitKind, Capability


class Settings(BaseModel):
    window: int = 1


class ProviderSettings(Settings):
    docs: int = 3
    duplicate_first: bool = False
    no_topic: bool = False


class EnricherSettings(Settings):
    fail_on: list[str] = Field(default_factory=list)


class Fake:
    calls = 0

    def __init__(self, settings, ctx):
        self.settings = settings
        self.ctx = ctx

    def process(self, unit, upstream):
        type(self).calls += 1
        if self.spec.stage == Stage.provide:
            documents, emits = [], []
            for i in range(self.settings.docs):
                external = f"d{i}"
                digest = sha256(external.encode()).hexdigest()
                documents.append(DocumentRecord(
                    "fake", external, "en", external, "", "", "", None,
                    external, [], [], digest))
                emits.append(UnitRef(UnitKind.document, f"fake:{external}", digest,
                                     (i,), None if self.settings.no_topic else f"t{i % 2}"))
            if self.settings.duplicate_first and emits:
                documents.append(documents[0])
                emits.append(emits[0])
            return StageResult({}, emits, documents)
        if self.spec.stage == Stage.extract:
            return StageResult({}, [
                UnitRef(UnitKind.mention, f"{unit.id}#{j}",
                        sha256(f"{unit.content_hash}:{j}".encode()).hexdigest())
                for j in range(2)])
        if self.spec.stage == Stage.enrich and unit.id in self.settings.fail_on:
            raise AdapterFailure(f"failed: {unit.id}")
        return StageResult({"seen": unit.id})


class FakeCluster(Fake):
    def process(self, unit, upstream, partition, session):
        type(self).calls += 1
        cluster = hash(unit.id) % 3
        session.execute(sa.text(
            "INSERT INTO fake_cluster_log (run_id, partition, unit_id, cluster) "
            "VALUES (:run, :partition, :unit, :cluster)"
        ), {"run": self.ctx.run_id, "partition": partition,
            "unit": unit.id, "cluster": cluster})
        return StageResult({"cluster": cluster})


MATRIX = [
    (Stage.provide, "fake-provider", UnitKind.run, ProviderSettings),
    (Stage.extract, "fake-extractor", UnitKind.document, Settings),
    (Stage.tag, "fake-tagger", UnitKind.mention, Settings),
    (Stage.enrich, "fake-enricher", UnitKind.mention, EnricherSettings),
    (Stage.embed, "fake-embedder", UnitKind.mention, Settings),
    (Stage.cluster, "fake-cluster", UnitKind.mention, Settings),
]
SPECS = []
for stage, name, kind, settings in MATRIX:
    capabilities = frozenset({Capability.extraction}) if stage == Stage.enrich else frozenset()
    SPECS.append(type(name.replace("-", "_").title(), (FakeCluster if stage == Stage.cluster else Fake,), {
        "calls": 0,
        "spec": AdapterSpec(name, "1", stage, kind, settings, capabilities),
    }))
