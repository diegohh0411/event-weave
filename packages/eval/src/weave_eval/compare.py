"""Our Track 1 scores beside the paper's published numbers. The two are never merged or subtracted here."""
from __future__ import annotations

from dataclasses import dataclass, field

from weave_eval.metrics import PRF
from weave_eval.paper import METRICS, PaperNumbers
from weave_eval.tracks import SKIPPED

NO_PAIRED_TEST = ("The paper published no system outputs, so no paired significance test against it "
                  "is possible.")
LABELS = {
    "transfer": "like for like (both tuned on English only)",
    "monolingual": "our setting is harder (we are tuned on English dev only)",
}


@dataclass(frozen=True)
class ScoreRow:
    """One `evaluation_scores` row without its evaluation_id."""
    track: str
    setting: str
    language: str
    metric: str
    precision: float | None
    recall: float | None
    f1: float
    f1_low: float | None = None
    f1_high: float | None = None


@dataclass(frozen=True)
class CompareRow:
    language: str
    setting: str
    metric: str
    ours: PRF | None
    ours_ci: tuple[float, float] | None
    skipped: str | None
    paper: dict[str, PRF | float] = field(default_factory=dict)
    regime: str = "monolingual"
    label: str = ""


def _paper_value(cell: dict | None) -> PRF | float | None:
    if not cell or cell.get("f1") is None:
        return None
    if cell.get("p") is not None and cell.get("r") is not None:
        return PRF(cell["p"], cell["r"], cell["f1"])
    return cell["f1"]


def compare(scores: list[ScoreRow], paper: PaperNumbers) -> list[CompareRow]:
    """Track 1 only (track 'paper'). The paper prints F1 only, so paper values are floats (model -> F1)."""
    ours = {(s.language, s.setting, s.metric): s for s in scores if s.track == "paper"}
    printed: dict[tuple, dict] = {}
    for t in paper.tables:
        for row in t.rows:
            for metric in METRICS:
                cell = printed.setdefault((row["language"], t.setting, metric, t.regime), {})
                v = _paper_value(row.get(metric))
                if v is not None:
                    cell[row["model"]] = v
    keys = set(printed)
    for lang, setting, metric in ours:  # our numbers apply to both regimes; English has no transfer
        keys.add((lang, setting, metric, "monolingual"))
        if lang != "en":
            keys.add((lang, setting, metric, "transfer"))
    out = []
    for lang, setting, metric, regime in sorted(keys):
        s = ours.get((lang, setting, metric))
        skipped = SKIPPED.get((lang, setting))
        if skipped:
            s = None
        out.append(CompareRow(
            language=lang, setting=setting, metric=metric,
            ours=PRF(s.precision, s.recall, s.f1) if s else None,
            ours_ci=(s.f1_low, s.f1_high) if s and s.f1_low is not None and s.f1_high is not None else None,
            skipped=skipped, paper=printed.get((lang, setting, metric, regime), {}),
            regime=regime, label=LABELS[regime]))
    return out
