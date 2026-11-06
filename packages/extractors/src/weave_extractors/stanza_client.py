"""Talking to the stanza service, plus a fixture client for tests."""
import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel
from weave_core.adapters import AdapterFailure

LANGUAGES = ("en", "es", "hi", "tr", "uk")


class Word(BaseModel):
    text: str
    start: int
    end: int
    upos: str
    lemma: str | None
    feats: str | None
    deprel: str
    head: int  # 1-based index in the sentence; 0 is the root


class Sentence(BaseModel):
    words: list[Word]


class StanzaClient(Protocol):
    def parse(self, text: str, lang: str) -> list[Sentence]: ...


def check_language(lang: str) -> None:
    if lang not in LANGUAGES:
        raise AdapterFailure(f"no stanza model for language {lang!r}", retryable=False)


class HttpStanzaClient:
    def __init__(self, base_url: str | None = None, http: httpx.Client | None = None, timeout: float = 300):
        self.base_url = (base_url or os.environ.get("WEAVE_STANZA_URL") or "http://stanza:8001").rstrip("/")
        self.http = http or httpx.Client(timeout=timeout)

    def parse(self, text: str, lang: str) -> list[Sentence]:
        check_language(lang)
        try:
            response = self.http.post(f"{self.base_url}/parse", json={"text": text, "lang": lang})
        except httpx.HTTPError as exc:
            raise AdapterFailure(f"stanza service unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise AdapterFailure(f"stanza service error {response.status_code}: {response.text[:200]}",
                                 retryable=response.status_code >= 500)
        return [Sentence.model_validate(s) for s in response.json()["sentences"]]


class FixtureStanzaClient:
    """Serves saved parses from `<dir>/<sha256(lang + text)>.json`."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    @staticmethod
    def key(text: str, lang: str) -> str:
        return hashlib.sha256((lang + text).encode()).hexdigest()

    def save(self, text: str, lang: str, sentences: list[Sentence]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{self.key(text, lang)}.json").write_text(
            json.dumps([s.model_dump() for s in sentences], ensure_ascii=False))

    def parse(self, text: str, lang: str) -> list[Sentence]:
        check_language(lang)
        path = self.directory / f"{self.key(text, lang)}.json"
        if not path.exists():
            raise AdapterFailure(f"no stanza fixture for {lang} text {text[:40]!r}", retryable=False)
        return [Sentence.model_validate(s) for s in json.loads(path.read_text())]
