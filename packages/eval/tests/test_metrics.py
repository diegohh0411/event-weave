import json
import random
from pathlib import Path

import pytest

from weave_eval.metrics import Counts, ceafe_similarity, counts, scores

FIXTURES = Path(__file__).parent / "fixtures" / "reference"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
NAMES = sorted(EXPECTED)
METRICS = ("muc", "bcub", "ceafe", "blanc")


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())["documents"]


def corpus(documents, side="response"):
    total = Counts.zero()
    for d in documents:
        total += counts(d["key"], d[side])
    return scores(total)


def ours(s, metric):
    prf = {"muc": s.muc, "bcub": s.b3, "ceafe": s.ceafe, "blanc": s.blanc}[metric]
    return {"precision": prf.precision, "recall": prf.recall, "f1": prf.f1}


def test_gold_against_itself_is_100():
    docs = load("identical") + load("pradhan2014")
    s = corpus(docs, side="key")
    for m in METRICS:
        assert ours(s, m) == pytest.approx({"precision": 100, "recall": 100, "f1": 100}), m
    assert s.conll == pytest.approx(100)


@pytest.mark.parametrize("name", NAMES)
def test_matches_reference_scorer(name):
    s = corpus(load(name))
    for metric in METRICS:
        for k, ref in EXPECTED[name][metric].items():
            assert abs(ours(s, metric)[k] - ref) <= 0.01, (name, metric, k, ours(s, metric)[k], ref)


@pytest.mark.parametrize("name", NAMES)
def test_blanc_link_counts_match_reference(name):
    total = Counts.zero()
    for d in load(name):
        total += counts(d["key"], d["response"])
    ref = EXPECTED[name]["blanc_counts"]
    assert (total.blanc_cc, total.blanc_kc, total.blanc_rc, total.blanc_cn, total.blanc_kn, total.blanc_rn) == (
        ref["cc"], ref["kc"], ref["rc"], ref["cn"], ref["kn"], ref["rn"])


def test_counts_additive():
    d1, d2 = load("two_documents")
    summed = scores(counts(d1["key"], d1["response"]) + counts(d2["key"], d2["response"]))
    for metric in METRICS:
        for k, ref in EXPECTED["two_documents"][metric].items():
            assert abs(ours(summed, metric)[k] - ref) <= 0.01
    # BLANC non-coreference links do not span documents, so one merged clustering would differ
    merged = scores(counts({(1, m): c for m, c in d1["key"].items()} | {(2, m): c for m, c in d2["key"].items()},
                           {(1, m): c for m, c in d1["response"].items()} | {(2, m): c for m, c in d2["response"].items()}))
    assert merged.blanc != summed.blanc


def test_pradhan_documented_values():
    # values documented in the reference scorer's test suite (test/CorefMetricTestConfig.pm, case A4), as percents
    s = corpus(load("pradhan2014"))
    assert (s.muc.recall, s.muc.precision) == pytest.approx((100 / 3, 100 / 3))
    assert (s.b3.recall, s.b3.precision) == pytest.approx((100 * (3 + 1 / 3) / 6, 100 * (1 + 4 / 3 + 1 / 2) / 7))
    assert (s.ceafe.recall, s.ceafe.precision, s.ceafe.f1) == pytest.approx((73.333, 55.0, 62.857), abs=0.001)
    assert (s.blanc.recall, s.blanc.precision, s.blanc.f1) == pytest.approx((35.227, 27.206, 30.357), abs=0.001)


def test_ceafe_components_equal_full_matrix():
    rng = random.Random(7)
    mentions = range(200)
    key = {m: rng.randrange(40) for m in mentions}
    response = {m: rng.randrange(50) for m in mentions if rng.random() < 0.9} | {f"x{i}": rng.randrange(50) for i in range(10)}
    assert ceafe_similarity(key, response) == pytest.approx(ceafe_similarity(key, response, by_component=False))
    assert ceafe_similarity(key, response) > 0


def test_zero_denominators_are_zero():
    s = scores(counts({}, {}))
    for metric in METRICS:
        assert ours(s, metric) == {"precision": 0, "recall": 0, "f1": 0}
    assert s.conll == 0
    assert scores(Counts.zero()) == s
    only_resp = scores(counts({}, {"a": 1, "b": 1}))
    assert only_resp.b3.precision == 0 and only_resp.conll == 0
