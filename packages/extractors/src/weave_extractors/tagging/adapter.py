"""Tag stage: significance (Noul) and event type (Choice) for every mention. Never removes mentions."""
from typing import Mapping

from pydantic import BaseModel, ConfigDict
from weave_core.adapters import AdapterContext, AdapterFailure, AdapterSpec, StageResult, UnitRef
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.gateway import Inference
from weave_inference.types import Choice, Noul, SystemOneRequest

from ..candidates import mark
from ..shape import Doc, Mention, load_document
from .questions import EVENT_TYPE_PROMPT, EVENT_TYPES, SIGNIFICANCE


class TaggerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignificanceTagger:
    spec = AdapterSpec(name="significance", version="1", stage=Stage.tag, input_kind=UnitKind.mention,
                       settings_model=TaggerSettings, capabilities=frozenset({Capability.system_one}))

    def __init__(self, settings: TaggerSettings, ctx: AdapterContext):
        self.settings = settings
        self.ctx = ctx

    def state(self, doc: Doc, m: Mention) -> dict:
        s, e = doc.sentence_offsets[m.sentence] if m.sentence < len(doc.sentence_offsets) else (0, len(doc.text))
        return {"title": doc.title, "sentence": mark(doc.text[s:e], m.start - s, m.end - s)}

    def process(self, unit: UnitRef, upstream: Mapping[Stage, dict]) -> StageResult:
        found = [Mention(**m) for m in (upstream.get(Stage.extract) or {}).get("mentions", [])]
        mention = next((m for m in found if m.id == unit.id), None)
        if mention is None:
            raise AdapterFailure(f"mention {unit.id} not in its extract output", retryable=False)
        doc = load_document(mention.document_id)
        request = SystemOneRequest(state=self.state(doc, mention), questions=[
            Noul(id="significance", prompt=SIGNIFICANCE),
            Choice(id="event_type", prompt=EVENT_TYPE_PROMPT, options=EVENT_TYPES)])
        result = Inference.for_context(self.ctx).system_one(request)
        answers = {a.question_id: a for a in result.value.answers}
        sig, kind = answers.get("significance"), answers.get("event_type")
        output = {"significance": None if sig is None or sig.value is None else float(sig.value),
                  "event_type": None if kind is None else kind.value,
                  "event_type_probs": None if kind is None else kind.probabilities}
        row = {"mention_id": mention.id, "tagger": self.spec.name, "tagger_version": self.spec.version,
               "deployment": result.deployment, **output}
        return StageResult(output=output, emits=None, rows={"mention_tags": [row]})
