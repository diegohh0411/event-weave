import pytest
import sqlalchemy as sa
from contract import run_contract
from helpers import ctx, make_doc, make_mention
from weave_core.schema import ledger
from weave_core.stages import Capability
from weave_enrichers.clairic import FIELD_INSTRUCTIONS, ClairicEnricher, ClairicSettings, clear_cache
from weave_inference.catalog import Catalog
from weave_inference.clients.stub import clear_stub_scripts, set_stub_script
from weave_inference.gateway import Inference
from weave_inference.ledger import request_hash
from weave_inference.probe import Readiness
from weave_inference.types import Deployment, Limits, Model, Provider

TEXT = "Police arrested two men on Monday. Police said the attack failed. Talks resumed."


@pytest.fixture
def clairic(db):
    def make(max_fields=None):
        catalog = Catalog(
            models={"m-x": Model(id="m-x", capability=Capability.extraction)},
            providers={"stub": Provider(id="stub", kind="stub", base_url="stub://")},
            deployments={"stub-x": Deployment(id="stub-x", model="m-x", provider="stub",
                                              limits=Limits(max_fields=max_fields))})
        catalog.readiness = {"stub-x": Readiness(ready=True)}
        Inference.configure(catalog, db)

    clear_cache()
    make()
    yield make
    clear_stub_scripts()
    clear_cache()


class C(ClairicEnricher):
    def __init__(self, doc, **settings):
        super().__init__(ClairicSettings(**settings), ctx())
        self.ctx.deployments[Capability.extraction] = "stub-x"
        self.doc = doc

    def load_document(self, doc_id):
        return self.doc


def sp(text, start, conf=0.9):
    return {"text": text, "start": start, "end": start + len(text), "confidence": conf}


def script(enricher, doc, mentions, replies):
    """replies: one {field: [spans]} per request, spans given as (text, start-in-request) or dicts."""
    entries, i = {}, 0
    per = enricher.per_request() or len(mentions)
    for n in range(0, len(mentions), per):
        batch = mentions[n:n + per]
        windows = [__import__("weave_enrichers.window", fromlist=["x"]).build_window(doc, m, 1) for m in batch]
        req, _ = enricher.build_request(doc, windows)
        entries[("extraction", request_hash(req.model_dump(mode="json")))] = {"fields": replies[i]}
        i += 1
    set_stub_script("stub", entries)


def at(request_text, needle, nth=0):
    pos = -1
    for _ in range(nth + 1):
        pos = request_text.index(needle, pos + 1)
    return pos


def request_of(enricher, doc, mentions):
    from weave_enrichers.window import build_window
    return enricher.build_request(doc, [build_window(doc, m, 1) for m in mentions])


def test_discards_text_not_in_document(clairic):
    doc = make_doc(TEXT)
    m = make_mention(doc, 7, 15)
    e = C(doc)
    req, _ = request_of(e, doc, [m])
    script(e, doc, [m], [{"m0_participants": [sp("Police", at(req.text, "Police")), sp("Police force", at(req.text, "Police"))],
                          "m0_action": [sp("arrested", at(req.text, "⟦") + 1)]}])
    enrichment, discarded = e.enrich_document(doc, [m])[next(iter(e.enrich_document(doc, [m])))]
    assert enrichment.participants.spans == [(0, 6)] and enrichment.participants.text == "Police"
    assert discarded["participants"] == 1 and discarded["action"] == 0
    assert enrichment.action.spans == [(7, 15)] and enrichment.time.normalized is None


def test_marker_offsets_removed(clairic):
    doc = make_doc(TEXT)
    m = make_mention(doc, 7, 15)
    e = C(doc)
    req, _ = request_of(e, doc, [m])
    # "Monday" lies after the ⟦ ⟧ markers in the request text, so its request offset is 2 past its document offset
    # plus the header and section label.
    assert req.text.index("Monday") - TEXT.index("Monday") > 2
    script(e, doc, [m], [{"m0_time": [sp("Monday", at(req.text, "Monday"))]}])
    enrichment, _ = next(iter(e.enrich_document(doc, [m]).values()))
    assert enrichment.time.spans == [(TEXT.index("Monday"),) * 1 + (TEXT.index("Monday") + 6,)]


def test_span_from_other_section_discarded(clairic):
    doc = make_doc(TEXT)
    ms = [make_mention(doc, 7, 15), make_mention(doc, TEXT.index("resumed"), TEXT.index("resumed") + 7, sentence=2)]
    e = C(doc)
    req, sections = request_of(e, doc, ms)
    other = at(req.text, "resumed")  # lies in section m1, reported for m0
    script(e, doc, ms, [{"m0_location": [sp("resumed", other)], "m1_location": [sp("resumed", other)]}])
    out = list(e.enrich_document(doc, ms).values())
    assert out[0][0].location.spans == [] and out[0][1]["location"] == 1
    assert out[1][0].location.text == "resumed" and out[1][1]["location"] == 0


def test_best_single_span_and_participants_all(clairic):
    doc = make_doc(TEXT)
    m = make_mention(doc, 7, 15)
    e = C(doc)
    req, _ = request_of(e, doc, [m])
    script(e, doc, [m], [{"m0_location": [sp("men", at(req.text, "men"), 0.2), sp("Monday", at(req.text, "Monday"), 0.8)],
                          "m0_participants": [sp("men", at(req.text, "men"), 0.3), sp("Police", at(req.text, "Police"), 0.6)]}])
    enrichment, _ = next(iter(e.enrich_document(doc, [m]).values()))
    assert enrichment.location.text == "Monday" and enrichment.location.confidence == 0.8
    assert enrichment.participants.text == "Police; men" and enrichment.participants.confidence == 0.6


def test_batches_by_field_limit(clairic, db):
    clairic(max_fields=8)
    doc = make_doc(TEXT)
    ms = [make_mention(doc, 7, 15), make_mention(doc, 59, 65, sentence=1), make_mention(doc, 70, 77, sentence=2)]
    e = C(doc)
    script(e, doc, ms, [{}, {}])
    out = e.enrich_document(doc, ms)
    assert len(out) == 3
    again = e.enrich_document(doc, ms)  # served from the cache
    assert again == out
    with db.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(ledger)) == 2


def test_instructions_cover_every_field():
    assert set(FIELD_INSTRUCTIONS) == {"action", "participants", "location", "time"}
    assert all("⟦like this⟧" in v and "[m{i}]" in v for v in FIELD_INSTRUCTIONS.values())


def test_contract(clairic):
    doc = make_doc(TEXT)
    m = make_mention(doc, 7, 15)
    e = C(doc)
    req, _ = request_of(e, doc, [m])
    script(e, doc, [m], [{"m0_action": [sp("arrested", at(req.text, "⟦") + 1)], "m0_time": [sp("Monday", at(req.text, "Monday"))]}])
    run_contract(lambda d: C(d), [(doc, m)])
    clear_cache()
    set_stub_script("stub", {})
    run_contract(lambda d: C(d), [], empty_cases=[(doc, m)])
