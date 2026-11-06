import random
from datetime import datetime, timezone

from weave_clusterer.cards import build_card, card_hash
from weave_clusterer.state import Member


def mem(mid, order, action=None, vec=None, sentence="s", language="en", published_at=None, **extra):
    enr = {"action": {"text": action}, **{k: {"text": v} for k, v in extra.items()}}
    return Member(mid, tuple(order), enr, sentence, language, published_at,
                  {"action": vec} if vec is not None else {})


def test_card_deterministic_and_order_free():
    ms = [mem(f"m{i}", (i,), action=f"a{i % 3}", vec=[1.0, i * 0.1], sentence=f"sent {i}") for i in range(6)]
    base = card_hash(build_card(ms, 3, 600))
    for seed in range(5):
        shuffled = ms[:]
        random.Random(seed).shuffle(shuffled)
        assert card_hash(build_card(shuffled, 3, 600)) == base


def test_representative_is_most_central():
    ms = [mem("a", (1,), "x", [1.0, 0.0], "sa"), mem("b", (2,), "y", [0.9, 0.1], "sb"),
          mem("c", (3,), "z", [0.0, 1.0], "sc")]
    card = build_card(ms, 3, 600)
    assert card.sentence == "sb" and card.values["action"][0] == "y"


def test_card_tie_break_by_order_key():
    # identical vectors: all mean similarities tie; the smaller (order_key, mention_id) represents
    ms = [mem("z", (1,), "late", [1.0, 0.0], "sz"), mem("a", (2,), "mid", [1.0, 0.0], "sa"),
          mem("b", (2,), "mid2", [1.0, 0.0], "sb")]
    assert build_card(ms, 3, 600).sentence == "sz"
    ms = [mem("b", (2,), "m1", [1.0, 0.0], "sb"), mem("a", (2,), "m2", [1.0, 0.0], "sa")]
    assert build_card(ms, 3, 600).sentence == "sa"
    # value frequency tie: first order_key wins after the representative's value
    ms = [mem("r", (1,), "rep", [1.0, 0.0]), mem("m2", (5,), "later", [1.0, 0.0]), mem("m3", (2,), "earlier", [1.0, 0.0])]
    assert build_card(ms, 3, 600).values["action"] == ("rep", "earlier", "later")


def test_frequency_orders_values_and_limit():
    ms = [mem("r", (1,), "rep", [1.0, 0.0])] + [mem(f"p{i}", (2 + i,), "common", [1.0, 0.0]) for i in range(2)] + \
         [mem("q", (9,), "rare", [1.0, 0.0])]
    assert build_card(ms, 3, 600).values["action"] == ("rep", "common", "rare")
    assert build_card(ms, 2, 600).values["action"] == ("rep", "common")


def test_lone_member_and_metadata():
    t1, t2 = datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 5, tzinfo=timezone.utc)
    card = build_card([mem("a", (1,), "x", published_at=t2, language="es"),
                       mem("b", (2,), "y", published_at=t1, language="en")], 3, 600)
    assert card.size == 2 and card.languages == ("en", "es")
    assert card.earliest == t1.isoformat() and card.latest == t2.isoformat()
    assert build_card([mem("a", (1,), "x")], 3, 600).values["action"] == ("x",)


def test_card_respects_max_chars():
    ms = [mem(f"m{i}", (i,), action="word " * 10 + str(i), vec=[1.0, 0.0], sentence="long sentence " * 30,
              location="somewhere " + str(i)) for i in range(5)]
    full = len(build_card(ms, 5, 10_000).render())
    for limit in (full - 1, 300, 150):
        card = build_card(ms, 5, limit)
        assert len(card.render()) <= limit
    small = build_card(ms, 5, 300)
    assert small.sentence.endswith("…") and small.values["action"]


def test_card_has_no_ids():
    ms = [mem("mention-123", (1,), "x", [1.0, 0.0], "a sentence"), mem("mention-456", (2,), "y", [1.0, 0.0])]
    text = build_card(ms, 3, 600).render()
    assert "mention" not in text and "doc" not in text.lower() and "cluster" not in text.lower()
