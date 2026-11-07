"""Regenerate tests/fixtures/reference/expected.json with the reference CoNLL scorer (v8.01).

usage: python tools/reference_scores.py <path to reference-coreference-scorers checkout at v8.01>

Each fixture (tests/fixtures/reference/<name>.json, {"documents": [{"id", "key", "response"}]}) is written as one
CoNLL key file and one response file, one `#begin document` per fixture document, one token per mention. The scorer is
run as `perl scorer.pl <metric> key response none`, which prints corpus totals over all documents in the file.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "reference"
METRICS = ("muc", "bcub", "ceafe", "blanc")
PAIR = r"\(([-\d.e]+) / ([-\d.e]+)\) [\d.]+%"
LINE = re.compile(rf"Recall: {PAIR}\tPrecision: {PAIR}\tF1: ([\d.]+)%")


def conll(documents: list[dict], side: str) -> str:
    lines = []
    for d in documents:
        mentions = sorted(set(d["key"]) | set(d["response"]))
        labels: dict[str, int] = {}
        lines.append(f"#begin document ({d['id']}); part 000")
        for i, m in enumerate(mentions):
            col = "-"
            if m in d[side]:
                col = f"({labels.setdefault(d[side][m], len(labels))})"
            lines.append("\t".join([d["id"], "0", str(i), m, "-", "-", "-", "-", str(i), "-", "-", "-", col]))
        lines += ["#end document", ""]
    return "\n".join(lines) + "\n"


def run(scorer: Path, metric: str, key: Path, response: Path) -> list[tuple]:
    out = subprocess.run(["perl", "scorer.pl", metric, str(key), str(response), "none"], cwd=scorer, check=True,
                         capture_output=True, text=True).stdout
    totals = out.split("====== TOTALS =======", 1)[1]
    return [tuple(float(x) for x in m.groups()) for m in LINE.finditer(totals)]


def prf(rn, rd, pn, pd, f1=None):
    r = rn / rd if rd else 0.0
    p = pn / pd if pd else 0.0
    f = f1 if f1 is not None else (2 * p * r / (p + r) if p + r else 0.0)
    return {"precision": 100 * p, "recall": 100 * r, "f1": 100 * f}


def score_fixture(scorer: Path, documents: list[dict]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        key, response = Path(tmp) / "key.conll", Path(tmp) / "response.conll"
        key.write_text(conll(documents, "key"))
        response.write_text(conll(documents, "response"))
        result = {}
        for metric in ("muc", "bcub", "ceafe"):
            # the last line is the corpus coreference line (the first is mention identification)
            lines = run(scorer, metric, key, response)
            rn, rd, pn, pd, _ = lines[-1]
            result[metric] = prf(rn, rd, pn, pd)
        lines = run(scorer, "blanc", key, response)
        # identification, coreference links, non-coreference links, BLANC (R, 1, P, 1, F1 as a truncated percent)
        (_, _, _, _, _), (cc, kc, _, rc, _), (cn, kn, _, rn_, _), (br, _, bp, _, bf) = lines[-4:]
        result["blanc"] = {"precision": 100 * bp, "recall": 100 * br, "f1": bf}
        result["blanc_counts"] = {"cc": cc, "kc": kc, "rc": rc, "cn": cn, "kn": kn, "rn": rn_}
        return result


def main(scorer: Path) -> None:
    expected = {}
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == "expected.json":
            continue
        expected[path.stem] = score_fixture(scorer, json.loads(path.read_text())["documents"])
    (FIXTURES / "expected.json").write_text(json.dumps(expected, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
