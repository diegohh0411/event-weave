import json
from datetime import datetime

from contract import run_contract
from helpers import ctx, make_doc, make_mention
from weave_core.stages import Capability
from weave_enrichers.nuextract import TEMPLATE, NuExtract3Enricher, NuExtractSettings
from weave_enrichers.window import build_window
from weave_inference.clients.stub import set_stub_script
from weave_inference.ledger import request_hash

TEXT = "Police arrested two men on Monday. Police said the attack failed on Monday. Talks resumed."
PUB = datetime(2024, 5, 6)


class Nu(NuExtract3Enricher):
    def __init__(self, doc, **settings):
        super().__init__(NuExtractSettings(**settings), ctx())
        self.ctx.deployments[Capability.llm] = "stub-llm"
        self.doc = doc

    def load_document(self, doc_id):
        return self.doc


def setup(doc, mention, reply):
    nu = Nu(doc)
    req = nu.build_request(build_window(doc, mention, 1))
    set_stub_script("stub", {("llm", request_hash(req.model_dump(mode="json"))): {"text": reply}})
    return nu


def run(doc, mention, reply):
    nu = setup(doc, mention, reply)
    return nu.enrich(doc, mention, build_window(doc, mention, 1))


def test_fields_located_nearest_trigger(stub_inference):
    doc = make_doc(TEXT, published_at=PUB)
    s = TEXT.index("said")
    m = make_mention(doc, s, s + 4, sentence=1)
    reply = json.dumps({"action": "said", "participants": ["Police", "Talks"], "location": None,
                        "time": "Monday", "time_normalized": "2024-05-06"})
    enrichment, discarded = run(doc, m, reply)
    assert enrichment.time.spans == [(TEXT.index("Monday"), TEXT.index("Monday") + 6)]
    assert enrichment.time.normalized == "2024-05-06" and enrichment.time.confidence is None
    assert enrichment.participants.spans[0] == (TEXT.index("Police", 10), TEXT.index("Police", 10) + 6)  # the second "Police", nearest the trigger
    assert enrichment.location.text is None and discarded == {}


def test_non_json_counted(stub_inference):
    doc = make_doc(TEXT)
    m = make_mention(doc, 7, 15)
    enrichment, discarded = run(doc, m, "sorry")
    assert enrichment.action.text is None and discarded == {"unparsed": 1}


def test_bad_values_discarded_and_counted(stub_inference):
    doc = make_doc(TEXT, published_at=PUB)
    m = make_mention(doc, 7, 15)
    reply = json.dumps({"action": "arrested", "participants": ["police"], "time": "Monday",
                        "time_normalized": "2024-13-40"})
    enrichment, discarded = run(doc, m, reply)
    assert enrichment.time.normalized is None and enrichment.time.text == "Monday"
    assert discarded == {"participants": 1}


def test_no_date_no_published_line(stub_inference):
    doc = make_doc(TEXT)
    m = make_mention(doc, 7, 15)
    request = Nu(doc).build_request(build_window(doc, m, 1))
    content = request.messages[0]["content"]
    assert "Published:" not in content and "⟦arrested⟧" in content
    assert request.template_args == {"template": TEMPLATE, "enable_thinking": False}
    with_date = make_doc(TEXT, published_at=PUB)
    assert "Published: 2024-05-06" in Nu(with_date).build_request(build_window(with_date, m, 1)).messages[0]["content"]


def test_relative_time_without_date_not_normalized(stub_inference):
    doc = make_doc(TEXT)
    m = make_mention(doc, 7, 15)
    enrichment, _ = run(doc, m, json.dumps({"time": "Monday", "time_normalized": "2024-05-06"}))
    assert enrichment.time.text == "Monday" and enrichment.time.normalized is None


def test_contract(stub_inference):
    doc = make_doc(TEXT, published_at=PUB)
    m = make_mention(doc, 7, 15)

    def factory(d):
        return setup(d, m, json.dumps({"action": "arrested", "participants": ["Police"], "time": "Monday",
                                       "time_normalized": "2024-05-06"}))

    run_contract(factory, [(doc, m)])
    run_contract(lambda d: setup(d, m, json.dumps({})), [], empty_cases=[(doc, m)])
