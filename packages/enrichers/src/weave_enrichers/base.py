"""Shared enrich-stage behaviour: find the mention, load the document, enforce the slice invariant."""
import hashlib
import json
from typing import Mapping

from pydantic import BaseModel
from weave_core.adapters import AdapterContext, AdapterFailure, StageResult, UnitRef
from weave_core.stages import Stage

from ._shared import Doc, load_document, mention_id
from .shape import Enrichment, enrichment_id
from .window import MentionWindow, build_window

FIELDS = ("action", "participants", "location", "time")


def settings_hash(settings: BaseModel) -> str:
    """Stable digest of an adapter's settings, so a re-run with other settings gets its own enrichment rows."""
    canonical = json.dumps(settings.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class EnricherBase:
    spec = None  # set by subclasses

    def __init__(self, settings: BaseModel, ctx: AdapterContext):
        self.settings = settings
        self.ctx = ctx

    def load_document(self, doc_id: str) -> Doc:
        return load_document(doc_id)

    def enrich(self, doc: Doc, mention: dict, window: MentionWindow) -> tuple[Enrichment, dict[str, int]]:
        raise NotImplementedError

    def deployment(self) -> str | None:
        """Deployments behind this enricher's capabilities; None for model-free enrichers."""
        caps = sorted(self.spec.capabilities)
        if not caps or self.ctx is None:
            return None
        return ",".join(str(self.ctx.deployments.get(c)) for c in caps)

    def process(self, unit: UnitRef, upstream: Mapping[Stage, dict]) -> StageResult:
        found = (upstream.get(Stage.extract) or {}).get("mentions", [])
        mention = next((m for m in found if (m.get("id") or mention_id(m)) == unit.id), None)
        if mention is None:
            raise AdapterFailure(f"mention {unit.id} not in its extract output", retryable=False)
        doc = self.load_document(mention["document_id"])
        window = build_window(doc, mention, getattr(self.settings, "window_sentences", 1))
        enrichment, discarded = self.enrich(doc, mention, window)
        check_slices(doc.text, enrichment)
        row = {"id": enrichment_id(unit.id, self.spec.name, self.spec.version, self.deployment(),
                                settings_hash(self.settings)),
               "mention_id": unit.id, "enricher": self.spec.name, "enricher_version": self.spec.version,
               "deployment": self.deployment(), "discarded": discarded,
               **{name: getattr(enrichment, name).model_dump() for name in FIELDS}}
        return StageResult(output={"enrichment": enrichment.model_dump(), "discarded": discarded, "enrichment_id": row["id"]},
                           emits=[unit], rows={"enrichments": [row]})


def check_slices(doc_text: str, enrichment: Enrichment) -> None:
    """Every span slices the document and the field text is exactly those slices. A violation is a bug."""
    for name in FIELDS:
        field = getattr(enrichment, name)
        if not field.spans:
            if field.text is not None:
                raise AdapterFailure(f"{name}: text without spans", retryable=False)
            continue
        if any(not (0 <= s < e <= len(doc_text)) for s, e in field.spans):
            raise AdapterFailure(f"{name}: span outside the document", retryable=False)
        if field.text != "; ".join(doc_text[s:e] for s, e in field.spans):
            raise AdapterFailure(f"{name}: text does not match its spans", retryable=False)
