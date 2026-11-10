"""Helpers for the document shape stages see."""

import hashlib
import re


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def opaque_id(*parts: str) -> str:
    return hashlib.sha256("/".join(parts).encode()).hexdigest()[:20]


_TOKEN = re.compile(r"\w+|[^\w\s]")
_SENTENCE_END = re.compile(r"[.!?…]+[»\"')\]”’]*(?=\s)")


def tokenize(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Regex tokens and sentences as (start, end) offsets into `text`."""
    tokens = [m.span() for m in _TOKEN.finditer(text)]
    sentences: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        sentences.append((start, m.end()))
        nxt = re.compile(r"\S").search(text, m.end())
        start = nxt.start() if nxt else len(text)
    if text[start:].strip():
        sentences.append((start, len(text.rstrip())))
    return tokens, sentences

# Shared with migrations, which cannot depend on the providers package.
from weave_core.textnorm import edit_hash, normalize_for_edit  # noqa: F401, E402
