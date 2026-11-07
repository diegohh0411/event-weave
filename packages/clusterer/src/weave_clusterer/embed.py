"""Embed stage: one document vector and one query vector per non-empty enrichment field."""
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field as PField
from weave_core.adapters import AdapterContext, AdapterFailure, AdapterSpec, StageResult, UnitRef
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.gateway import Inference
from weave_inference.types import EmbeddingsRequest

from .fields import FIELDS, field_text
from .vectors import member_vector_id

# Retrieval prefixes per embedding model id: (document, query).
PREFIXES = {
    "embeddinggemma-2": ("title: none | text: {value}", "task: search result | query: {value}"),
}


class EmbedSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: int = PField(gt=0)  # pinned in the run config; a vector of any other length fails the job


class EmbeddingGemmaEmbedder:
    spec = AdapterSpec(name="embedding-gemma", version="1", stage=Stage.embed, input_kind=UnitKind.mention,
                       settings_model=EmbedSettings, capabilities=frozenset({Capability.embeddings}))

    def __init__(self, settings: EmbedSettings, ctx: AdapterContext):
        self.settings, self.ctx = settings, ctx

    def process(self, unit: UnitRef, upstream: Mapping[Stage, dict]) -> StageResult:
        enrich = upstream.get(Stage.enrich) or {}
        enrichment = enrich.get("enrichment") or {}
        present = [(f, t) for f in FIELDS if (t := field_text(enrichment, f))]
        if not present:
            return StageResult(output={}, emits=[unit], rows={})
        enrichment_id = enrich.get("enrichment_id")
        if not enrichment_id:
            raise AdapterFailure("enrich output has no enrichment_id", retryable=False)
        inference = Inference.for_context(self.ctx)
        deployment = inference.deployment(Capability.embeddings)
        prefixes = PREFIXES.get(deployment.model)
        if prefixes is None:
            raise AdapterFailure(f"no embedding prefixes for model {deployment.model}", retryable=False)
        doc_prefix, query_prefix = prefixes
        kinds = ("document", "query")
        texts = [p.format(value=t) for _, t in present for p in (doc_prefix, query_prefix)]
        vectors = inference.embed(EmbeddingsRequest(texts=texts)).value.vectors
        if len(vectors) != len(texts):
            raise AdapterFailure(f"expected {len(texts)} vectors, got {len(vectors)}", retryable=False)
        dim = self.settings.dimension
        if any(len(v) != dim for v in vectors):
            raise AdapterFailure(f"embedding dimension is not {dim}", retryable=False)
        rows, ids = [], {}
        it = iter(vectors)
        for field, _ in present:
            for kind in kinds:
                vid = member_vector_id(enrichment_id, field, kind, deployment.id, dim)
                ids[f"{field}:{kind}"] = vid
                rows.append({"id": vid, "mention_id": unit.id, "enrichment_id": enrichment_id, "field": field,
                             "kind": kind, "deployment": deployment.id, "dimension": dim, "vector": next(it)})
        return StageResult(output={"fields": [f for f, _ in present], "vector_ids": ids}, emits=[unit],
                           rows={"member_vectors": rows})
