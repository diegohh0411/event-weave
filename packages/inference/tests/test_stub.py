import math

from weave_core.stages import Capability

from weave_inference.clients.stub import StubClient, set_stub_script
from weave_inference.types import (Choice, ChatRequest, EmbeddingsRequest, ExtractionRequest, Noul, Score,
                                   SystemOneRequest)


def s1():
    return SystemOneRequest(state="x", questions=[Choice(id="c", prompt="p", options=["a", "b", "c"]),
                                                  Score(id="s", prompt="p", low=0, high=10),
                                                  Noul(id="n", prompt="p")])


def test_stub_is_deterministic():
    a, ua = StubClient().call(Capability.system_one, s1())
    b, _ = StubClient().call(Capability.system_one, s1())
    assert a == b and ua.reported_cost == 0
    by = {x.question_id: x for x in a.answers}
    assert by["c"].value in {"a", "b", "c"} and by["c"].probabilities[by["c"].value] == 1.0
    assert by["s"].value == 5.0 and "n" not in by
    chat, _ = StubClient().call(Capability.llm, ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert chat.text == "stub"
    ex, _ = StubClient().call(Capability.extraction, ExtractionRequest(text="t", fields={"a": 1}))
    assert ex.fields == {}


def test_stub_script_overrides_default():
    from weave_inference.clients.stub import StubClient as C
    c = C({"c": "b", "s": 7.5})
    r, _ = c.call(Capability.system_one, s1())
    by = {x.question_id: x.value for x in r.answers}
    assert by == {"c": "b", "s": 7.5}
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}])
    from weave_inference.ledger import request_hash
    h = request_hash(req.model_dump(mode="json"))
    r, _ = C({("llm", h): {"text": "canned"}}).call(Capability.llm, req)
    assert r.text == "canned"
    set_stub_script("p1", {("llm", h): {"text": "reg"}})
    assert C.for_provider("p1").call(Capability.llm, req)[0].text == "reg"


def test_stub_script_from_file(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("c: a\n")
    r, _ = StubClient.from_path(str(f)).call(Capability.system_one, s1())
    assert {x.question_id: x.value for x in r.answers}["c"] == "a"


def test_stub_embeddings_have_fixed_dim_and_unit_norm():
    r, u = StubClient().call(Capability.embeddings, EmbeddingsRequest(texts=["a", "b"]))
    r2, _ = StubClient().call(Capability.embeddings, EmbeddingsRequest(texts=["a", "b"]))
    assert r == r2 and len(r.vectors) == 2 and r.vectors[0] != r.vectors[1]
    for v in r.vectors:
        assert len(v) == 8 and math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-9)
