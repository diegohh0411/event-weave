"""NuExtract3 enricher: one chat request per mention, every answer located in the document by exact match only."""
import json

from pydantic import BaseModel, ConfigDict, Field as PField
from weave_core.adapters import AdapterSpec
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.gateway import Inference
from weave_inference.types import ChatRequest

from ._shared import Doc
from .base import EnricherBase
from .shape import Enrichment, TimeField
from .timenorm import check_normalized
from .verify import verify_field
from .window import MentionWindow

TEMPLATE = {"action": "verbatim-string", "participants": ["verbatim-string"], "location": "verbatim-string",
            "time": "verbatim-string", "time_normalized": "date-time"}
INSTRUCTIONS = ("For the event marked ⟦like this⟧ in the text, give its action, participants, location and time, "
                "each copied verbatim from the text. Also give the time as an ISO 8601 date-time"
                "{published}. Leave a field null when the text does not state it.")


class NuExtractSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_sentences: int = PField(default=1, ge=0)


def _answers(value) -> list[str]:
    """A reply value as a list of answers; missing or null is no answers."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class NuExtract3Enricher(EnricherBase):
    spec = AdapterSpec(name="nuextract3", version="1", stage=Stage.enrich, input_kind=UnitKind.mention,
                       settings_model=NuExtractSettings, capabilities=frozenset({Capability.llm}))

    def build_request(self, window: MentionWindow) -> ChatRequest:
        published = ", relative to the publication date" if "Published:" in window.header else ""
        parts = [f"# Instructions:\n{INSTRUCTIONS.format(published=published)}",
                 f"# Template:\n{json.dumps(TEMPLATE)}"]
        context = f"{window.header}\n\n{window.text}" if window.header else window.text
        parts.append(f"# Context:\n{context}")
        return ChatRequest(messages=[{"role": "user", "content": "\n".join(parts)}],
                           template_args={"template": TEMPLATE, "enable_thinking": False})

    def enrich(self, doc: Doc, mention: dict, window: MentionWindow) -> tuple[Enrichment, dict[str, int]]:
        text = Inference.for_context(self.ctx).chat(self.build_request(window)).value.text
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise TypeError
        except (ValueError, TypeError):
            return Enrichment(), {"unparsed": 1}
        anchor, discarded, fields = window.trigger[0], {}, {}
        for name, multi in (("action", False), ("participants", True), ("location", False), ("time", False)):
            answers = _answers(data.get(name))
            good = [a for a in answers if isinstance(a, str)]
            fields[name], bad = verify_field(doc.text, good, anchor, multi=multi)
            if bad + len(answers) - len(good):
                discarded[name] = bad + len(answers) - len(good)
        raw = data.get("time_normalized")
        normalized = check_normalized(raw if isinstance(raw, str) else None, doc.published_at, fields["time"].text)
        time = TimeField(**fields["time"].model_dump(), normalized=normalized)
        return Enrichment(action=fields["action"], participants=fields["participants"],
                          location=fields["location"], time=time), discarded
