import pytest
from contract import run_contract
from helpers import ctx, make_doc, make_mention, unit_for
from weave_core.adapters import AdapterFailure, AdapterSpec
from weave_core.stages import Stage, UnitKind
from weave_enrichers.base import EnricherBase
from weave_enrichers.shape import Enrichment, Field, enrichment_id
from weave_enrichers.verify import verify_field
from pydantic import BaseModel


class S(BaseModel):
    window_sentences: int = 1


class Fake(EnricherBase):
    spec = AdapterSpec(name="fake", version="1", stage=Stage.enrich, input_kind=UnitKind.mention, settings_model=S)

    def __init__(self, doc, answer=None, bad=False):
        super().__init__(S(), ctx())
        self.doc, self.answer, self.bad = doc, answer, bad

    def load_document(self, doc_id):
        return self.doc

    def enrich(self, doc, mention, window):
        if self.bad:
            return Enrichment(action=Field(text="nope", spans=[(0, 4)])), {}
        action, d = verify_field(doc.text, [self.answer] if self.answer else [], mention["start"], multi=False)
        return Enrichment(action=action), {"action": d}


DOC = make_doc("Police arrested him.")
M = make_mention(DOC, 7, 15)


def test_contract():
    run_contract(lambda doc: Fake(doc, "arrested"), [(DOC, M)])
    run_contract(lambda doc: Fake(doc, None), [], empty_cases=[(DOC, M)])


def test_row_and_output():
    unit = unit_for(M, DOC)
    r = Fake(DOC, "arrested").process(unit, {Stage.extract: {"mentions": [M]}})
    row = r.rows["enrichments"][0]
    assert row["id"] == enrichment_id(unit.id, "fake", "1", None) and row["enricher"] == "fake"
    assert row["action"]["spans"] == [(7, 15)] and r.output["discarded"] == {"action": 0}


def test_base_rejects_bad_span():
    with pytest.raises(AdapterFailure) as e:
        Fake(DOC, bad=True).process(unit_for(M, DOC), {Stage.extract: {"mentions": [M]}})
    assert e.value.retryable is False


def test_unknown_mention_fails():
    other = make_mention(DOC, 0, 6)
    with pytest.raises(AdapterFailure) as e:
        Fake(DOC).process(unit_for(other, DOC), {Stage.extract: {"mentions": [M]}})
    assert e.value.retryable is False
