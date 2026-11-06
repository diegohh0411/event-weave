"""Helpers for the document shape stages see."""

import hashlib


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def opaque_id(*parts: str) -> str:
    return hashlib.sha256("/".join(parts).encode()).hexdigest()[:20]
