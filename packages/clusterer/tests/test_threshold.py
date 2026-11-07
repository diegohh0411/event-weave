import sqlalchemy as sa
from weave_core.schema import clusters, jobs, ledger

from helpers import decisions, drain, membership, setup, unit


def test_threshold_joins_and_splits(db):
    run = setup(db, [("m1", {"action": unit(0.0)}), ("m2", {"action": unit(0.1)}), ("m3", {"action": unit(1.5)})])
    drain(db)
    got = membership(db, run)
    assert got["m1"] == got["m2"] != got["m3"]
    d = decisions(db, run)
    assert (d["m1"].outcome, d["m1"].reason) == ("new", "empty_partition")
    assert (d["m2"].outcome, d["m2"].reason, d["m2"].cluster_id) == ("join", "above_threshold", got["m1"])
    assert (d["m3"].outcome, d["m3"].reason) == ("new", "below_threshold")
    assert d["m2"].candidates[0]["cluster_id"] == got["m1"]
    with db.connect() as c:
        sizes = dict(c.execute(sa.select(clusters.c.id, clusters.c.size)).all())
        done = c.scalar(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.stage == "cluster", jobs.c.status == "done"))
    assert sizes == {got["m1"]: 2, got["m3"]: 1} and done == 3


def test_threshold_makes_no_model_calls(db):
    setup(db, [("m1", {"action": unit(0.0)}), ("m2", {"action": unit(0.1)})])
    drain(db)
    with db.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(ledger)) == 0


