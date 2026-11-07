from helpers import seed_corpus
from weave_eval.gold import load_gold
from weave_eval.system import load_system
from weave_eval.tracks import (SKIPPED, TRACK2_MENTION_POLICY, cluster_spread, track1, track2, trigger_scores)

G = {"m1": [0, 3], "m2": [4, 7], "m3": [8, 11]}


def sysm(prefix, ids=(1, 2, 3)):
    """System mentions on gold tokens 0..2 of a 4-char-per-token document."""
    return {f"{prefix}{i}": [4 * (i - 1), 4 * (i - 1) + 3, i - 1, i] for i in ids}


def doc(i, lang="en", topic="t", split="test", gold=G, mentions=None):
    return {"id": i, "language": lang, "topic": topic, "split": split, "gold": gold,
            "mentions": sysm(i) if mentions is None else mentions}


def run(engine, spec, split=None):
    seeded = seed_corpus(engine, spec)
    with engine.connect() as c:
        gold = load_gold(c, split=split)
        system = load_system(c, seeded["run_id"], list(seeded["docs"]))
    return gold, system


# Gold: a1,a2 corefer across docs a/b (a1~b1), a2 alone; two docs, one topic.
CROSS = {"docs": [doc("a"), doc("b")],
         "pairs": [["a", "m1", "a", "m2"], ["b", "m1", "b", "m2"], ["a", "m1", "b", "m1"]]}


def test_track1_within_vs_cross(db):
    ok = {**CROSS, "clusters": {"c1": ["a1", "a2", "b1", "b2"], "c2": ["a3"], "c3": ["b3"]}}
    gold, system = run(db, ok)
    r = track1(gold, system)["en"]
    from weave_eval.metrics import scores
    assert scores(r.within[("en", "t")]).conll == 100 and scores(r.cross[("en", "t")]).conll == 100


def test_track1_split_cross_chain(db):
    bad = {**CROSS, "clusters": {"c1": ["a1", "a2"], "c4": ["b1", "b2"], "c2": ["a3"], "c3": ["b3"]}}
    gold, system = run(db, bad)
    r = track1(gold, system)["en"]
    from weave_eval.metrics import scores
    assert scores(r.within[("en", "t")]).conll == 100 and scores(r.cross[("en", "t")]).muc.recall < 100


def test_failed_mention_is_missed_gold(db):
    spec = {"docs": [doc("a")], "pairs": [["a", "m1", "a", "m2"]], "clusters": {"c1": ["a1"], "c2": ["a3"]}}
    gold, system = run(db, spec)
    r = track1(gold, system)["en"]
    from weave_eval.metrics import scores
    s = scores(r.within[("en", "t")])
    assert s.b3.recall < 100 and s.b3.precision == 100 and len(gold.mentions) == 3


def test_track2_duplicate_system_span_one_match(db):
    spec = {"docs": [doc("a", mentions={**sysm("a"), "a0": [0, 3, 0, 1]})],
            "clusters": {"c1": ["a1", "a2", "a3"], "c2": ["a0"]}}
    gold, system = run(db, spec)
    r = track2(gold, system)
    assert r.inputs.response[("a", 0, 1)] == "c2"  # smallest mention id "a0" wins
    assert r.inputs.response[("sys", "a1")] == "c1" and len(r.inputs.response) == 4
    assert r.pooled.b3_pd == 4 and r.pooled.b3_rd == 3


def test_unaligned_gold_counted(db):
    spec = {"docs": [{"id": "a", "language": "en", "topic": "t", "tokens": [[0, 3], [4, 7]],
                      "gold": {"m1": [0, 3], "m2": [1, 3]}, "mentions": {"a1": [0, 3, 0, 1]}}],
            "clusters": {"c1": ["a1"]}}
    gold, system = run(db, spec)
    r = track2(gold, system)
    assert r.unaligned_gold == 1 and r.pooled.b3_rd == 2 and r.pooled.b3_pd == 1


