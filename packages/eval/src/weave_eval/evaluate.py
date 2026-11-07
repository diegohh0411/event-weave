"""Score a finished run against gold: Track 1 for `paper` runs, Track 2 for `realistic` runs."""
from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

from weave_core.config import RunConfig
from weave_core.schema import jobs, runs
from weave_core.stages import PIPELINE, Stage, UnitKind
from weave_eval import SCORER_VERSION
from weave_eval.bootstrap import interval_additive, interval_pooled
from weave_eval.compare import NO_PAIRED_TEST, ScoreRow
from weave_eval.cost import cost_report, failures
from weave_eval.gold import chain_stats, gold_hash, load_gold
from weave_eval.metrics import METRICS, Counts, Scores, scores
from weave_eval.system import load_system
from weave_eval.tracks import SKIPPED, TRACK2_MENTION_POLICY, cluster_spread, track1, track2, trigger_scores


class Refused(Exception):
    """A run that cannot be scored."""


class NotFinished(Refused):
    pass


class NoGold(Refused):
    pass


@dataclass
class Evaluation:
    id: uuid.UUID
    run_id: uuid.UUID
    track: str
    split: str
    scores: list[ScoreRow]
    report: dict


def _json(value):
    if isinstance(value, Decimal):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _json(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _rows(track: str, setting: str, language: str, total: Counts, interval: dict) -> list[ScoreRow]:
    s: Scores = scores(total)
    out = []
    for m in METRICS:
        lo, hi = interval[m]
        if m == "conll":
            out.append(ScoreRow(track, setting, language, m, None, None, s.conll, lo, hi))
        else:
            prf = getattr(s, m)
            out.append(ScoreRow(track, setting, language, m, prf.precision, prf.recall, prf.f1, lo, hi))
    return out


def _sum(rows) -> Counts:
    total = Counts.zero()
    for c in rows:
        total = total + c
    return total


def evaluate(engine, run_id, *, split: str = "test", resamples: int = 10_000,
             workers: int | None = None) -> Evaluation:
    with engine.connect() as c:
        run = c.execute(sa.select(runs.c.config, runs.c.config_hash, runs.c.state).where(runs.c.id == run_id)).one_or_none()
        if run is None:
            raise Refused(f"run {run_id} does not exist")
        if run.state != "done":
            raise NotFinished(f"run {run_id} is {run.state}; only finished runs can be scored")
        cfg = RunConfig.model_validate(run.config)
        doc_ids = set(c.scalars(sa.select(jobs.c.unit_id).where(
            jobs.c.run_id == run_id, jobs.c.stage == Stage.extract.value, jobs.c.unit_kind == UnitKind.document.value)))
        gold = load_gold(c, split=split, document_ids=doc_ids)
        if not gold.documents:
            raise NoGold(f"run {run_id} has no gold documents in split {split}")
        # the paper's annotation statistics cover the whole dataset, so chain stats do too
        whole = load_gold(c, languages={lang for lang, _ in gold.documents.values()})
        system = load_system(c, run_id, gold.documents)

    seed = cfg.seed
    rows: list[ScoreRow] = []
    report: dict = {"track": cfg.track.value, "stages": [s.value for s in PIPELINE if s in cfg.stages],
                    "skipped": {f"{lang}/{setting}": why for (lang, setting), why in SKIPPED.items()},
                    "no_paired_test": NO_PAIRED_TEST}
    if cfg.track.value == "paper":
        result = track1(gold, system)
        for lang in sorted(result):
            r = result[lang]
            for setting, by_topic in (("within", r.within), ("cross", r.cross)):
                if by_topic:
                    rows += _rows("paper", setting, lang, _sum(by_topic.values()),
                                  interval_additive(by_topic, resamples, seed))
        report["unmatched_system"] = {lang: r.unmatched_system for lang, r in sorted(result.items())}
    else:
        result2 = track2(gold, system)
        for lang, total in result2.per_language.items():
            rows += _rows("realistic", "pooled", lang, total,
                          interval_pooled(result2.inputs, lang, resamples, seed, workers))
        rows += _rows("realistic", "pooled", "all", result2.pooled,
                      interval_pooled(result2.inputs, None, resamples, seed, workers))
        report["track2_mention_policy"] = TRACK2_MENTION_POLICY
        report["unaligned_gold"] = result2.unaligned_gold
        report["unmatched_system"] = {"all": result2.unmatched_system}
        report["triggers"] = _json(trigger_scores(gold, system))
        report["spread"] = _json(cluster_spread(system, gold))
    report.update(chain_stats=_json(chain_stats(whole)), failures=failures(engine, run_id),
                  cost=_json(cost_report(engine, run_id)), gold_hash=gold_hash(gold),
                  config_hash=run.config_hash, scorer_version=SCORER_VERSION, seed=seed, resamples=resamples)
    return Evaluation(uuid.uuid4(), run_id, cfg.track.value, split, rows, report)
