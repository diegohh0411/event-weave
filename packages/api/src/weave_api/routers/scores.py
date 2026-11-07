from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from weave_api.deps import get_engine, get_paper
from weave_api.queries import runs as run_q
from weave_core.schema import evaluations
from weave_eval.compare import CompareRow, ScoreRow, compare
from weave_eval.cost import cost_report, failures
from weave_eval.paper import PaperNumbers
from weave_eval.store import load_latest
from weave_eval.tracks import SKIPPED

router = APIRouter()

MAX_COMPARE_RUNS = 8


class ScoreOut(BaseModel):
    track: str
    setting: str
    language: str
    metric: str
    precision: float | None
    recall: float | None
    f1: float
    f1_low: float | None
    f1_high: float | None


class ScoresOut(BaseModel):
    evaluation_id: UUID
    split: str
    track: str
    scorer_version: str
    created_at: datetime
    scores: list[ScoreOut]
    report: dict[str, Any]


class PRFOut(BaseModel):
    precision: float | None
    recall: float | None
    f1: float


class CompareRowOut(BaseModel):
    language: str
    setting: str
    metric: str
    ours: PRFOut | None
    ours_ci: tuple[float, float] | None
    skipped: str | None
    paper: dict[str, PRFOut | float]
    regime: str
    label: str


class CostOut(BaseModel):
    dollars_per_1000_mentions: Decimal | None
    mentions_per_hour: float | None
    total_dollars: Decimal
    any_estimated: bool


class CompareRunOut(BaseModel):
    run_id: UUID
    name: str
    track: str
    evaluated: bool
    scores: list[ScoreOut]
    trigger: dict[str, Any] | None
    cost: CostOut
    failures: dict[str, dict[str, int]]
    skipped: dict[str, str]


class CompareOut(BaseModel):
    runs: list[CompareRunOut]
    paper: list[CompareRowOut] | None
    paper_checked: bool
    paper_source: dict[str, Any]


def _score(s: ScoreRow) -> ScoreOut:
    return ScoreOut(track=s.track, setting=s.setting, language=s.language, metric=s.metric, precision=s.precision,
                    recall=s.recall, f1=s.f1, f1_low=s.f1_low, f1_high=s.f1_high)


def _prf(v):
    return PRFOut(precision=v.precision, recall=v.recall, f1=v.f1) if hasattr(v, "f1") else v


def _paper_row(r: CompareRow) -> CompareRowOut:
    return CompareRowOut(language=r.language, setting=r.setting, metric=r.metric, ours=_prf(r.ours) if r.ours else None,
                         ours_ci=r.ours_ci, skipped=r.skipped, paper={m: _prf(v) for m, v in r.paper.items()},
                         regime=r.regime, label=r.label)


@router.get("/runs/{run_id}/scores")
def run_scores(run_id: UUID, engine=Depends(get_engine), split: str = "test") -> ScoresOut:
    with engine.connect() as conn:
        run_q.run_row(conn, run_id)
    ev = load_latest(engine, run_id, split)
    if ev is None:
        raise HTTPException(404, f"run {run_id} has no {split} evaluation; run weave eval {run_id}")
    with engine.connect() as conn:
        created = conn.scalar(sa.select(evaluations.c.created_at).where(evaluations.c.id == ev.id))
    return ScoresOut(evaluation_id=ev.id, split=ev.split, track=ev.track, scorer_version=ev.report["scorer_version"],
                     created_at=created, scores=[_score(s) for s in ev.scores], report=ev.report)


def _trigger(report: dict) -> dict | None:
    # The scorer writes report["triggers"]; report["track2"]["trigger"] is the documented shape.
    return (report.get("track2") or {}).get("trigger") or report.get("triggers")


@router.get("/compare")
def compare_runs(run_id: Annotated[list[UUID], Query(min_length=1, max_length=MAX_COMPARE_RUNS)],
                 engine=Depends(get_engine), paper: PaperNumbers = Depends(get_paper), split: str = "test") -> CompareOut:
    skipped = {f"{lang}/{setting}": why for (lang, setting), why in SKIPPED.items()}
    out: list[CompareRunOut] = []
    paper_scores: list[ScoreRow] | None = None
    for rid in run_id:
        with engine.connect() as conn:
            row = run_q.run_row(conn, rid)
        ev = load_latest(engine, rid, split)
        if row.track == "paper" and paper_scores is None:
            paper_scores = ev.scores if ev else []
        cost = cost_report(engine, rid)
        out.append(CompareRunOut(
            run_id=rid, name=row.name, track=row.track, evaluated=ev is not None,
            scores=[_score(s) for s in ev.scores] if ev else [], trigger=_trigger(ev.report) if ev else None,
            cost=CostOut(dollars_per_1000_mentions=cost.dollars_per_1000_mentions, mentions_per_hour=cost.mentions_per_hour,
                         total_dollars=cost.total_dollars, any_estimated=cost.any_estimated),
            failures=failures(engine, rid), skipped=skipped))
    rows = None if paper_scores is None else [_paper_row(r) for r in compare(paper_scores, paper)]
    return CompareOut(runs=out, paper=rows, paper_checked=paper.checked, paper_source=paper.source)
