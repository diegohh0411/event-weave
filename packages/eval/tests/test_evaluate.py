import json
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from helpers import seed_corpus
from weave_core.cli import app
from weave_core.schema import evaluation_scores, evaluations, jobs, runs
from weave_eval import SCORER_VERSION
from weave_eval.evaluate import Evaluation, NotFinished, evaluate
from weave_eval.paper import load_paper
from weave_eval.render import render
from weave_eval.store import load_latest, save
from weave_eval.tracks import SKIPPED, TRACK2_MENTION_POLICY

STAGES = {s: {"adapter": s[0]} for s in ("provide", "extract", "enrich", "embed", "cluster")}
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def corpus(db, track="paper", state="done", docs=None, pairs=None, clusters=None, extra_jobs=()):
    docs = docs if docs is not None else [
        {"id": "d1", "language": "en", "topic": "1", "gold": {"m1": [0, 3], "m2": [4, 7], "m3": [8, 11]},
         "mentions": {"s1": [0, 3, 0, 1], "s2": [4, 7, 1, 2], "s3": [8, 11, 2, 3]}},
        {"id": "d2", "language": "en", "topic": "1", "gold": {"m4": [0, 3]}, "mentions": {"s4": [0, 3, 0, 1]}},
        {"id": "d3", "language": "es", "topic": "1", "gold": {"m5": [0, 3], "m6": [4, 7]},
         "mentions": {"s5": [0, 3, 0, 1], "s6": [4, 7, 1, 2]}},
        {"id": "d4", "language": "hi", "topic": "1", "gold": {"m7": [0, 3], "m8": [4, 7]},
         "mentions": {"s7": [0, 3, 0, 1], "s8": [4, 7, 1, 2]}}]
    pairs = pairs if pairs is not None else [
        ["d1", "m1", "d1", "m2"], ["d1", "m1", "d2", "m4"], ["d3", "m5", "d3", "m6"], ["d4", "m7", "d4", "m8"]]
    clusters = clusters if clusters is not None else {
        "c1": ["s1", "s2", "s4"], "c2": ["s3"], "c3": ["s5", "s6"], "c4": ["s7", "s8"]}
    seed = seed_corpus(db, {"state": state, "docs": docs, "pairs": pairs, "clusters": clusters})
    run_id = seed["run_id"]
    cfg = {"name": "t", "track": track, "seed": 7, "stages": STAGES}
    with db.begin() as c:
        c.execute(sa.update(runs).where(runs.c.id == run_id).values(
            config=cfg, started_at=T0, finished_at=T0.replace(hour=2)))
        for i, d in enumerate(docs):
            c.execute(sa.insert(jobs).values(
                run_id=run_id, stage="extract", unit_kind="document", unit_id=d["id"], unit_content_hash="h",
                partition="*", order_key=[i], input_hash="h", status="done", available_at=T0))
        for i, (stage, kind, status) in enumerate(extra_jobs):
            c.execute(sa.insert(jobs).values(
                run_id=run_id, stage=stage, unit_kind=kind, unit_id=f"x{i}", unit_content_hash="h",
                partition="*", order_key=[100 + i], input_hash="h", status=status, available_at=T0))
    return run_id


@pytest.mark.parametrize("state", ["created", "running", "cancelled"])
def test_refuses_unfinished_run(db, state, monkeypatch):
    run_id = corpus(db, state=state)
    with pytest.raises(NotFinished, match=f"run {run_id} is {state}; only finished runs can be scored"):
        evaluate(db, run_id, resamples=5)
    result = CliRunner().invoke(app, ["eval", str(run_id), "--resamples", "5"])
    assert result.exit_code == 1
    assert f"run {run_id} is {state}; only finished runs can be scored" in result.output


def test_refuses_run_without_gold_in_split(db):
    run_id = corpus(db)
    with pytest.raises(Exception, match=f"run {run_id} has no gold documents in split dev"):
        evaluate(db, run_id, split="dev", resamples=5)


