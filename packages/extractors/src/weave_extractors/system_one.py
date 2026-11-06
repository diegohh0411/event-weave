"""System One extractor: Stanza proposes candidates, one Noul per candidate decides."""
from pydantic import BaseModel, ConfigDict, Field
from weave_core.adapters import AdapterFailure, AdapterSpec
from weave_core.stages import Capability, Stage, UnitKind
from weave_inference.gateway import Inference
from weave_inference.types import LimitExceeded, Noul, SystemOneRequest

from .base import ExtractorBase, SpanBatch
from .candidates import Candidate, candidates, mark
from .shape import Doc
from .stanza_client import HttpStanzaClient, Sentence, StanzaClient, check_language

# Written from the MCECR paper's annotation section. Diego reviews this wording before the first full run.
EVENT_DEFINITION = (
    "An event is something that happens or holds: an occurrence, action, process or state. "
    "An event trigger is the word or short phrase that most clearly names it. Triggers are usually verbs "
    "(attacked, resigned), event nouns (election, earthquake, protest) and some adjectives (dead, injured). "
    "Reporting and communication verbs such as \"said\", \"announced\" or \"told\" are events too. "
    "Entities, people, places, times and plain descriptions are not events.")
INSTRUCTION = "For each question: does the word marked with ⟦ ⟧ in the sentence name an event, as defined?"


class SystemOneSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: float = Field(ge=0, le=1)  # chosen on MCECR English dev; frozen in the run config
    stanza_url: str | None = None
    batch: int = Field(default=64, ge=1)


def sentence_text(sentence: Sentence, doc: Doc) -> tuple[str, int]:
    start = sentence.words[0].start
    return doc.text[start:sentence.words[-1].end], start


class SystemOneExtractor(ExtractorBase):
    spec = AdapterSpec(name="system-one", version="1", stage=Stage.extract, input_kind=UnitKind.document,
                       settings_model=SystemOneSettings, capabilities=frozenset({Capability.system_one}))

    def client(self) -> StanzaClient:
        return HttpStanzaClient(self.settings.stanza_url)

    def build_request(self, doc: Doc, sentences: list[Sentence], items: list[tuple[int, Candidate]]) -> SystemOneRequest:
        questions = []
        for i, c in items:
            text, offset = sentence_text(sentences[c.sentence], doc)
            questions.append(Noul(id=f"c{i}", prompt=mark(text, c.start - offset, c.end - offset)))
        return SystemOneRequest(state={"title": doc.title, "definition": EVENT_DEFINITION, "instruction": INSTRUCTION},
                                questions=questions)

    def spans(self, doc: Doc) -> SpanBatch:
        check_language(doc.language)
        sentences = self.client().parse(doc.text, doc.language)
        found, misaligned = candidates(doc, sentences)
        inference = Inference.for_context(self.ctx)
        probs: dict[int, float | None] = {}
        pending = list(enumerate(found))
        size = self.settings.batch
        while pending:
            items = pending[:size]
            try:
                result = inference.system_one(self.build_request(doc, sentences, items))
            except LimitExceeded as exc:
                if size == 1:
                    raise AdapterFailure(f"one candidate exceeds the deployment's limits: {exc}", retryable=False) from exc
                size = max(1, size // 2)
                continue
            answers = {a.question_id: a.value for a in result.value.answers}
            for i, _ in items:
                value = answers.get(f"c{i}")
                probs[i] = None if value is None else float(value)
            pending = pending[len(items):]
        spans = [(c.start, c.end, probs[i]) for i, c in enumerate(found)
                 if probs[i] is not None and probs[i] >= self.settings.threshold]
        return SpanBatch(spans, {"misaligned": misaligned,
                                 "candidates": [[c.start, c.end, probs[i]] for i, c in enumerate(found)]})
