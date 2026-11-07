from weave_eval.compare import NO_PAIRED_TEST, ScoreRow, compare
from weave_eval.metrics import PRF
from weave_eval.paper import load_paper
from weave_eval.tracks import SKIPPED

METRICS = ("muc", "b3", "ceafe", "blanc", "conll")
TRANSFER = "like for like (both tuned on English only)"
MONO = "our setting is harder (we are tuned on English dev only)"


def fixture_scores():
    rows = []
    for lang in ("en", "es", "hi"):
        for setting in ("within", "cross"):
            if (lang, setting) in SKIPPED:
                continue
            for i, m in enumerate(METRICS):
                v = 11.0 + i + (0.5 if setting == "cross" else 0)  # never equals a paper value
                rows.append(ScoreRow("paper", setting, lang, m, v + 0.1, v + 0.2, v, v - 1, v + 1))
    rows.append(ScoreRow("realistic", "pooled", "all", "muc", 1, 1, 1, None, None))  # Track 2: ignored
    return rows


def find(rows, **kw):
    out = [r for r in rows if all(getattr(r, k) == v for k, v in kw.items())]
    assert len(out) == 1, (kw, out)
    return out[0]


def test_compare_never_mixes():
    paper, scores = load_paper(), fixture_scores()
    rows = compare(scores, paper)
    by_key = {(s.language, s.setting, s.metric): s for s in scores if s.track == "paper"}
    for r in rows:
        s = by_key.get((r.language, r.setting, r.metric))
        if s is None:
            assert r.ours is None and r.ours_ci is None
        else:
            assert r.ours == PRF(s.precision, s.recall, s.f1)
            assert r.ours_ci == (s.f1_low, s.f1_high)
        table = next(t for t in paper.tables if t.setting == r.setting and t.regime == r.regime)
        for model, value in r.paper.items():
            src = next(x for x in table.rows if x["language"] == r.language and x["model"] == model)
            assert value == src[r.metric]["f1"]
            assert r.metric != "conll"
        if r.metric == "conll":
            assert r.paper == {}
    # no Track 2 row leaks in, and every ours f1 differs from every paper f1
    assert all(r.setting in ("within", "cross") for r in rows)
    assert find(rows, language="es", setting="within", metric="muc", regime="monolingual").paper == {
        "synonym": 15.2, "xlmr": 85.0, "hierarchical": 76.2, "joint": 70.1}
    assert find(rows, language="es", setting="within", metric="muc", regime="monolingual").ours.f1 == 11.0
    assert find(rows, language="es", setting="within", metric="muc", regime="transfer").paper == {
        "xlmr": 68.2, "hierarchical": 60.2, "joint": 66.5}


def test_labels_by_regime():
    rows = compare(fixture_scores(), load_paper())
    assert {r.label for r in rows if r.regime == "transfer"} == {TRANSFER}
    assert {r.label for r in rows if r.regime == "monolingual"} == {MONO}
    assert {r.regime for r in rows} == {"transfer", "monolingual"}
    assert not [r for r in rows if r.language == "en" and r.regime == "transfer"]


def test_hindi_cross_skipped():
    rows = compare(fixture_scores(), load_paper())
    reason = SKIPPED[("hi", "cross")]
    for regime in ("monolingual", "transfer"):
        r = find(rows, language="hi", setting="cross", metric="muc", regime=regime)
        assert r.paper and r.ours is None and r.ours_ci is None and r.skipped == reason
    ok = find(rows, language="hi", setting="within", metric="muc", regime="monolingual")
    assert ok.skipped is None and ok.ours is not None


def test_paper_only_rows_have_none_ours():
    rows = compare([], load_paper())
    r = find(rows, language="uk", setting="cross", metric="blanc", regime="monolingual")
    assert r.ours is None and r.skipped is None and r.paper["joint"] == 70.6


def test_no_paired_test_sentence():
    assert NO_PAIRED_TEST.startswith("The paper published no system outputs")
