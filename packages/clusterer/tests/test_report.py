import sqlalchemy as sa
from helpers import make_pipeline_run, seed_unit
from typer.testing import CliRunner
from weave_clusterer import cli
from weave_clusterer.report import RecallRow, recall_at_k, summary
from weave_core.schema import (cluster_decisions, cluster_members, clusters, gold_mentions, gold_pairs, jobs,
                               ledger)

V = {"action": [1.0, 0.0, 0.0]}


def place(c, run, mention, order, cluster, reason, candidates=(), deployment=None, status="done"):
    """Record a clustering result by hand: membership, cluster row and decision."""
    if c.execute(sa.select(clusters.c.id).where(clusters.c.id == cluster)).first() is None:
        c.execute(sa.insert(clusters).values(id=cluster, run_id=run, partition="all", card={}, card_hash="h", size=1,
                                             created_order=[order]))
    c.execute(sa.insert(cluster_members).values(run_id=run, mention_id=mention, cluster_id=cluster, partition="all",
                                                order_key=[order]))
    c.execute(sa.insert(cluster_decisions).values(
        run_id=run, mention_id=mention, adapter="a", adapter_version="1", deployment=deployment,
        candidates=[{"cluster_id": cid, "score": 1.0 - i / 10, "field_scores": {}, "card_hash": "h", "label": None}
                    for i, cid in enumerate(candidates)],
        noul=None, probabilities=None, outcome="join" if cluster in candidates else "new", reason=reason,
        cluster_id=cluster))
    c.execute(sa.update(jobs).where(jobs.c.run_id == run, jobs.c.unit_id == mention, jobs.c.stage == "cluster")
              .values(status=status))


def gold(c, a, b, label=1):
    c.execute(sa.insert(gold_pairs).values(language="en", kind="within", split="dev", topic="t", source_document_id="d-" + a,
                                           source_mention_id=a, target_document_id="d-" + b, target_mention_id=b,
                                           label=label))


def seed_four(db):
    with db.begin() as c:
        run = make_pipeline_run(c)
        for i, m in enumerate("abcd", start=1):
            seed_unit(c, run, m, (i,), V, partition="all")
            c.execute(sa.insert(gold_mentions).values(document_id="d-" + m, mention_id=m, start=0, end=8, text="Sentence"))
        # Gold chain a - b - d; c stands alone (its pair with a is a negative).
        gold(c, "a", "b")
        gold(c, "b", "d")
        gold(c, "c", "a", label=0)
        place(c, run, "a", 1, "cA", "empty_partition")
        place(c, run, "b", 2, "cA", "above_threshold", ["cA"])
        place(c, run, "c", 3, "cC", "below_threshold", ["cA"])
        place(c, run, "d", 4, "cA", "above_threshold", ["cC", "cA"])
    return run


def test_recall_at_k_hand_counted(db):
    run = seed_four(db)
    # b has antecedent a (cluster cA, ranked 1st): hit at 1 and 2. d has antecedents a, b (cluster cA, ranked 2nd).
    # a has no antecedent and c has no gold partner, so neither counts.
    assert recall_at_k(db, run, 1) == [RecallRow("en", 2, 1, 0.5)]
    assert recall_at_k(db, run, 2) == [RecallRow("en", 2, 2, 1.0)]
    assert recall_at_k(db, run) == recall_at_k(db, run, 2)
    assert recall_at_k(db, run, 99) == recall_at_k(db, run, 2)


def test_recall_ignores_other_runs_and_partitions(db):
    run = seed_four(db)
    with db.begin() as c:
        c.execute(sa.update(cluster_members).where(cluster_members.c.mention_id == "b").values(partition="elsewhere"))
    # b is now alone in its partition, so only d (antecedent a, same partition) still counts.
    assert recall_at_k(db, run, 2) == [RecallRow("en", 1, 1, 1.0)]


def ledger_row(c, run, stage, request_hash):
    c.execute(sa.insert(ledger).values(run_id=run, stage=stage, adapter="a", deployment="stub-s1",
                                       request_hash=request_hash, cost=0, estimated=False, latency_ms=1, attempts=1,
                                       status="ok", created_at=sa.func.now()))


def test_decision_calls_count_ledger_rows_not_replayed_decisions(db):
    with db.begin() as c:
        run = make_pipeline_run(c)
        seed_unit(c, run, "m1", (1,), V)
        place(c, run, "m1", 1, "c1", "above_threshold", ["c1"], deployment="stub-s1")  # replayed: no ledger row
    assert summary(db, run).decision_calls == 0


def test_summary_counts(db):
    with db.begin() as c:
        run = make_pipeline_run(c)
        for i, m in enumerate(["m1", "m2", "m3", "m4"], start=1):
            seed_unit(c, run, m, (i,), V)
        place(c, run, "m1", 1, "c1", "empty_partition")
        place(c, run, "m2", 2, "c1", "above_threshold", ["c1"], deployment="stub-s1")
        place(c, run, "m3", 3, "c3", "noul_below_threshold", ["c1"], deployment="stub-s1")
        c.execute(sa.update(jobs).where(jobs.c.run_id == run, jobs.c.unit_id == "m4", jobs.c.stage == "cluster")
                  .values(status="failed"))
        ledger_row(c, run, "cluster", "h1")
        ledger_row(c, run, "cluster", "h2")
        ledger_row(c, run, "embed", "h3")  # other stages do not count
    s = summary(db, run)
    assert (s.mentions, s.clustered, s.failed, s.clusters, s.singletons, s.decision_calls) == (4, 3, 1, 2, 1, 2)
    assert s.outcomes == {"above_threshold": 1, "empty_partition": 1, "noul_below_threshold": 1}


def test_report_cli(db):
    run = seed_four(db)
    result = CliRunner().invoke(cli.app, ["report", str(run), "--k", "1"])
    assert result.exit_code == 0, result.output
    assert "clustered 4" in result.output and "recall@1" in result.output and "0.500" in result.output
