import pytest
from contract import run_contract
from helpers import ctx, make_doc, make_mention
from pydantic import ValidationError
from stanza_fixture import ARREST, ARREST_PARSE, parse, _W
from weave_core.stages import Capability
from weave_enrichers._shared import Entity, FixtureStanzaClient
from weave_enrichers.system_one import SystemOneEnricher, SystemOneSettings, clear_cache, find_trigger, proposals
from weave_enrichers.window import build_window
from weave_inference.clients import stub
from weave_inference.clients.stub import set_stub_script
from weave_inference.gateway import Inference
from weave_inference.types import Choice

ARR = (7, 15)
LOC = ARREST.index("Madrid")
MON = ARREST.index("Monday")
SEEN: list = []


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    clear_cache()
    SEEN.clear()
    original = stub.StubClient.call

    def spy(self, capability, request):
        SEEN.append(request)
        return original(self, capability, request)

    monkeypatch.setattr(stub.StubClient, "call", spy)
    yield
    clear_cache()


class S1(SystemOneEnricher):
    def __init__(self, doc, client, deployment="stub-s1", **settings):
        super().__init__(SystemOneSettings(**({"participant_threshold": 0.5, "stated_threshold": 0.5} | settings)), ctx())
        self.ctx.deployments[Capability.system_one] = deployment
        self._doc, self._client = doc, client

    def load_document(self, doc_id):
        return self._doc

    def client(self):
        return self._client


@pytest.fixture
def arrest(tmp_path):
    doc = make_doc(ARREST)
    client = FixtureStanzaClient(tmp_path)
    client.save(ARREST, "en", ARREST_PARSE, ner=True)
    return doc, make_mention(doc, *ARR), client


def run(arrest, **settings):
    doc, m, client = arrest
    return S1(doc, client, **settings).enrich(doc, m, build_window(doc, m, 1))


def ids():
    return [q.id for r in SEEN for q in r.questions]


def test_proposals_from_parse():
    sent = ARREST_PARSE
    p = proposals(ARREST, sent, find_trigger(sent, ARR))
    texts = lambda spans: {ARREST[s:e] for s, e in spans}  # noqa: E731
    assert texts(p.participants) >= {"Police", "two men"}
    assert texts(p.locations) == {"Madrid"} and texts(p.times) == {"Monday"}
    assert ARREST[slice(*p.action_span)] == "arrested" and p.misaligned == 0


def test_phrase_stays_contiguous_and_other_labels_map_to_nothing():
    sent = parse(ARREST, _W, [("Police", "ORG"), ("Madrid", "WORK_OF_ART")])
    p = proposals(ARREST, sent, find_trigger(sent, ARR))
    assert {ARREST[s:e] for s, e in p.locations} == {"Madrid"}  # still found, from the obl phrase
    assert (ARREST.index("Madrid"), ARREST.index("Madrid") + 6) not in p.participants


def test_misaligned_entities_counted_not_kept():
    sent = [ARREST_PARSE[0].model_copy(deep=True)]
    sent[0].entities.append(Entity(start=0, end=6, type="PERSON", text="Policia"))
    p = proposals(ARREST, sent, find_trigger(sent, ARR))
    assert p.misaligned == 1 and (0, 6) in p.participants  # the good ORG entity is the same span


def test_action_takes_particles_and_passive_auxiliaries():
    text = "The march was broken up."
    words = [("The", "DET", "the", "det", 2), ("march", "NOUN", "march", "nsubj:pass", 4), ("was", "AUX", "be", "aux:pass", 4),
             ("broken", "VERB", "break", "root", 0), ("up", "ADP", "up", "compound:prt", 4), (".", "PUNCT", ".", "punct", 4)]
    sent = parse(text, words)
    p = proposals(text, sent, find_trigger(sent, (14, 20)))
    assert text[slice(*p.action_span)] == "was broken up"
    assert {text[s:e] for s, e in p.participants} == {"The march"}


def test_no_action_call(stub_inference, arrest):
    set_stub_script("stub", {"p0": 0.9, "p1": 0.9, "loc_stated": 0.9, "time_stated": 0.9})
    enrichment, _ = run(arrest)
    assert enrichment.action.text == "arrested" and enrichment.action.confidence is None
    assert not [i for i in ids() if i.startswith("action")]


