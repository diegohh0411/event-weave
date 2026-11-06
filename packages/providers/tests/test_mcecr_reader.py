import json
import logging
from pathlib import Path

import pytest

from weave_providers.mcecr.reader import (
    file_sha256, read_pairs, read_splits, read_topic,
)
from weave_providers.shape import content_hash, opaque_id

ROOT = Path(__file__).parent / "fixtures" / "mcecr"
T1 = ROOT / "english/events/t1.json"
T2 = ROOT / "english/events/t2.json"


def tokens_of(path, doc_ind):
    return [t["text"] for t in json.loads(path.read_text()) if t["doc_ind"] == doc_ind]


@pytest.mark.parametrize("path,lang", [
    (T1, "english"), (T2, "english"),
    (ROOT / "hindi/events/h1.json", "hindi"),
    (ROOT / "spanish/events/s1.json", "spanish"),
])
def test_every_token_and_mention_slices_back(path, lang):
    for doc in read_topic(path, lang):
        for (s, e), tok in zip(doc.token_offsets, tokens_of(path, doc.doc_index)):
            assert doc.text[s:e] == tok
        for m in doc.mentions:
            assert doc.text[m.start:m.end] == m.text


def test_offsets_are_document_relative():
    docs = read_topic(T1, "english")
    assert [d.doc_index for d in docs] == [0, 1]
    assert docs[1].token_offsets[0][0] == 0
    assert docs[1].text == "Obama visited Hawaii ."


def test_multi_token_mention_is_one_span():
    doc = read_topic(T1, "english")[0]
    assert [m.text for m in doc.mentions if m.mention_id == "T19"] == ["Pearl Harbor Day"]


def test_sentence_offsets():
    doc = read_topic(T1, "english")[0]
    assert [doc.text[s:e] for s, e in doc.sentence_offsets] == [
        "Pearl Harbor Day was observed .", "Leaders spoke .",
    ]


def test_overlapping_spans_still_round_trip():
    (doc,) = read_topic(T2, "english")
    # fire/burned overlap in the source: one space is inserted, nothing is lost
    assert doc.text == "A bigfire burned  down."
    assert [m.text for m in doc.mentions] == ["fire"]
    assert all(a[1] <= b[0] for a, b in zip(doc.token_offsets, doc.token_offsets[1:]))


def test_source_gap_kept_and_zero_gap_kept():
    (doc,) = read_topic(T2, "english")
    # "down" starts 2 after "burned" ends; "." is glued to "down"; "fire" glued to "big"
    assert "burned  down" in doc.text and doc.text.endswith("down.") and "bigfire" in doc.text


def test_both_pair_formats(tmp_path):
    recs = read_pairs(ROOT / "english/withinDoc/train.json")
    arr = tmp_path / "a.json"
    arr.write_text(json.dumps(recs))
    jl = tmp_path / "b.json"
    jl.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert read_pairs(arr) == read_pairs(jl) == recs
    assert len(recs) == 1


def test_topic_split_and_hindi_crossdoc_skipped():
    splits, pairs, skipped = read_splits(ROOT, "hindi")
    assert splits == {"h1": "train"} and skipped == 2
    assert [p.kind for p in pairs] == ["within"]


def test_split_conflict_takes_test_and_does_not_raise(caplog):
    with caplog.at_level(logging.WARNING):
        splits, pairs, skipped = read_splits(ROOT, "english")
    assert splits == {"t1": "test"} and skipped == 0
    assert "t1" in caplog.text
    cross = [p for p in pairs if p.kind == "cross"]
    assert (cross[0].source_doc, cross[0].target_doc) == (0, 1)
    assert {p.kind for p in pairs} == {"within", "cross"}


def test_missing_language_dir_is_empty(tmp_path):
    assert read_splits(tmp_path, "english") == ({}, [], 0)


def test_hashes(tmp_path):
    f = tmp_path / "x"
    f.write_bytes(b"abc")
    assert file_sha256(f) == content_hash("abc")
    assert opaque_id("a", "b") == opaque_id("a", "b")
    assert len(opaque_id("a", "b")) == 20