def test_gold_vs_gold_run_scores_100(db):
    ev = evaluate(db, corpus(db), resamples=50, workers=1)
    assert ev.track == "paper" and ev.scores
    assert {(s.setting, s.language) for s in ev.scores} == {
        ("within", "en"), ("cross", "en"), ("within", "es"), ("cross", "es"), ("within", "hi")}
    for s in ev.scores:
        assert s.f1 == pytest.approx(100), s
        assert (s.f1_low, s.f1_high) == (pytest.approx(100), pytest.approx(100)), s
        if s.metric == "conll":
            assert s.precision is None and s.recall is None
    text = render(ev, load_paper())
    assert SKIPPED[("hi", "cross")] in text


def test_stored_rows_carry_provenance(db):
    run_id = corpus(db)
    ev = evaluate(db, run_id, resamples=20, workers=1)
    eid = save(db, ev)
    with db.connect() as c:
        row = c.execute(sa.select(evaluations).where(evaluations.c.id == eid)).one()
        n = c.scalar(sa.select(sa.func.count()).where(evaluation_scores.c.evaluation_id == eid))
    assert row.run_id == run_id and row.config_hash == "h" and row.gold_hash
    assert row.scorer_version == SCORER_VERSION and row.seed == 7 and row.resamples == 20 and row.split == "test"
    assert n == len(ev.scores)
    loaded = load_latest(db, run_id)
    assert loaded.id == eid and loaded.scores == sorted(
        ev.scores, key=lambda s: (s.track, s.setting, s.language, s.metric))
    assert loaded.report["gold_hash"] == row.gold_hash
    assert load_latest(db, uuid.uuid4()) is None


def test_rescore_same_seed_identical(db):
    run_id = corpus(db, track="realistic")
    a = evaluate(db, run_id, resamples=200, workers=1)
    b = evaluate(db, run_id, resamples=200, workers=2)
    assert a.scores == b.scores
    assert a.report == b.report


def test_track2_report_states_policy(db):
    ev = evaluate(db, corpus(db, track="realistic"), resamples=20, workers=1)
    assert {(s.setting, s.language) for s in ev.scores} == {("pooled", "en"), ("pooled", "es"), ("pooled", "all")}
    assert ev.report["triggers"]["hi"]["f1"] == pytest.approx(100)
    text = render(ev, load_paper())
    assert TRACK2_MENTION_POLICY in text
    assert "hi pooled: " + SKIPPED[("hi", "cross")] in text
    json.dumps(ev.report)


def test_report_shows_failures_and_chain_stats(db):
    run_id = corpus(db, extra_jobs=[("cluster", "mention", "failed"), ("cluster", "mention", "failed"),
                                    ("enrich", "document", "failed")])
    ev = evaluate(db, run_id, resamples=20, workers=1)
    text = render(ev, load_paper())
    lines = text.splitlines()
    assert any(l.split() == ["cluster", "0", "2"] for l in lines)
    assert any(l.split() == ["enrich", "1", "0"] for l in lines)
    assert any(l.split() == ["provide", "0", "0"] for l in lines)  # every configured stage, zero-filled
    en = next(l for l in lines if l.startswith("en ") and "≠" in l)
    assert "3 / 22445 ≠" not in en and "4 / 22445 ≠" in en  # mentions ours / paper
    assert "crossDoc pairs unusable in the dataset" in text
    assert "UNCHECKED" in text  # the paper file has one checker
    assert not load_paper().checked
    assert text.index("== Scores") < text.index("== Failures") < text.index("== Chain") < text.index("== Comparison") \
        < text.index("== Significance") < text.index("== Cost")


def test_cli_mounted(db):
    assert "eval" in CliRunner().invoke(app, ["--help"]).output
    run_id = corpus(db)
    result = CliRunner().invoke(app, ["eval", str(run_id), "--resamples", "10", "--workers", "1"])
    assert result.exit_code == 0, result.output
    assert result.output.rstrip().splitlines()[-1].startswith("evaluation ")
    assert load_latest(db, run_id) is not None