def test_fields_filled_and_confidences(stub_inference, arrest):
    set_stub_script("stub", {"p0": 0.9, "p1": 0.2, "loc_stated": 0.8, "time_stated": 0.7})
    enrichment, discarded = run(arrest)
    assert enrichment.participants.text == "Police" and enrichment.participants.confidence == 0.9
    assert enrichment.location.text == "Madrid" and enrichment.location.spans == [(LOC, LOC + 6)]
    assert enrichment.time.text == "Monday" and enrichment.time.normalized is None
    assert discarded == {"misaligned": 0}
    assert not [r for r in SEEN for q in r.questions if isinstance(q, Choice)]  # one candidate each: no Choice


def test_normalized_always_none(stub_inference, arrest):
    set_stub_script("stub", {"time_stated": 1.0})
    assert run(arrest)[0].time.normalized is None


def test_stated_below_threshold_empties_field(stub_inference, arrest):
    set_stub_script("stub", {"loc_stated": 0.3, "time_stated": 0.4})
    enrichment, _ = run(arrest, stated_threshold=0.5)
    assert enrichment.location.text is None and enrichment.location.spans == []
    assert enrichment.time.text is None and enrichment.time.confidence is None


def test_no_candidates_no_questions(stub_inference, tmp_path):
    text = "Police arrested two men."
    words = [w for w in _W[:4]] + [(".", "PUNCT", ".", "punct", 2)]
    client = FixtureStanzaClient(tmp_path)
    client.save(text, "en", parse(text, words), ner=True)
    doc, m = make_doc(text), None
    m = make_mention(doc, 7, 15)
    enrichment, _ = S1(doc, client).enrich(doc, m, build_window(doc, m, 1))
    assert enrichment.location.text is None and enrichment.time.text is None
    assert not [i for i in ids() if i.startswith(("loc", "time"))]


def seven_places(tmp_path, names=("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta")):
    names = list(names)
    text = "Police arrested two men in " + ", ".join(names) + "."
    words = [w for w in _W[:4]] + [(n, "PROPN", n, "obl" if i == 0 else "conj", 2) for i, n in enumerate(names)] + [(".", "PUNCT", ".", "punct", 2)]
    client = FixtureStanzaClient(tmp_path)
    client.save(text, "en", parse(text, words, [(n, "GPE") for n in names]), ner=True)
    doc = make_doc(text)
    return doc, make_mention(doc, 7, 15), client, names


def test_choice_never_exceeds_limit(stub_inference, tmp_path):
    doc, m, client, names = seven_places(tmp_path)
    set_stub_script("stub", {"loc_stated": 0.9,
                             **{f"loc_r0_{j}": {"question_id": f"loc_r0_{j}", "value": names[[0, 3, 5][j]], "probabilities": {names[[0, 3, 5][j]]: 0.6}}
                                for j in range(2)},
                             "loc": {"question_id": "loc", "value": names[3], "probabilities": {names[3]: 0.8}}})
    # 7 options in chunks of 3,3,1: the lone Eta needs no Choice; winners (Alpha, Delta) and Eta feed the final
    enrichment, _ = S1(doc, client, deployment="stub-s1-3").enrich(doc, m, build_window(doc, m, 1))
    choices = [q for r in SEEN for q in r.questions if isinstance(q, Choice)]
    assert choices and all(2 <= len(c.options) <= 3 for c in choices)
    assert [c.id for c in choices][-1] == "loc" and choices[-1].options == ["Alpha", "Delta", "Eta"]
    assert enrichment.location.text == "Delta" and enrichment.location.confidence == 0.8


def test_odd_candidates_never_single_option_choice(stub_inference, tmp_path):
    doc, m, client, names = seven_places(tmp_path, ("Alpha", "Beta", "Gamma"))
    Inference._catalog.deployments["stub-s1-3"].limits.max_choice_options = 2
    set_stub_script("stub", {"loc_stated": 0.9,
                             "loc_r0_0": {"question_id": "loc_r0_0", "value": "Beta", "probabilities": {"Beta": 0.7}},
                             "loc": {"question_id": "loc", "value": "Gamma", "probabilities": {"Gamma": 0.6}}})
    enrichment, _ = S1(doc, client, deployment="stub-s1-3").enrich(doc, m, build_window(doc, m, 1))
    choices = [q for r in SEEN for q in r.questions if isinstance(q, Choice)]
    assert [len(c.options) for c in choices] == [2, 2]
    assert choices[-1].options == ["Beta", "Gamma"]
    assert enrichment.location.text == "Gamma" and enrichment.location.confidence == 0.6


