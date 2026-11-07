"""The MCECR paper's published numbers (versioned YAML), loaded and validated."""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

DEFAULT_PATH = Path(str(resources.files("weave_eval") / "data" / "mcecr_paper_v1.yaml"))
METRICS = ("muc", "b3", "ceafe", "blanc", "conll")
SETTINGS = ("within", "cross")
REGIMES = ("monolingual", "transfer")


@dataclass(frozen=True)
class PaperTable:
    table: int
    page: int
    setting: str
    regime: str
    rows: list[dict]


@dataclass(frozen=True)
class PaperNumbers:
    version: int
    source: dict
    transcribed_by: str
    checked_by: list[str]
    annotation_stats: dict[str, dict]
    tables: list[PaperTable] = field(default_factory=list)

    @property
    def checked(self) -> bool:
        """True only when two distinct people have checked the transcription."""
        return len({c.strip() for c in self.checked_by if c and c.strip()}) >= 2


def _check_value(v, where: str) -> None:
    if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100):
        raise ValueError(f"{where}: {v!r} is not null or a percentage in 0-100")


def load_paper(path: Path | str | None = None) -> PaperNumbers:
    data = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    for key in ("version", "source", "transcribed_by", "checked_by", "annotation_stats", "tables"):
        if key not in data:
            raise ValueError(f"paper file is missing {key!r}")
    if not data["source"].get("url") or not data["source"].get("anthology_id"):
        raise ValueError("paper file source needs url and anthology_id")
    tables = []
    for t in data["tables"]:
        if not isinstance(t.get("page"), int):
            raise ValueError(f"table {t.get('table')} has no page")
        if t["setting"] not in SETTINGS or t["regime"] not in REGIMES:
            raise ValueError(f"table {t['table']}: bad setting or regime")
        for row in t["rows"]:
            for metric in METRICS:
                cell = row.get(metric)
                for k in ("p", "r", "f1"):
                    _check_value((cell or {}).get(k), f"table {t['table']} {row['language']} {row['model']} {metric}.{k}")
        tables.append(PaperTable(t["table"], t["page"], t["setting"], t["regime"], t["rows"]))
    for lang, st in data["annotation_stats"].items():
        if not isinstance(st.get("page"), int):
            raise ValueError(f"annotation_stats {lang} has no page")
    return PaperNumbers(data["version"], data["source"], data["transcribed_by"], list(data["checked_by"]),
                        data["annotation_stats"], tables)
