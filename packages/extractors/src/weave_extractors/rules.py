"""Stanza-rules extractor: non-auxiliary verbs, listed event nouns, contiguous phrasal particles."""
from pydantic import BaseModel, ConfigDict
from weave_core.adapters import AdapterSpec
from weave_core.stages import Stage, UnitKind

from .base import ExtractorBase, SpanBatch
from .event_nouns import load_event_nouns
from .shape import Doc
from .stanza_client import HttpStanzaClient, Sentence, StanzaClient, check_language

NOT_EVENT_DEPRELS = {"aux", "aux:pass", "cop"}


class RulesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stanza_url: str | None = None


def aligned(doc: Doc, w) -> bool:
    return doc.text[w.start:w.end] == w.text


class StanzaRulesExtractor(ExtractorBase):
    spec = AdapterSpec(name="stanza-rules", version="1", stage=Stage.extract, input_kind=UnitKind.document,
                       settings_model=RulesSettings)

    def client(self) -> StanzaClient:
        return HttpStanzaClient(self.settings.stanza_url)

    def event_nouns(self, lang: str) -> frozenset[str]:
        return load_event_nouns(lang)

    def spans(self, doc: Doc) -> SpanBatch:
        check_language(doc.language)
        nouns = self.event_nouns(doc.language)
        spans, misaligned = [], 0
        for sentence in self.client().parse(doc.text, doc.language):
            for span in self._sentence_spans(doc, sentence, nouns):
                if span is None:
                    misaligned += 1
                else:
                    spans.append(span)
        return SpanBatch(spans, {"misaligned": misaligned})

    def _sentence_spans(self, doc: Doc, sentence: Sentence, nouns: frozenset[str]):
        words = sentence.words
        for i, w in enumerate(words, start=1):
            is_verb = w.upos == "VERB" and w.deprel not in NOT_EVENT_DEPRELS
            is_noun = w.upos == "NOUN" and (w.lemma or "").lower() in nouns
            if not (is_verb or is_noun):
                continue
            if not aligned(doc, w):
                yield None
                continue
            end = w.end
            if is_verb:
                for p in words:
                    if p.head == i and p.deprel == "compound:prt" and aligned(doc, p) \
                            and p.start >= end and not doc.text[end:p.start].strip():
                        end = p.end
            yield (w.start, end, None)
