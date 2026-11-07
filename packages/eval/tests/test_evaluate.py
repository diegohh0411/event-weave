import json
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from helpers import MENTION_STAGE, seed_corpus
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


def test_chain_stats_cover_whole_dataset_not_split(db):
    docs = [
        {"id": "d1", "language": "en", "topic": "1", "split": "test", "gold": {"m1": [0, 3], "m2": [4, 7]},
         "mentions": {"s1": [0, 3, 0, 1], "s2": [4, 7, 1, 2]}},
        {"id": "d2", "language": "en", "topic": "2", "split": "dev", "gold": {"m3": [0, 3], "m4": [4, 7]}},
        {"id": "d3", "language": "es", "topic": "1", "split": "dev", "gold": {"m5": [0, 3]}}]
    run_id = corpus(db, docs=docs, pairs=[["d1", "m1", "d1", "m2"], ["d2", "m3", "d2", "m4"]],
                    clusters={"c1": ["s1", "s2"]})
    ev = evaluate(db, run_id, resamples=5, workers=1)
    stats = ev.report["chain_stats"]
    assert stats["en"]["mentions"] == 4 and stats["en"]["chains"] == 2 and stats["en"]["non_singleton"] == 2
    assert stats["es"]["mentions"] == 1  # d3 is a dev-only Spanish document of the run: language still reported
    assert {s.language for s in ev.scores} == {"en"}  # but scores use test gold only
    assert all(s.f1 == pytest.approx(100) for s in ev.scores)  # the dev docs have no system mentions; not counted
    assert "whole dataset" in render(ev, load_paper())


def test_failures_labelled_run_wide(db):
    text = render(evaluate(db, corpus(db), resamples=5, workers=1), load_paper())
    assert "run-wide" in text


def test_track2_unmatched_system_counted_by_track2(db):
    from weave_eval.gold import load_gold
    from weave_eval.system import load_system
    from weave_eval.tracks import track2
    run_id = corpus(db, track="realistic")
    with db.begin() as c:
        c.execute(sa.text("insert into mentions (id, document_id, start, \"end\", tok_start, tok_end, sentence, text, extractor, extractor_version) "
                          "values ('x1','d1',0,3,0,1,0,'x','e','1')"))
        c.execute(sa.insert(jobs).values(
            run_id=run_id, stage=MENTION_STAGE, unit_kind="mention", unit_id="x1", unit_content_hash="h",
            partition="*", order_key=[50], input_hash="h", status="done", available_at=T0))
        c.execute(sa.text("insert into cluster_members (run_id, mention_id, cluster_id, partition, order_key) "
                          "values (:r,'x1','c1','p','{9}')"), {"r": run_id})
    with db.connect() as c:
        gold = load_gold(c, split="test")
        r = track2(gold, load_system(c, run_id, gold.documents, MENTION_STAGE))
    assert r.unmatched_system == 1  # a clustered duplicate of a matched span is spurious
    ev = evaluate(db, run_id, resamples=5, workers=1)
    assert ev.report["unmatched_system"] == {"all": 1}


def add_foreign_mentions(db, doc_id="d1"):
    """Mentions of another run/extractor on the same document (no jobs in the run under test)."""
    with db.begin() as c:
        for i, (st, en, ts, te) in enumerate([(0, 3, 0, 1), (4, 7, 1, 2), (8, 11, 2, 3)]):
            c.execute(sa.text("insert into mentions (id, document_id, start, \"end\", tok_start, tok_end, sentence, "
                              "text, extractor, extractor_version) values (:i,:d,:s,:e,:ts,:te,0,'x','gold','1')"),
                      dict(i=f"o{i}", d=doc_id, s=st, e=en, ts=ts, te=te))
        c.execute(sa.text("insert into mentions (id, document_id, start, \"end\", tok_start, tok_end, sentence, "
                          "text, extractor, extractor_version) values ('o9',:d,0,7,0,2,0,'x','gold','1')"), dict(d=doc_id))


def test_other_runs_mentions_are_ignored(db):
    from weave_eval.gold import load_gold
    from weave_eval.system import load_system
    from weave_eval.tracks import trigger_scores, track2
    run_id = corpus(db, track="realistic")
    add_foreign_mentions(db)
    with db.connect() as c:
        gold = load_gold(c, split="test")
        system = load_system(c, run_id, gold.documents, MENTION_STAGE)
        assert not {m.mention_id for m in system} & {"o0", "o1", "o2", "o9"}
        assert track2(gold, system).unmatched_system == 0
        assert trigger_scores(gold, system)["en"].precision == pytest.approx(100)
    ev = evaluate(db, run_id, resamples=5, workers=1)
    assert ev.report["unmatched_system"] == {"all": 0}
    assert all(t["precision"] == pytest.approx(100) and t["recall"] == pytest.approx(100)
               for t in ev.report["triggers"].values())


