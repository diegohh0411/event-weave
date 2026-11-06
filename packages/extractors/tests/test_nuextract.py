import json

from contract import run_contract
from ext_helpers import ctx, make_doc, unit
from weave_extractors.nuextract import NuExtract3Extractor, NuExtractSettings, locate, windows
from weave_inference.clients.stub import set_stub_script
from weave_inference.ledger import request_hash

TEXT = "Police said the protest ended. He said the attack failed. Talks resumed."


class Nu(NuExtract3Extractor):
    def __init__(self, doc, **settings):
        super().__init__(NuExtractSettings(**settings), ctx(llm="stub-llm"))
        self._doc = doc

    def load_document(self, doc_id):
        return self._doc


def script(doc, replies, **settings):
    """replies: one raw text reply per window."""
    nu = Nu(doc, **settings)
    entries = {}
    for (start, end), reply in zip(windows(doc, nu.settings.window_sentences), replies):
        req = nu.build_request(doc.text[start:end])
        entries[("llm", request_hash(req.model_dump(mode="json")))] = {"text": reply}
    set_stub_script("stub", entries)
    return nu


def test_locate_left_to_right_and_counts_unlocated():
    assert locate("he said and said", ["said", "said", "told"]) == ([(3, 7), (12, 16)], 1)


def test_windows_group_sentences():
    doc = make_doc(TEXT)
    assert windows(doc, 2) == [(0, 57), (58, 72)]


def test_triggers_located_in_document(stub_inference):
    doc = make_doc(TEXT)
    nu = script(doc, [json.dumps({"event_triggers": ["said", "protest", "said", "exploded"]}),
                      json.dumps({"event_triggers": ["resumed"]})], window_sentences=2)
    result = nu.process(unit(doc), {})
    assert [(m["start"], m["text"]) for m in result.output["mentions"]] == [
        (7, "said"), (16, "protest"), (34, "said"), (64, "resumed")]
    assert result.output["unlocated"] == 1 and result.output["unlocated_windows"] == 0


def test_non_json_reply_counts_window(stub_inference):
    doc = make_doc(TEXT)
    nu = script(doc, ["sorry, I can't", json.dumps({"event_triggers": ["resumed"]})], window_sentences=2)
    result = nu.process(unit(doc), {})
    assert [m["text"] for m in result.output["mentions"]] == ["resumed"]
    assert result.output["unlocated_windows"] == 1


def test_nuextract_contract(stub_inference):
    doc = make_doc(TEXT)
    script(doc, [json.dumps({"event_triggers": ["said", "attack"]})], window_sentences=3)
    run_contract(lambda d: Nu(d, window_sentences=3), [doc])
