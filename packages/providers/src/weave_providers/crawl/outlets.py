from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Outlet:
    name: str
    domain: str
    language: str
    sitemaps: list[str] = field(default_factory=list)
    feeds: list[str] = field(default_factory=list)
    per_day: int | None = None

    def __post_init__(self):
        if not self.sitemaps and not self.feeds:
            raise ValueError(f"outlet {self.name!r} needs at least one sitemap or feed")


def load_outlets(path: Path) -> list[Outlet]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [Outlet(**o) for o in data.get("outlets", [])]