def test_joint_one_request_per_mention(stub_inference, arrest):
    set_stub_script("stub", {"p0": 0.9, "p1": 0.9, "loc_stated": 0.9, "time_stated": 0.9})
    run(arrest, deployment="stub-s1-joint")
    assert len(SEEN) == 1 and len(SEEN[0].questions) == 4


def test_splits_on_limit(stub_inference, arrest):
    set_stub_script("stub", {"p0": 0.9, "p1": 0.9, "loc_stated": 0.9, "time_stated": 0.9})
    run(arrest)
    stub_inference(context_tokens=len(SEEN[0].text_for_context.encode()) // 4 - 5)
    SEEN.clear()
    enrichment, _ = run(arrest, deployment="stub-s1-small")
    assert len(SEEN) >= 2 and sum(len(r.questions) for r in SEEN) == 4
    assert enrichment.location.text == "Madrid" and enrichment.participants.text == "Police; two men"


def test_thresholds_required():
    with pytest.raises(ValidationError):
        SystemOneSettings()
    with pytest.raises(ValidationError):
        SystemOneSettings(participant_threshold=0.5)
    with pytest.raises(ValidationError):
        SystemOneSettings(participant_threshold=0.5, stated_threshold=1.5)
    assert SystemOneSettings(participant_threshold=0.5, stated_threshold=0.5).window_sentences == 1


def test_contract(stub_inference, arrest):
    doc, m, client = arrest
    set_stub_script("stub", {"p0": 0.9, "p1": 0.9, "loc_stated": 0.9, "time_stated": 0.9})
    run_contract(lambda d: S1(d, client), [(doc, m)])
    # no empty case: the action is built in code, so an enrichment is never entirely empty


def test_same_document_parses_once_concurrently(arrest):
    import threading
    doc, m, client = arrest
    calls, barrier = [], threading.Barrier(4, timeout=5)

    class Counting:
        def parse(self, text, lang, ner=False):
            calls.append(1)
            import time
            time.sleep(0.2)
            return client.parse(text, lang, ner)

    def go():
        barrier.wait()
        S1(doc, Counting()).parse(doc)

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert len(calls) == 1


def test_choice_limit_of_one_fails(stub_inference, tmp_path, db):
    from weave_core.adapters import AdapterFailure
    from weave_inference.gateway import Inference
    doc, m, client, _ = seven_places(tmp_path)
    Inference._catalog.deployments["stub-s1-3"].limits.max_choice_options = 1
    set_stub_script("stub", {"loc_stated": 0.9})
    with pytest.raises(AdapterFailure) as exc:
        S1(doc, client, deployment="stub-s1-3").enrich(doc, m, build_window(doc, m, 1))
    assert exc.value.retryable is False


def test_repeated_text_picks_occurrence_nearest_trigger(stub_inference, tmp_path):
    text = "Madrid reacted. Police arrested two men in Madrid."
    t = text.index("arrested")
    words = [("Madrid", "PROPN", "Madrid", "nsubj", 2), ("reacted", "VERB", "react", "root", 0), (".", "PUNCT", ".", "punct", 2)]
    sents = parse(text[:15], words)
    # one sentence for simplicity: all words as a single dependency tree around "arrested"
    w2 = [("Madrid", "PROPN", "Madrid", "dep", 4), ("reacted", "VERB", "react", "dep", 4), (".", "PUNCT", ".", "dep", 4),
          ("Police", "NOUN", "police", "nsubj", 4), ("arrested", "VERB", "arrest", "root", 0), ("in", "ADP", "in", "case", 7),
          ("Madrid", "PROPN", "Madrid", "obl", 5), (".", "PUNCT", ".", "punct", 5)]
    client = FixtureStanzaClient(tmp_path)
    client.save(text, "en", parse(text, w2, [("Madrid", "GPE")]) , ner=True)
    sent = client.parse(text, "en", ner=True)
    sent[0].entities.append(Entity(start=text.rindex("Madrid"), end=text.rindex("Madrid") + 6, type="GPE", text="Madrid"))
    client.save(text, "en", sent, ner=True)
    doc = make_doc(text)
    m = make_mention(doc, t, t + 8, sentence=1)
    set_stub_script("stub", {"loc_stated": 0.9})
    enrichment, _ = S1(doc, client).enrich(doc, m, build_window(doc, m, 1))
    assert enrichment.location.spans == [(text.rindex("Madrid"), text.rindex("Madrid") + 6)]
