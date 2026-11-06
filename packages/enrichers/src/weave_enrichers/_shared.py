"""Copies of the extractor package's document and parse helpers: a stage package never imports another."""
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import httpx
import sqlalchemy as sa
from pydantic import BaseModel
from weave_core.adapters import AdapterFailure
from weave_core.db import make_engine
from weave_core.schema import documents
from weave_core.settings import Settings

# Copy of weave_extractors.shape.ISO_CODES.
ISO_CODES = {"english": "en", "spanish": "es", "hindi": "hi", "turkish": "tr", "ukrainian": "uk"}
LANGUAGES = ("en", "es", "hi", "tr", "uk")


@dataclass(frozen=True)
class Doc:
    """Copy of weave_extractors.shape.Doc, plus the outlet and publication time enrichers put in requests."""
    id: str
    language: str  # ISO 639-1
    title: str
    text: str
    content_hash: str
    token_offsets: list[tuple[int, int]]
    sentence_offsets: list[tuple[int, int]]
    outlet: str | None = None
    published_at: datetime | None = None


def iso(language: str) -> str:
    """Copy of weave_extractors.shape.iso."""
    return ISO_CODES.get(language.lower(), language.lower())


def load_document(doc_id: str) -> Doc:
    """Copy of weave_extractors.shape.load_document (adapters receive no engine), also reading outlet and published_at."""
    d = documents.c
    engine = make_engine(Settings.from_env().database_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.select(d.id, d.language, d.title, d.text, d.content_hash, d.token_offsets,
                                         d.sentence_offsets, d.outlet, d.published_at).where(d.id == doc_id)).first()
    finally:
        engine.dispose()
    if row is None:
        raise AdapterFailure(f"document not found: {doc_id}", retryable=False)
    return Doc(row.id, iso(row.language), row.title, row.text, row.content_hash,
               [tuple(t) for t in row.token_offsets], [tuple(s) for s in row.sentence_offsets],
               row.outlet or None, row.published_at)


def mark(sentence_text: str, rel_start: int, rel_end: int) -> str:
    """Copy of weave_extractors.candidates.mark."""
    return f"{sentence_text[:rel_start]}⟦{sentence_text[rel_start:rel_end]}⟧{sentence_text[rel_end:]}"


def mention_id(m: dict) -> str:
    """Same formula as weave_extractors.shape.Mention.id; extract output dicts carry no id key."""
    base = f"{m['start']}:{m['end']}:{m['extractor']}:{m['extractor_version']}"
    if m.get("deployment") is not None:
        base = f"{base}:{m['deployment']}"
    return hashlib.sha256(f"{m['document_id']}:{base}".encode()).hexdigest()[:32]


# Copies of weave_extractors.stanza_client.
class Word(BaseModel):
    text: str
    start: int
    end: int
    upos: str
    lemma: str | None
    feats: str | None
    deprel: str
    head: int  # 1-based index in the sentence; 0 is the root


class Entity(BaseModel):
    start: int  # document char offsets
    end: int
    type: str  # Stanza NER label, e.g. PERSON, GPE, DATE
    text: str


class Sentence(BaseModel):
    words: list[Word]
    entities: list[Entity] = []  # only when parsed with ner=True, in languages that have a NER model


class StanzaClient(Protocol):
    def parse(self, text: str, lang: str, ner: bool = False) -> list[Sentence]: ...


def check_language(lang: str) -> None:
    if lang not in LANGUAGES:
        raise AdapterFailure(f"no stanza model for language {lang!r}", retryable=False)


class HttpStanzaClient:
    def __init__(self, base_url: str | None = None, http: httpx.Client | None = None, timeout: float = 300):
        self.base_url = (base_url or os.environ.get("WEAVE_STANZA_URL") or "http://stanza:8001").rstrip("/")
        self.http = http or httpx.Client(timeout=timeout)

    def parse(self, text: str, lang: str, ner: bool = False) -> list[Sentence]:
        check_language(lang)
        body = {"text": text, "lang": lang, **({"ner": True} if ner else {})}
        try:
            response = self.http.post(f"{self.base_url}/parse", json=body)
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
    def key(text: str, lang: str, ner: bool = False) -> str:
        return hashlib.sha256((lang + text + ("\0ner" if ner else "")).encode()).hexdigest()  # unchanged unless ner

    def save(self, text: str, lang: str, sentences: list[Sentence], ner: bool = False) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{self.key(text, lang, ner)}.json").write_text(
            json.dumps([s.model_dump() for s in sentences], ensure_ascii=False))

    def parse(self, text: str, lang: str, ner: bool = False) -> list[Sentence]:
        check_language(lang)
        path = self.directory / f"{self.key(text, lang, ner)}.json"
        if not path.exists():
            raise AdapterFailure(f"no stanza fixture for {lang} text {text[:40]!r}", retryable=False)
        return [Sentence.model_validate(s) for s in json.loads(path.read_text())]
