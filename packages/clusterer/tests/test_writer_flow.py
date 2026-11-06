import sqlalchemy as sa
from weave_clusterer.cards import build_card, card_hash
from weave_clusterer.state import PartitionView
from weave_clusterer.threshold import ThresholdClusterer
from weave_core.adapters import AdapterFailure
from weave_core.schema import cluster_members, clusters, jobs

from helpers import decisions, drain, make_pipeline_run, membership, seed_unit, setup, unit


def test_all_empty_fields_singleton(db):
    class NoDecide(ThresholdClusterer):
        def decide(self, *a):
            raise AssertionError("decide must not be called")

    run = setup(db, [("m1", {"action": unit(0.0)}), ("m2", {})])
    drain(db, NoDecide)
    got, d = membership(db, run), decisions(db, run)
    assert got["m2"] != got["m1"]
    assert (d["m2"].outcome, d["m2"].reason, d["m2"].candidates, d["m2"].noul) == ("new", "no_fields", [], None)
    with db.connect() as c:
        assert c.scalar(sa.select(jobs.c.status).where(jobs.c.stage == "cluster", jobs.c.unit_id == "m2")) == "done"


def test_back_to_back_same_document(db):
    run = make_pipeline_run_with_shared_document(db)
    drain(db)
    got, d = membership(db, run), decisions(db, run)
    assert got["m1"] == got["m2"]
    assert (d["m2"].outcome, d["m2"].reason) == ("join", "above_threshold")


def make_pipeline_run_with_shared_document(db):
    with db.begin() as c:
        run = make_pipeline_run(c)
        seed_unit(c, run, "m1", (1,), {"action": unit(0.0)}, doc_id="shared")
        seed_unit(c, run, "m2", (2,), {"action": unit(0.05)}, doc_id="shared")
    return run


def test_every_mention_in_exactly_one_cluster(db):
    class FailsOnM3(ThresholdClusterer):
        def decide(self, mention, candidates, cards):
            if mention.mention_id == "m3":
                raise AdapterFailure("boom", retryable=False)
            return super().decide(mention, candidates, cards)

    run = setup(db, [("m1", {"action": unit(0.0)}), ("m2", {"action": unit(0.1)}), ("m3", {"action": unit(0.2)}),
                     ("m4", {}), ("m5", {"action": unit(1.5)})])
    drain(db, FailsOnM3)
    with db.connect() as c:
        status = dict(c.execute(sa.select(jobs.c.unit_id, jobs.c.status).where(jobs.c.stage == "cluster")).all())
        counts = c.execute(sa.select(cluster_members.c.mention_id, sa.func.count()).group_by(cluster_members.c.mention_id)).all()
    done = {m for m, s in status.items() if s == "done"}
    assert status["m3"] == "failed" and done == {"m1", "m2", "m4", "m5"}
    assert {m for m, _ in counts} == done and all(n == 1 for _, n in counts)
    assert len(decisions(db, run)) == len(done)


def test_card_changes_only_on_join(db):
    from sqlalchemy.orm import Session
    run = setup(db, [("m1", {"action": unit(0.0)}), ("m2", {"action": unit(1.5)})])
    drain(db)
    with db.begin() as c:
        seed_unit(c, run, "m3", (3,), {"action": unit(0.05)})
        c.execute(sa.text("UPDATE runs SET state = 'running' WHERE id = :r"), {"r": run})
    with db.connect() as c:
        before = dict(c.execute(sa.select(clusters.c.id, clusters.c.card_hash)).all())
    drain(db)
    with db.connect() as c:
        after = dict(c.execute(sa.select(clusters.c.id, clusters.c.card_hash)).all())
    got = membership(db, run)
    assert got["m3"] == got["m1"]
    assert after[got["m2"]] == before[got["m2"]] and after[got["m1"]] != before[got["m1"]]
    # the stored hash is the pure function of the members
    with Session(db) as s:
        members = PartitionView(s, run, "all", deployment="stub-emb", dimension=3).members(got["m1"])
        assert card_hash(build_card(members, 3, 600)) == after[got["m1"]]
