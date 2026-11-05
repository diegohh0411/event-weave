"""Inference-calling fake adapters. Core tests never import this module."""
from pydantic import BaseModel
from weave_core.adapters import AdapterSpec, StageResult
from weave_core.stages import Capability, Stage, UnitKind

from weave_inference.gateway import Inference
from weave_inference.types import Choice, EmbeddingsRequest, Noul, SystemOneRequest


class Settings(BaseModel):
    window: int = 1


class InfEnricher:
    calls = 0
    spec = AdapterSpec("fake-enricher", "1", Stage.enrich, UnitKind.mention, Settings, frozenset({Capability.system_one}))

    def __init__(self, settings, ctx):
        self.ctx = ctx

    def process(self, unit, upstream):
        type(self).calls += 1
        res = Inference.for_context(self.ctx).system_one(SystemOneRequest(
            state=unit.id, questions=[Choice(id="stance", prompt="stance?", options=["pro", "con"]), Noul(id="n", prompt="n?")]))
        return StageResult({"answers": [a.value for a in res.value.answers]})


class InfEmbedder:
    calls = 0
    spec = AdapterSpec("fake-embedder", "1", Stage.embed, UnitKind.mention, Settings, frozenset({Capability.embeddings}))

    def __init__(self, settings, ctx):
        self.ctx = ctx

    def process(self, unit, upstream):
        type(self).calls += 1
        res = Inference.for_context(self.ctx).embed(EmbeddingsRequest(texts=[unit.id]))
        return StageResult({"dim": len(res.value.vectors[0])})
