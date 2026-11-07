"""Plain-text report of one evaluation."""
from __future__ import annotations

from weave_eval.compare import compare
from weave_eval.evaluate import Evaluation
from weave_eval.metrics import PRF
from weave_eval.paper import PaperNumbers
from weave_eval.tracks import SKIPPED

CHAIN_FIELDS = ("mentions", "chains", "non_singleton", "within_links", "cross_links")


def _table(head: list[str], body: list[list[str]]) -> list[str]:
    widths = [max(len(r[i]) for r in [head, *body]) for i in range(len(head))]
    return ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in [head, *body]]


def _num(v) -> str:
    return "-" if v is None else f"{v:.2f}"


def _ci(lo, hi) -> str:
    return "-" if lo is None or hi is None else f"[{lo:.2f}, {hi:.2f}]"


def _scores(ev: Evaluation) -> list[str]:
    body = [[s.setting, s.language, s.metric, _num(s.precision), _num(s.recall), _num(s.f1), _ci(s.f1_low, s.f1_high)]
            for s in ev.scores]
    out = _table(["setting", "language", "metric", "P", "R", "F1", "95% CI of F1"], body)
    langs = {s.language for s in ev.scores}
    for (lang, setting), reason in SKIPPED.items():
        if ev.track == "paper":
            out.append(f"{lang} {setting}: {reason}")
        elif lang not in langs:
            out.append(f"{lang} pooled: {reason}")
    triggers = ev.report.get("triggers")
    if triggers:
        out += ["", "Triggers (exact token span)"]
        out += _table(["language", "P", "R", "F1"],
                      [[lang, _num(t["precision"]), _num(t["recall"]), _num(t["f1"])] for lang, t in triggers.items()])
    return out


def _failures(ev: Evaluation) -> list[str]:
    failed = ev.report["failures"]
    body = [[stage, str(failed.get(stage, {}).get("documents", 0)), str(failed.get(stage, {}).get("mentions", 0))]
            for stage in ev.report["stages"]]
    out = _table(["stage", "failed documents", "failed mentions"], body)
    unmatched = ev.report.get("unmatched_system", {})
    out.append("unmatched system mentions: " + ", ".join(f"{k}={v}" for k, v in unmatched.items()))
    if "unaligned_gold" in ev.report:
        out.append(f"unaligned gold mentions (kept as misses): {ev.report['unaligned_gold']}")
    return out


def _chain_stats(ev: Evaluation, paper: PaperNumbers) -> list[str]:
    body, notes = [], []
    for lang, ours in sorted(ev.report["chain_stats"].items()):
        theirs = paper.annotation_stats.get(lang, {})
        cells = []
        for f in CHAIN_FIELDS:
            o, p = ours[f], theirs.get(f)
            cells.append(f"{o} / {'-' if p is None else p}" + (" ≠" if p is not None and o != p else ""))
        body.append([lang, *cells, str(ours["skipped_pairs"])])
        if ours.get("note"):
            notes.append(f"{lang}: {ours['note']}")
    out = ["ours / paper; ≠ marks a mismatch"]
    out += _table(["language", *CHAIN_FIELDS, "skipped pairs"], body)
    return out + notes


def _compare(ev: Evaluation, paper: PaperNumbers) -> list[str]:
    body = []
    for r in compare(ev.scores, paper):
        if r.metric != "conll" and not r.paper and r.ours is None and not r.skipped:
            continue
        if r.skipped:
            ours = r.skipped
        else:
            ours = "-" if r.ours is None else _num(r.ours.f1) + (" " + _ci(*r.ours_ci) if r.ours_ci else "")
        theirs = ", ".join(f"{m}={_num(v.f1 if isinstance(v, PRF) else v)}" for m, v in sorted(r.paper.items())) or "-"
        body.append([r.language, r.setting, r.metric, r.regime, ours, theirs, r.label])
    return _table(["language", "setting", "metric", "regime", "ours F1", "paper F1", "reading"], body)


def _cost(ev: Evaluation) -> list[str]:
    c = ev.report["cost"]
    body = [[r["stage"], r["deployment"], str(r["calls"]), str(r["errors"]), str(r["input_tokens"]),
             str(r["output_tokens"]), r["dollars"] + ("*" if r["estimated_dollars"] != "0" and float(r["estimated_dollars"]) else ""),
             _num(r["p50_ms"]), _num(r["p95_ms"])] for r in c["rows"]]
    out = _table(["stage", "deployment", "calls", "errors", "in tok", "out tok", "$", "p50 ms", "p95 ms"], body)
    out.append(f"total ${c['total_dollars']}" + (" (includes estimated costs, marked *)" if c["any_estimated"] else ""))
    per = c["dollars_per_1000_mentions"]
    out.append(f"mentions {c['mentions']}  $/1k mentions {'-' if per is None else per}  "
               f"mentions/hour {_num(c['mentions_per_hour'])}  "
               f"cache share {'-' if c['cache_share'] is None else format(c['cache_share'], '.1%')}")
    return out


def render(ev: Evaluation, paper: PaperNumbers) -> str:
    r = ev.report
    lines = [f"run {ev.run_id}  track {ev.track}  split {ev.split}",
             f"config {r['config_hash']}  gold {r['gold_hash']}  scorer v{r['scorer_version']}  "
             f"seed {r['seed']}  resamples {r['resamples']}"]
    if not paper.checked:
        lines.append("paper numbers: UNCHECKED (the transcription needs two distinct checkers)")
    sections = [("Scores", _scores(ev)), ("Failures", _failures(ev)),
                ("Chain statistics", _chain_stats(ev, paper))]
    if ev.track == "paper":
        sections.append(("Comparison with the paper", _compare(ev, paper)))
    else:
        spread = r["spread"]
        sections.append(("Track 2 mention policy", [
            r["track2_mention_policy"],
            f"clusters {spread['clusters']}  spanning topics {spread['multi_topic']}  "
            f"spanning languages {spread['multi_language']}"]))
    sections += [("Significance", [r["no_paired_test"]]), ("Cost", _cost(ev))]
    for title, body in sections:
        lines += ["", f"== {title} ==", *body]
    return "\n".join(lines) + "\n"
