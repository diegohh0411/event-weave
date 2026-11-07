import random

import numpy as np

from weave_eval.bootstrap import draw, interval_additive, interval_pooled
from weave_eval.metrics import METRICS, Counts, counts, scores, score_matrix, to_matrix
from weave_eval.tracks import Track2Inputs


def random_inputs(seed=0, topics=6, langs=("en", "es"), perfect=False):
    rnd = random.Random(seed)
    inp = Track2Inputs()
    for lang in langs:
        for t in range(topics):  # topic ids repeat across languages
            for i in range(rnd.randint(4, 9)):
                kid = (lang, t, i)
                gold = f"{lang}{t}-g{rnd.randint(0, 3)}"
                inp.key[kid] = gold
                inp.group[kid] = (lang, str(t))
                inp.response[kid] = gold if perfect else f"{lang}{t}-s{rnd.randint(0, 3)}"
            if not perfect:  # a spurious system mention belongs to its topic
                inp.response[(lang, t, "sp")] = f"{lang}{t}-s0"
                inp.group[(lang, t, "sp")] = (lang, str(t))
    return inp


def additive_rows(seed=0):
    inp = random_inputs(seed)
    rows = {}
    for u in sorted(set(inp.group.values())):
        ids = [i for i, g in inp.group.items() if g == u]
        rows[u] = counts({i: inp.key[i] for i in ids if i in inp.key}, {i: inp.response[i] for i in ids})
    return rows


def test_draw_sorts_units_and_is_seeded():
    a = draw(["b", "a", "c"], 50, 1)
    assert a.shape == (50, 3) and (draw(["c", "b", "a"], 50, 1) == a).all()
    assert not (draw(["a", "b", "c"], 50, 2) == a).all()


def test_same_seed_same_intervals():
    inp = random_inputs()
    a = interval_pooled(inp, "en", 40, 3, workers=1)
    assert a == interval_pooled(inp, "en", 40, 3, workers=1)
    assert a == interval_pooled(inp, "en", 40, 3, workers=4)
    rows = additive_rows()
    assert interval_additive(rows, 200, 3) == interval_additive(rows, 200, 3)
    assert set(a) == set(METRICS)


def test_different_seed_differs():
    rows = additive_rows()
    assert interval_additive(rows, 200, 1) != interval_additive(rows, 200, 2)


def test_perfect_system_interval_is_100_100():
    inp = random_inputs(perfect=True)
    for m, iv in interval_pooled(inp, None, 30, 0, workers=2).items():
        assert iv == (100.0, 100.0), m
    rows = {u: counts(*[{i: d[i] for i in inp.key if inp.group[i] == u} for d in (inp.key, inp.response)])
            for u in set(inp.group.values())}
    assert all(iv == (100.0, 100.0) for iv in interval_additive(rows, 100, 0).values())


def test_interval_contains_point_estimate():
    rows = additive_rows(1)
    point = scores(sum(rows.values(), Counts.zero()))
    iv = interval_additive(rows, 500, 7)
    for m in METRICS:
        v = point.conll if m == "conll" else getattr(point, m).f1
        assert iv[m][0] <= v <= iv[m][1], m
    assert iv["conll"][0] < iv["conll"][1]


def test_additive_f1_matches_scores_of_sum():
    rows = additive_rows(2)
    units = sorted(rows)
    mat = to_matrix([rows[u] for u in units])
    idx = draw(units, 3, 5)
    sums = np.stack([mat[i].sum(axis=0) for i in idx])
    got = score_matrix(sums)
    for r, i in enumerate(idx):
        s = scores(sum((rows[units[j]] for j in i), Counts.zero()))
        assert got["muc"][2][r] == s.muc.f1 and got["blanc"][2][r] == s.blanc.f1
        assert got["conll"][r] == s.conll


def test_units_never_mix_languages():
    inp = random_inputs()
    en = interval_pooled(inp, "en", 30, 0, workers=1)
    only_en = Track2Inputs(
        key={k: v for k, v in inp.key.items() if k[0] == "en"},
        response={k: v for k, v in inp.response.items() if k[0] == "en"},
        group={k: v for k, v in inp.group.items() if k[0] == "en"})
    assert en == interval_pooled(only_en, None, 30, 0, workers=1)
    # topic id "0" exists in en and es: es-only change must not move the en interval
    inp.response[("es", 0, 0)] = "other"
    assert en == interval_pooled(inp, "en", 30, 0, workers=1)


def test_pooled_duplicate_topic_does_not_self_merge():
    # one topic only, so every resample is [t, t, ...]; its two clusters must not merge across occurrences
    one = Track2Inputs(key={"a": "g", "b": "g", "c": "h", "d": "h"}, response={"a": "s", "b": "s", "c": "t", "d": "t"},
                       group={m: ("en", "1") for m in "abcd"})
    for iv in interval_pooled(one, "en", 20, 0, workers=1).values():
        assert iv == (100.0, 100.0)


def cross_topic_inputs():
    # two topics, one system cluster S spanning all four mentions
    key = {"a": "g1", "b": "g1", "c": "g2", "d": "g2"}
    return Track2Inputs(key=key, response={m: "S" for m in key},
                        group={"a": ("en", "1"), "b": ("en", "1"), "c": ("en", "2"), "d": ("en", "2")})


def test_cross_topic_cluster_stays_together_in_all_topics_once_resample():
    from weave_eval.bootstrap import _score_chunk
    inp = cross_topic_inputs()
    unit_data = [[(m, inp.key[m], inp.response[m]) for m in "ab"], [(m, inp.key[m], inp.response[m]) for m in "cd"]]
    got = _score_chunk(unit_data, np.array([[0, 1]]))
    assert (got == to_matrix([counts(inp.key, inp.response)])).all()


def test_pooled_interval_contains_point_estimate_with_cross_topic_clusters():
    inp = cross_topic_inputs()
    point = scores(counts(inp.key, inp.response))
    assert point.b3.precision == 50
    iv = interval_pooled(inp, "en", 200, 0, workers=1)
    assert iv["b3"][0] <= point.b3.f1 <= iv["b3"][1]
