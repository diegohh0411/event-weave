import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from weave_core.schema import jobs, ledger, runs
from weave_eval.cost import cost_report, failures

CONFIG = {"name": "t", "track": "paper", "seed": 1, "stages": {
    "provide": {"adapter": "p"}, "extract": {"adapter": "e"}, "enrich": {"adapter": "n"},
    "embed": {"adapter": "m"}, "cluster": {"adapter": "c"}}}
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_run(engine, hours=None, config=CONFIG):
    run_id = uuid.uuid4()
    with engine.begin() as c:
        c.execute(sa.insert(runs).values(
            id=run_id, config=config, config_hash="h", state="done", created_at=T0, started_at=T0,
            finished_at=None if hours is None else T0 + timedelta(hours=hours)))
    return run_id


def add_ledger(engine, run_id, rows):
    """rows: (stage, deployment, cost, latency_ms, status='ok', estimated=False, in_tok=10, out_tok=5)."""
    with engine.begin() as c:
        for i, (stage, dep, cost, lat, *rest) in enumerate(rows):
            status, est, it, ot = (list(rest) + ["ok", False, 10, 5][len(rest):])[:4]
            c.execute(sa.insert(ledger).values(
                run_id=run_id, stage=stage, adapter="a", deployment=dep, request_hash=f"r{i}", input_tokens=it,
                output_tokens=ot, cost=Decimal(cost), estimated=est, latency_ms=lat, attempts=1, status=status,
                created_at=T0))


def add_jobs(engine, run_id, rows):
    """rows: (stage, unit_kind, status, cache_key, cache_hit)."""
    with engine.begin() as c:
        for i, (stage, kind, status, key, hit) in enumerate(rows):
            c.execute(sa.insert(jobs).values(
                run_id=run_id, stage=stage, unit_kind=kind, unit_id=f"u{i}", unit_content_hash="h", partition="*",
                order_key=[i], input_hash="h", status=status, available_at=T0, cache_key=key, cache_hit=hit))


def ledger_sum(engine, run_id):
    with engine.connect() as c:
        return c.scalar(sa.select(sa.func.sum(ledger.c.cost)).where(ledger.c.run_id == run_id))


def test_total_equals_ledger_sum(db):
    run = make_run(db)
    add_ledger(db, run, [("extract", "m1", "0.00000001", 5), ("extract", "m1", "0.12345679", 6),
                         ("extract", "m2", "1.00000003", 7), ("enrich", "m2", "0.33333333", 8),
                         ("enrich", "m2", "0.66666667", 9), (None, "m3", "0.00000007", 1),
                         ("embed", "m3", "2.50000001", 2)])
    report = cost_report(db, run)
    total = ledger_sum(db, run)
    assert report.total_dollars == total == Decimal("4.62345691")
    assert sum(r.dollars for r in report.rows) == total
    assert all(isinstance(r.dollars, Decimal) for r in report.rows)
    assert "(none)" in {r.stage for r in report.rows}


def test_estimated_marked(db):
    run = make_run(db)
    add_ledger(db, run, [("extract", "m1", "1.5", 5, "ok", True), ("extract", "m1", "2.0", 5)])
    report = cost_report(db, run)
    (row,) = report.rows
    assert report.any_estimated and row.estimated_dollars == Decimal("1.5") and row.dollars == Decimal("3.5")
    run2 = make_run(db)
    add_ledger(db, run2, [("extract", "m1", "2.0", 5)])
    r2 = cost_report(db, run2)
    assert not r2.any_estimated and r2.rows[0].estimated_dollars == 0


def test_percentiles(db):
    run = make_run(db)
    add_ledger(db, run, [("extract", "m1", "0.1", ms) for ms in (10, 20, 30, 40, 100)]
               + [("extract", "m1", "0.1", 50, "error"), ("extract", "m1", "0.1", 60, "refused")])
    (row,) = cost_report(db, run).rows
    assert (row.calls, row.errors) == (7, 2)
    assert (row.input_tokens, row.output_tokens) == (70, 35)
    # latencies 10..100 sorted: 10,20,30,40,50,60,100 -> p50 = 40, p95 = 60 + 0.7 * 40 = 88
    assert row.p50_ms == 40 and row.p95_ms == pytest.approx(88.0)


def test_per_1000_and_per_hour(db):
    run = make_run(db, hours=2)
    add_ledger(db, run, [("extract", "m1", "3.00", 5), ("embed", "m1", "1.00", 5)])
    add_jobs(db, run, [("enrich", "mention", "done", None, False)] * 500 + [("extract", "document", "done", None, False)])
    report = cost_report(db, run)
    assert report.mentions == 500
    assert report.dollars_per_1000_mentions == Decimal("8")
    assert report.mentions_per_hour == pytest.approx(250.0)


def test_rates_none_without_denominators(db):
    no_time = make_run(db)
    add_jobs(db, no_time, [("enrich", "mention", "done", None, False)])
    r = cost_report(db, no_time)
    assert r.mentions_per_hour is None and r.dollars_per_1000_mentions == 0
    empty = make_run(db, hours=1)
    r = cost_report(db, empty)
    assert r.mentions == 0 and r.mentions_per_hour is None and r.dollars_per_1000_mentions is None
    assert r.cache_share is None and r.rows == [] and r.total_dollars == 0


def test_cache_share(db):
    run = make_run(db)
    add_jobs(db, run, [("extract", "document", "done", "k1", True), ("extract", "document", "done", "k2", False),
                       ("enrich", "mention", "done", "k3", True), ("enrich", "mention", "done", None, False),
                       ("enrich", "mention", "failed", "k4", False), ("cluster", "mention", "done", "k5", True)])
    assert cost_report(db, run).cache_share == pytest.approx(2 / 3)


def test_failures_by_stage_and_kind(db):
    run = make_run(db)
    add_jobs(db, run, [("extract", "document", "failed", None, False), ("extract", "document", "failed", None, False),
                       ("extract", "document", "done", None, False), ("cluster", "mention", "failed", None, False),
                       ("enrich", "mention", "failed", None, False), ("enrich", "mention", "pending", None, False)])
    assert failures(db, run) == {"extract": {"documents": 2, "mentions": 0},
                                 "cluster": {"documents": 0, "mentions": 1},
                                 "enrich": {"documents": 0, "mentions": 1}}
    assert failures(db, make_run(db)) == {}


def test_other_runs_ledger_excluded(db):
    a, b = make_run(db), make_run(db)
    add_ledger(db, a, [("extract", "m1", "1.0", 5)])
    add_ledger(db, b, [("extract", "m1", "9.0", 5), ("embed", "m2", "1.0", 5)])
    report = cost_report(db, a)
    assert report.total_dollars == Decimal("1.0") and len(report.rows) == 1 and report.rows[0].calls == 1
