"""Persist and reload evaluations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from weave_core.schema import evaluation_scores, evaluations
from weave_eval.compare import ScoreRow
from weave_eval.evaluate import Evaluation


def save(engine, ev: Evaluation) -> uuid.UUID:
    r = ev.report
    with engine.begin() as c:
        c.execute(sa.insert(evaluations).values(
            id=ev.id, run_id=ev.run_id, config_hash=r["config_hash"], gold_hash=r["gold_hash"],
            scorer_version=r["scorer_version"], split=ev.split, seed=r["seed"], resamples=r["resamples"],
            created_at=datetime.now(timezone.utc), report=r))
        if ev.scores:
            c.execute(sa.insert(evaluation_scores), [
                dict(evaluation_id=ev.id, track=s.track, setting=s.setting, language=s.language, metric=s.metric,
                     precision=s.precision, recall=s.recall, f1=s.f1, f1_low=s.f1_low, f1_high=s.f1_high)
                for s in ev.scores])
    return ev.id


def load_latest(engine, run_id) -> Evaluation | None:
    with engine.connect() as c:
        row = c.execute(sa.select(evaluations).where(evaluations.c.run_id == run_id)
                        .order_by(evaluations.c.created_at.desc()).limit(1)).one_or_none()
        if row is None:
            return None
        rows = c.execute(sa.select(evaluation_scores).where(evaluation_scores.c.evaluation_id == row.id)
                         .order_by(evaluation_scores.c.track, evaluation_scores.c.setting,
                                   evaluation_scores.c.language, evaluation_scores.c.metric))
        scores = [ScoreRow(s.track, s.setting, s.language, s.metric, s.precision, s.recall, s.f1, s.f1_low, s.f1_high)
                  for s in rows]
    return Evaluation(row.id, row.run_id, row.report["track"], row.split, scores, row.report)
