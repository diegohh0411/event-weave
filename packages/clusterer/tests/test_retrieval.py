import math

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from weave_clusterer.retrieval import retrieve
from weave_clusterer.state import PartitionView

from helpers import add_member, make_run, q

W = {"action": 1.0, "participants": 1.0, "location": 1.0, "time": 1.0}


def unit(angle):
    return [math.cos(angle), math.sin(angle), 0.0]


@pytest.fixture
def session(db):
    with Session(db) as s, s.begin():
        yield s


def test_best_member_not_average(session):
    run = make_run(session)
    add_member(session, run, "p", "A", "a1", {"action": unit(0.0)})
    add_member(session, run, "p", "A", "a2", {"action": unit(1.5)}, order=(2,))
    add_member(session, run, "p", "B", "b1", {"action": unit(0.4)}, order=(3,))
    add_member(session, run, "p", "B", "b2", {"action": unit(0.4)}, order=(4,))
    got = retrieve(session, run, "p", q(action=unit(0.0)), W, 5)
    assert [c.cluster_id for c in got] == ["A", "B"]
    assert got[0].score == pytest.approx(1.0) and got[0].field_scores == {"action": pytest.approx(1.0)}
    assert got[1].score == pytest.approx(math.cos(0.4))


def test_weights_renormalised_when_field_missing(session):
    run = make_run(session)
    add_member(session, run, "p", "A", "a1", {"action": unit(0.0), "location": unit(1.0)})
    got = retrieve(session, run, "p", q(action=unit(0.5)), {"action": 1.0, "location": 3.0, "time": 1.0}, 3)
    assert got[0].score == pytest.approx(math.cos(0.5))
    # only fields both sides have count: query has action and time, cluster has action and location
    got = retrieve(session, run, "p", q(action=unit(0.5), time=unit(0.0)), W, 3)
    assert got[0].score == pytest.approx(math.cos(0.5)) and set(got[0].field_scores) == {"action"}


def test_zero_shared_fields_not_returned_and_k_limits(session):
    run = make_run(session)
    add_member(session, run, "p", "A", "a1", {"location": unit(0.0)})
    add_member(session, run, "p", "B", "b1", {"action": unit(0.0)}, order=(2,))
    add_member(session, run, "p", "C", "c1", {"action": unit(0.1)}, order=(3,))
    got = retrieve(session, run, "p", q(action=unit(0.0)), W, 1)
    assert [c.cluster_id for c in got] == ["B"]


def test_never_crosses_partition(session):
    run, other = make_run(session), make_run(session)
    add_member(session, run, "p", "A", "a1", {"action": unit(1.0)})
    add_member(session, run, "other", "X", "x1", {"action": unit(0.0)}, order=(2,))
    add_member(session, other, "p", "Y", "y1", {"action": unit(0.0)}, order=(3,))
    got = retrieve(session, run, "p", q(action=unit(0.0)), W, 5)
    assert [c.cluster_id for c in got] == ["A"]


def test_partition_view_scoped_to_run(session):
    run, other = make_run(session), make_run(session)
    add_member(session, run, "p", "A", "a1", {"action": unit(0.0)},
               enrichment={"action": {"text": "arrested"}})
    add_member(session, other, "p", "B", "b1", {"action": unit(0.0)})
    view = PartitionView(session, run, "p")
    assert view.cluster_ids() == ["A"]
    (m,) = view.members("A")
    assert m.enrichment == {"action": {"text": "arrested"}} and m.sentence == "Sentence of a1."
    assert m.vectors == {"action": [pytest.approx(x) for x in unit(0.0)]} and m.order_key == (1,)
    assert PartitionView(session, run, "other").cluster_ids() == []
