"""Acceptance tests against the real MCECR dataset (set WEAVE_MCECR_PATH)."""

import json
import os
from pathlib import Path

import pytest

from weave_providers.mcecr.reader import LANGUAGES, read_topic

_ROOT = os.environ.get("WEAVE_MCECR_PATH")

pytestmark = [
    pytest.mark.mcecr,
    pytest.mark.skipif(not _ROOT, reason="WEAVE_MCECR_PATH not set"),
]

PAPER_DOCS = {"english": 2600, "spanish": 694, "turkish": 193, "ukrainian": 1123, "hindi": 1192}


def topic_files(lang):
    return sorted((Path(_ROOT).expanduser() / lang / "events").glob("*.json"))


def raw_tokens(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_all(lang):
    return [(p, read_topic(p, lang)) for p in topic_files(lang)]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_document_counts_match_paper(lang):
    count = sum(len({t["doc_ind"] for t in raw_tokens(p)}) for p in topic_files(lang))
    assert count == PAPER_DOCS[lang]
    assert sum(len(docs) for _, docs in read_all(lang)) == PAPER_DOCS[lang]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_token_and_sentence_counts_match_files(lang):
    raw_tok = raw_sent = doc_tok = doc_sent = 0
    for path, docs in read_all(lang):
        toks = raw_tokens(path)
        raw_tok += len(toks)
        raw_sent += len({(t["doc_ind"], t["sent_ind"]) for t in toks})
        doc_tok += sum(len(d.token_offsets) for d in docs)
        doc_sent += sum(len(d.sentence_offsets) for d in docs)
    assert doc_tok == raw_tok
    assert doc_sent == raw_sent


def expected_mention_texts(tokens):
    """mention id -> text, computed from raw JSON: first..last token with that id,
    joined by the original source gaps (one space on overlap or backwards)."""
    idx = {}
    for i, t in enumerate(tokens):
        if t.get("id") is not None and t.get("label", "O") != "O":
            idx.setdefault(t["id"], []).append(i)
    out = {}
    for mid, ixs in idx.items():
        parts = [tokens[ixs[0]]["text"]]
        for i in range(ixs[0] + 1, ixs[-1] + 1):
            gap = tokens[i]["span"][0] - tokens[i - 1]["span"][1]
            parts.append(" " * (gap if gap >= 0 else 1) + tokens[i]["text"])
        out[mid] = "".join(parts)
    return out


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_gold_mention_slices_back(lang):
    total = 0
    for path, docs in read_all(lang):
        by_doc = {}
        for t in raw_tokens(path):
            by_doc.setdefault(t["doc_ind"], []).append(t)
        for doc in docs:
            expected = expected_mention_texts(by_doc[doc.doc_index])
            assert {m.mention_id for m in doc.mentions} == set(expected)
            for m in doc.mentions:
                assert doc.text[m.start : m.end] == expected[m.mention_id], (path.name, doc.doc_index, m.mention_id)
                assert m.text == expected[m.mention_id]
                total += 1
    assert total > 0
