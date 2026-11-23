import threading

import pytest
from fastapi.testclient import TestClient
from weave_enrichers.clairic_service import create_app, to_clairic, to_spans

TEXT = "[m0]\nPolice ⟦arrested⟧ two men in Lyon. Police said so."


def test_each_field_becomes_a_list_of_text():
    req = to_clairic(TEXT, {"m0_action": "The action."})
    assert req == {"state": TEXT, "fields": {"m0_action": {"type": "list", "items": "text",
                                                            "instructions": "The action."}}}


def test_spans_come_from_the_model_or_a_unique_match():
    answer = {"type": "list", "value": ["arrested", "Lyon", "Police", "Paris"], "confidence": 0.8,
              "spans": [[13, 21], None, None, None]}
    spans = to_spans(TEXT, answer)
    # model span kept; "Lyon" found once; "Police" twice: every occurrence, for the caller to choose; "Paris" absent: dropped
    police = [i for i in range(len(TEXT)) if TEXT.startswith("Police", i)]
    assert spans == [{"text": "arrested", "start": 13, "end": 21, "confidence": 0.8},
                     {"text": "Lyon", "start": TEXT.index("Lyon"), "end": TEXT.index("Lyon") + 4, "confidence": 0.8},
                     *({"text": "Police", "start": i, "end": i + 6, "confidence": 0.8} for i in police)]
    assert len(police) == 2


def test_per_item_confidence_wins():
    answer = {"value": ["arrested", "Lyon"], "confidence": 0.5, "confidences": [0.9, 0.7], "spans": [[13, 21], None]}
    assert [s["confidence"] for s in to_spans(TEXT, answer)] == [0.9, 0.7]


def test_span_that_does_not_quote_the_text_is_dropped():
    assert to_spans(TEXT, {"value": ["arrested"], "confidence": 0.5, "spans": [[0, 8]]}) == []


def test_null_or_invalid_answer_has_no_spans():
    assert to_spans(TEXT, {"value": None, "confidence": 0.0}) == []
    assert to_spans(TEXT, {"value": ["x"], "valid": False}) == []


class FakeModel:
    def __init__(self, answers, truncated=False, gate=None):
        self.answers, self.truncated, self.gate, self.seen = answers, truncated, gate, []

    def predict(self, request):
        self.seen.append(request)
        if self.gate:
            self.gate.wait(5)
        return {"model": "clairic-flash", "answers": self.answers, "truncated": self.truncated,
                "usage": {"input_tokens": 40, "output_tokens": 6}}


def test_extract_round_trip():
    model = FakeModel({"m0_action": {"type": "list", "value": ["arrested"], "confidence": 0.9, "spans": [[13, 21]]}})
    client = TestClient(create_app(lambda: model, info={"model": "clairic-flash"}))
    r = client.post("/extract", json={"text": TEXT, "fields": {"m0_action": "The action."}})
    assert r.status_code == 200
    assert r.json() == {"fields": {"m0_action": [{"text": "arrested", "start": 13, "end": 21, "confidence": 0.9}]},
                        "usage": {"input_tokens": 40, "output_tokens": 6}}
    assert model.seen[0]["fields"]["m0_action"]["type"] == "list"
    assert client.get("/health").json() == {"status": "ok", "loaded": True, "model": "clairic-flash"}


def test_truncated_input_is_an_error():
    client = TestClient(create_app(lambda: FakeModel({}, truncated=True)))
    r = client.post("/extract", json={"text": TEXT, "fields": {"a": "x"}})
    assert r.status_code == 422 and "truncated" in r.json()["detail"]


def test_second_request_while_busy_gets_503():
    gate = threading.Event()
    app = create_app(lambda: FakeModel({"a": {"value": None}}, gate=gate))
    client = TestClient(app)
    first = threading.Thread(target=lambda: client.post("/extract", json={"text": TEXT, "fields": {"a": "x"}}))
    first.start()
    try:
        for _ in range(100):
            if app.state.busy.locked():
                break
            threading.Event().wait(0.01)
        r = client.post("/extract", json={"text": TEXT, "fields": {"a": "x"}})
        assert r.status_code == 503
    finally:
        gate.set()
        first.join()


@pytest.mark.parametrize("body", [{"text": "", "fields": {"a": "x"}}, {"text": TEXT, "fields": {}}])
def test_bad_request_is_422(body):
    client = TestClient(create_app(lambda: FakeModel({})))
    assert client.post("/extract", json=body).status_code == 422