def test_trigger_prf_hand_counted(db):
    # 3 gold; system: exact on m1, duplicate of m1, off-by-one span, so tp=1, system=3, gold=3
    spec = {"docs": [doc("a", mentions={"a1": [0, 3, 0, 1], "a2": [0, 3, 0, 1], "a3": [4, 7, 1, 3]})],
            "clusters": {"c1": ["a1", "a2", "a3"]}}
    gold, system = run(db, spec)
    t = trigger_scores(gold, system)["all"]
    assert round(t.precision, 3) == round(100 / 3, 3) and round(t.recall, 3) == round(100 / 3, 3)
    assert round(t.f1, 3) == round(100 / 3, 3)


def test_cluster_spread_counts(db):
    spec = {"docs": [doc("a", topic="t1"), doc("b", topic="t2"), doc("c", lang="es", topic="t1")],
            "clusters": {"c1": ["a1", "a2"], "c2": ["a3", "b1"], "c3": ["b2", "c1"], "c4": ["b3"]}}
    gold, system = run(db, spec)
    s = cluster_spread(system, gold)
    assert (s.clusters, s.multi_topic, s.multi_language) == (4, 2, 1)


def test_track2_restricted_to_split(db):
    spec = {"docs": [doc("a", split="test"), doc("d", split="dev")],
            "clusters": {"c1": ["a1", "a2", "a3"], "c2": ["d1", "d2", "d3"]}}
    gold, system = run(db, spec, split="test")
    r = track2(gold, system)
    assert r.pooled.b3_pd == 3 and r.pooled.b3_rd == 3 and len(r.inputs.key) == 3


def test_hindi_cross_not_scored(db):
    assert ("hi", "cross") in SKIPPED
    spec = {"docs": [doc("a"), doc("h", lang="hi", topic="x"), doc("k", lang="hi", topic="x")],
            "pairs": [["h", "m1", "h", "m2"]],
            "clusters": {"c1": ["a1", "a2", "a3"], "c2": ["h1", "h2", "h3"], "c3": ["k1", "k2", "k3"]}}
    gold, system = run(db, spec)
    t1 = track1(gold, system)
    assert t1["hi"].within and not t1["hi"].cross and t1["en"].cross
    r = track2(gold, system)
    assert set(r.per_language) == {"en"} and r.pooled.b3_rd == 3 and r.pooled.b3_pd == 3
    assert all(g[0] != "hi" for g in r.inputs.group.values())
    assert "hi" in trigger_scores(gold, system)


def test_policy_text():
    assert TRACK2_MENTION_POLICY.startswith("Mentions present on only one side are kept as the reference CoNLL")


def test_gold_empty_document_is_scored(db):
    spec = {"docs": [doc("a"), doc("e", topic="t2", gold={}, mentions=sysm("e"))],
            "clusters": {"c1": ["a1", "a2", "a3"], "c2": ["e1", "e2", "e3"]}}
    gold, system = run(db, spec)
    assert gold.documents["e"] == ("en", "t2")
    r = track2(gold, system)
    assert r.pooled.b3_pd == 6 and r.pooled.b3_rd == 3 and r.pooled.b3_pn < r.pooled.b3_pd
    assert round(trigger_scores(gold, system)["all"].precision, 3) == 50.0
    assert cluster_spread(system, gold).multi_topic == 0 and cluster_spread(system, gold).clusters == 2
    assert track1(gold, system)["en"].unmatched_system == 3


def test_track1_prefers_clustered_duplicate(db):
    spec = {"docs": [doc("a", mentions={**sysm("a"), "a0": [0, 3, 0, 1]})],
            "clusters": {"c1": ["a1", "a2", "a3"]}}  # a0 (smallest id) is unclustered
    gold, system = run(db, spec)
    from weave_eval.metrics import scores
    assert scores(track1(gold, system)["en"].within[("en", "t")]).b3.recall == 100
