"""Fetching seam for the crawler: a Fetcher returns a page or a reason it failed."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    html: str | None
    error: str | None


class Fetcher(Protocol):
    def fetch(self, url: str) -> Fetched: ...


class FixtureFetcher:
    """Serves saved files or canned responses; never touches the network."""

    def __init__(self, routes: dict[str, "Fetched | Path"]):
        self.routes = routes
        self.calls: list[str] = []

    def fetch(self, url: str) -> Fetched:
        self.calls.append(url)
        if url not in self.routes:
            raise AssertionError(f"unexpected fetch: {url}")
        route = self.routes[url]
        if isinstance(route, Path):
            return Fetched(url, 200, route.read_text(encoding="utf-8"), None)
        return route
