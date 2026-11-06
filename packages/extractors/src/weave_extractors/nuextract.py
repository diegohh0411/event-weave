"""NuExtract3 extractor: verbatim triggers per sentence window, located by exact match only."""
import json

from pydantic import BaseModel, ConfigDict, Field
from weave_core.adapters import AdapterSpec
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.gateway import Inference
from weave_inference.types import ChatRequest

from .base import ExtractorBase, SpanBatch
from .shape import Doc
from .system_one import EVENT_DEFINITION

TEMPLATE = {"event_triggers": ["verbatim-string"]}


class NuExtractSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_sentences: int = Field(default=3, ge=1)


def windows(doc: Doc, size: int) -> list[tuple[int, int]]:
    """Non-overlapping runs of `size` stored sentences, as (start, end) character offsets."""
    sents = doc.sentence_offsets or ([(0, len(doc.text))] if doc.text else [])
    return [(sents[i][0], sents[min(i + size, len(sents)) - 1][1]) for i in range(0, len(sents), size)]


def locate(window_text: str, triggers: list[str]) -> tuple[list[tuple[int, int]], int]:
    """Exact matches, left to right; a repeated trigger takes the next occurrence after its previous hit."""
    spans, unlocated, after = [], 0, {}
    for trigger in triggers:
        if not isinstance(trigger, str) or not trigger.strip():
            unlocated += 1
            continue
        at = window_text.find(trigger, after.get(trigger, 0))
        if at < 0:
            unlocated += 1
            continue
        spans.append((at, at + len(trigger)))
        after[trigger] = at + len(trigger)
    return spans, unlocated


class NuExtract3Extractor(ExtractorBase):
    spec = AdapterSpec(name="nuextract3", version="1", stage=Stage.extract, input_kind=UnitKind.document,
                       settings_model=NuExtractSettings, capabilities=frozenset({Capability.llm}))

    def build_request(self, window_text: str) -> ChatRequest:
        content = (f"# Instructions:\nList every event trigger in the text, copied verbatim. {EVENT_DEFINITION}\n"
                   f"# Template:\n{json.dumps(TEMPLATE)}\n# Context:\n{window_text}")
        return ChatRequest(messages=[{"role": "user", "content": content}], template_args={"template": TEMPLATE})

    def spans(self, doc: Doc) -> SpanBatch:
        inference = Inference.for_context(self.ctx)
        spans, unlocated, bad = [], 0, 0
        for start, end in windows(doc, self.settings.window_sentences):
            text = inference.chat(self.build_request(doc.text[start:end])).value.text
            try:
                triggers = json.loads(text)["event_triggers"]
                if not isinstance(triggers, list):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                bad += 1
                continue
            found, missed = locate(doc.text[start:end], triggers)
            spans += [(start + s, start + e, None) for s, e in found]
            unlocated += missed
        return SpanBatch(spans, {"unlocated": unlocated, "unlocated_windows": bad})
