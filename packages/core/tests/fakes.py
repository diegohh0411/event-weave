from pydantic import BaseModel
from weave_core.adapters import AdapterSpec
from weave_core.stages import Stage, UnitKind, Capability

class Settings(BaseModel):
    window: int = 1

SPECS = []
for stage, name, kind in [(Stage.provide, "fake-provider", UnitKind.run), (Stage.extract, "fake-extractor", UnitKind.document), (Stage.tag, "fake-tagger", UnitKind.mention), (Stage.enrich, "fake-enricher", UnitKind.mention), (Stage.embed, "fake-embedder", UnitKind.mention), (Stage.cluster, "fake-cluster", UnitKind.mention)]:
    SPECS.append(type(name.replace("-", "_").title(), (), {"spec": AdapterSpec(name, "1", stage, kind, Settings, frozenset({Capability.extraction}) if stage == Stage.enrich else frozenset())}))
