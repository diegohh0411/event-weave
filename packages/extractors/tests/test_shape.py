from ext_helpers import make_doc, unit
from weave_extractors.base import ExtractorBase, SpanBatch
from weave_extractors.shape import Mention, char_to_token_span
from pydantic import BaseModel
from weave_core.adapters import AdapterSpec
from weave_core.stages import Stage, UnitKind


def mention(**kw):
    base = dict(document_id="fake:d1", start=0, end=4, tok_start=0, tok_end=1, sentence=0, text="Rain",
                extractor="x", extractor_version="1")
    return Mention(**(base | kw))


def test_mention_id_stable_and_differs_by_version():
    assert mention().id == mention().id and len(mention().id) == 32
    assert mention().id != mention(extractor_version="2").id


def test_char_to_token_span_covering():
    doc = make_doc("Rain fell. Then floods came.")
    assert char_to_token_span(doc, 11, 15) == (3, 4, 1)      # "Then"
    assert char_to_token_span(doc, 12, 22) == (3, 5, 1)      # "hen floods" -> covering tokens
    assert char_to_token_span(doc, 0, 9) == (0, 2, 0)


class NoSettings(BaseModel):
    pass


class Fixed(ExtractorBase):
    spec = AdapterSpec("fixed", "1", Stage.extract, UnitKind.document, NoSettings)

    def __init__(self, doc, spans):
        super().__init__(NoSettings(), None)
        self._doc, self._spans = doc, spans

    def load_document(self, doc_id):
        return self._doc

    def spans(self, doc):
        return SpanBatch(self._spans, {})


def test_base_drops_misaligned_and_counts():
    doc = make_doc("Rain fell.")
    result = Fixed(doc, [(0, 4, None), (5, 99, None), (-1, 3, None), (3, 2, None)]).process(unit(doc), {})
    assert [m["text"] for m in result.output["mentions"]] == ["Rain"]
    assert result.output["misaligned"] == 3


def test_base_overlap_longest_wins():
    doc = make_doc("They set up camp.")
    result = Fixed(doc, [(5, 8, 0.4), (5, 11, 0.9), (12, 16, None)]).process(unit(doc), {})
    assert [m["text"] for m in result.output["mentions"]] == ["set up", "camp"]
    assert result.output["mentions"][0]["probability"] == 0.9