def test_other_runs_mentions_ignored_in_track1(db):
    run_id = corpus(db)
    add_foreign_mentions(db)
    ev = evaluate(db, run_id, resamples=5, workers=1)
    assert ev.report["unmatched_system"]["en"] == 0


def test_failed_cluster_mention_is_a_predicted_trigger_not_coreferent(db):
    from weave_eval.gold import load_gold
    from weave_eval.system import load_system
    run_id = corpus(db, track="realistic", clusters={"c1": ["s1", "s2", "s4"], "c3": ["s5", "s6"], "c4": ["s7", "s8"]})
    with db.connect() as c:  # s3 (d1) has a mention job in this run but never joined a cluster
        system = {m.mention_id: m for m in load_system(c, run_id, load_gold(c, split="test").documents, MENTION_STAGE)}
    assert "s3" in system and system["s3"].cluster_id is None
    ev = evaluate(db, run_id, resamples=5, workers=1)
    assert ev.report["triggers"]["en"]["precision"] == pytest.approx(100)  # s3 is a true trigger
    assert ev.report["triggers"]["en"]["recall"] == pytest.approx(100)
    en = next(s for s in ev.scores if s.language == "en" and s.metric == "conll")
    assert en.f1 < 100  # but its gold chain m3 is missed by coreference (not clustered)


def test_track2_gold_equals_system_scores_100(db):
    docs = [
        {"id": "d1", "language": "en", "topic": "1", "gold": {"m1": [0, 3], "m2": [4, 7], "m3": [8, 11]},
         "mentions": {"s1": [0, 3, 0, 1], "s2": [4, 7, 1, 2], "s3": [8, 11, 2, 3]}},
        {"id": "d2", "language": "en", "topic": "1", "gold": {"m4": [0, 3]}, "mentions": {"s4": [0, 3, 0, 1]}},
        {"id": "d3", "language": "es", "topic": "2", "gold": {"m5": [0, 3], "m6": [4, 7]},
         "mentions": {"s5": [0, 3, 0, 1], "s6": [4, 7, 1, 2]}},
        {"id": "d4", "language": "tr", "topic": "3", "gold": {"m7": [0, 3], "m8": [4, 7]},
         "mentions": {"s7": [0, 3, 0, 1], "s8": [4, 7, 1, 2]}}]
    pairs = [["d1", "m1", "d1", "m2"], ["d1", "m1", "d2", "m4"], ["d1", "m2", "d2", "m4"], ["d3", "m5", "d3", "m6"],
             ["d4", "m7", "d4", "m8"]]
    clusters = {"c1": ["s1", "s2", "s4"], "c2": ["s3"], "c3": ["s5", "s6"], "c4": ["s7", "s8"]}
    ev = evaluate(db, corpus(db, track="realistic", docs=docs, pairs=pairs, clusters=clusters), resamples=20, workers=1)
    assert {s.language for s in ev.scores} == {"en", "es", "tr", "all"}
    assert [(s.language, s.metric, s.f1) for s in ev.scores if s.f1 != pytest.approx(100)] == []
    for lang, t in ev.report["triggers"].items():
        assert (t["precision"], t["recall"], t["f1"]) == pytest.approx((100, 100, 100)), lang
    assert ev.report["unmatched_system"] == {"all": 0} and ev.report["unaligned_gold"] == 0


def test_load_latest_selects_split(db):
    run_id = corpus(db)
    test_ev = evaluate(db, run_id, resamples=5, workers=1)
    dev_docs = [{"id": "d9", "language": "en", "topic": "9", "split": "dev", "gold": {"m9": [0, 3]},
                 "mentions": {"s9": [0, 3, 0, 1]}}]
    dev_run = corpus(db, docs=dev_docs, pairs=[], clusters={"c9": ["s9"]})
    assert load_latest(db, run_id) is None
    save(db, test_ev)
    dev_ev = evaluate(db, dev_run, split="dev", resamples=5, workers=1)
    save(db, dev_ev)
    assert load_latest(db, run_id).id == test_ev.id and load_latest(db, run_id, "dev") is None
    assert load_latest(db, dev_run) is None and load_latest(db, dev_run, "dev").id == dev_ev.id
    later = evaluate(db, run_id, split="test", resamples=5, workers=1)
    save(db, later)
    assert load_latest(db, run_id).id == later.id


def test_links_columns_not_compared_when_paper_defines_no_links(db):
    text = render(evaluate(db, corpus(db), resamples=5, workers=1), load_paper())
    en = next(l for l in text.splitlines() if l.startswith("en ") and "22445" in l)
    assert "/ 482 n/c" in en and "/ 2229 n/c" in en
    assert "/ 482 ≠" not in en and "/ 2229 ≠" not in en
    assert "4 / 22445 ≠" in en  # mentions still compared
    assert "not comparable (paper does not define links" in text
