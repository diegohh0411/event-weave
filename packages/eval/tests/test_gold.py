import sqlalchemy as sa

from conftest import database
from helpers import seed_corpus
from weave_eval.gold import HINDI_NOTE, chain_stats, gold_hash, load_gold

G = {"m1": [0, 3], "m2": [4, 7], "m3": [8, 11], "m4": [12, 15]}


def doc(i, lang="en", topic="t", split="test", gold=G):
    return {"id": i, "language": lang, "topic": topic, "split": split, "gold": gold}


def load(engine, spec, **kw):
    seed_corpus(engine, spec)
    kw = {"languages": None, "split": None, "document_ids": None, **kw}
    with engine.connect() as c:
        return load_gold(c, **kw)


def chains(gold):
    out = {}
    for k, cid in gold.cluster.items():
        out.setdefault(cid, set()).add(gold.mentions[k].mention_id)
    return sorted(map(sorted, out.values()))


def test_transitive_closure_and_singletons(db):
    g = load(db, {"docs": [doc("d1")], "pairs": [["d1", "m1", "d1", "m2"], ["d1", "m2", "d1", "m3"]]})
    assert chains(g) == [["m1", "m2", "m3"], ["m4"]]
    assert len({v for v in g.cluster.values()}) == 2
    assert all(len(v) == 16 for v in g.cluster.values())
    m = g.mentions[("d1", 0, 3)]
    assert (m.tok_start, m.tok_end) == (0, 1) and m.language == "en"


def test_token_span_none_off_boundary(db):
    g = load(db, {"docs": [{"id": "d1", "language": "en", "topic": "t", "tokens": [[0, 3], [4, 7]],
                            "gold": {"a": [0, 3], "b": [1, 3], "c": [0, 7]}}]})
    spans = {m.mention_id: (m.tok_start, m.tok_end) for m in g.mentions.values()}
    assert spans == {"a": (0, 1), "b": (None, None), "c": (0, 2)}


def test_negative_pairs_ignored(db):
    g = load(db, {"docs": [doc("d1")], "pairs": [["d1", "m1", "d1", "m2", 0]]})
    assert chains(g) == [["m1"], ["m2"], ["m3"], ["m4"]] and g.skipped_pairs == 0


def test_same_topic_id_two_languages_not_merged(db):
    g = load(db, {"docs": [doc("e1", "en", "1"), doc("s1", "es", "1")],
                  "pairs": [["e1", "m1", "e1", "m2"], ["s1", "m1", "s1", "m3"],
                            ["e1", "m1", "s1", "m1", 1, {"kind": "cross"}]]})
    assert g.skipped_pairs == 1
    by = {(k[0], g.mentions[k].mention_id): c for k, c in g.cluster.items()}
    assert by["e1", "m1"] == by["e1", "m2"] != by["s1", "m1"] == by["s1", "m3"]
    assert by["e1", "m3"] != by["s1", "m3"]


def test_bad_pairs_skipped_and_counted(db):
    g = load(db, {"docs": [doc("d1", topic="a"), doc("d2", topic="b")],
                  "pairs": [["d1", "m1", "d1", "nope"], ["d1", "m1", "d2", "m1", 1, {"kind": "cross"}]]})
    assert g.skipped_pairs == 2 and g.skipped_by_language == {"en": 2}
    assert len(chains(g)) == 8
    assert chain_stats(g)["en"].skipped_pairs == 2


def test_split_from_gold_documents_not_pairs(db):
    spec = {"docs": [doc("d1", split="test"), doc("d2", topic="u", split="dev")],
            "pairs": [["d1", "m1", "d1", "m2", 1, {"split": "dev"}], ["d2", "m1", "d2", "m2", 1, {"split": "test"}]]}
    g = load(db, spec, split="test")
    assert {k[0] for k in g.mentions} == {"d1"} and chains(g)[0] == ["m1", "m2"]
    assert g.skipped_pairs == 0


def test_filters(db):
    spec = {"docs": [doc("d1"), doc("s1", "es", "x")]}
    seed_corpus(db, spec)
    with db.connect() as c:
        assert {k[0] for k in load_gold(c, languages={"es"}, split=None, document_ids=None).mentions} == {"s1"}
        assert {k[0] for k in load_gold(c, languages=None, split=None, document_ids={"d1"}).mentions} == {"d1"}


def test_chain_stats_counts(db):
    # chain A: d1:m1, d1:m2, d2:m1 -> 1 within + 2 cross; chain B: d2:m2, d2:m3 -> 1 within; singleton d1:m3
    g = load(db, {"docs": [doc("d1"), doc("d2")],
                  "pairs": [["d1", "m1", "d1", "m2"], ["d1", "m2", "d2", "m1"], ["d2", "m2", "d2", "m3"]]})
    s = chain_stats(g)["en"]
    assert (s.mentions, s.chains, s.non_singleton) == (8, 5, 2)
    assert (s.within_links, s.cross_links, s.skipped_pairs, s.note) == (2, 2, 0, None)


def test_hindi_note_present(db):
    g = load(db, {"docs": [doc("h1", "hi", "1"), doc("d1")]})
    s = chain_stats(g)
    assert s["hi"].note == HINDI_NOTE == "crossDoc pairs unusable in the dataset (no topic or mention ids); chains are within-document only"
    assert s["en"].note is None and s["hi"].cross_links == 0


def test_gold_hash_stable_and_order_free(db):
    cross = ["d1", "m1", "d2", "m1", 1, {"kind": "cross"}]
    a = {"docs": [doc("d1"), doc("d2")], "pairs": [cross, ["d1", "m2", "d1", "m3"]]}
    b = {"docs": [doc("d2"), doc("d1")], "pairs": [["d1", "m2", "d1", "m3"], cross]}
    c = {"docs": [doc("d1"), doc("d2")], "pairs": [cross]}
    ha = gold_hash(load(db, a))
    with database() as url:
        eng = sa.create_engine(url)
        hb = gold_hash(load(eng, b))
    with database() as url:
        eng = sa.create_engine(url)
        hc = gold_hash(load(eng, c))
    assert ha == hb and len(ha) == 64 and ha != hc
