"""Stable normalization for detecting edits to article text."""

import hashlib
import unicodedata


def normalize_for_edit(title: str, text: str) -> str:
    def normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).split())

    return normalize(title) + "\n" + normalize(text)


def edit_hash(title: str, text: str) -> str:
    return hashlib.sha256(normalize_for_edit(title, text).encode("utf-8")).hexdigest()
