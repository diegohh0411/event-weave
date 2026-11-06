import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from weave_core.adapters import AdapterContext, AdapterFailure, UnitRef
from weave_core.stages import Stage, UnitKind
from weave_providers.mcecr.adapter import McecrProvider, McecrSettings, interleave

FIX = Path(__file__).parent / "fixtures" / "mcecr"


def tok(text, start, doc, sent=0):
    return {"text": text, "span": [start, start + len(text)], "label": "O",
            "sent_ind": sent, "doc_ind": doc}


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "mcecr"
    shutil.copytree(FIX, root)
    (root / ".DS_Store").write_bytes(b"x")
    # t3 appears in no pair file: it has no split
    (root / "english/events/t3.json").write_text(json.dumps([tok("Lonely", 0, 0)]))
    # t4 holds a document whose text equals t1's second document exactly
    (root / "english/events/t4.json").write_text(json.dumps(
        [tok(w, i, 0) for w, i in (("Obama", 0), ("visited", 6), ("Hawaii", 14), (".", 21))]))
    (root / "english/crossDoc/dev.json").write_text("".join(
        json.dumps({"topic_id": t, "doc_ids": [0, 0], "source_id": "T1",
                    "target_id": "T2", "label": 0}) + "\n" for t in ("t2", "t4")))
    # same topic id as english t1, in another language
    shutil.copy(root / "spanish/events/s1.json", root / "spanish/events/t1.json")
    (root / "spanish/withinDoc").mkdir(exist_ok=True)
    (root / "spanish/withinDoc/test.json").write_text(
        json.dumps({"topic_id": "t1", "doc_ids": [0], "source_id": "T1",
                    "target_id": "T2", "label": 1}) + "\n")
    return root


def run(root, seed=0, **kw):
    settings = McecrSettings(path=str(root), **kw)
    ctx = AdapterContext(run_id=uuid4(), seed=seed, deployments={})
    unit = UnitRef(UnitKind.run, "r", "h")
    return McecrProvider(settings, ctx).process(unit, {})


def ids(result):
    return [d.id for d in result.documents]


@pytest.fixture
def result(dataset):
    return run(dataset)


def test_same_seed_same_order_other_seed_differs(dataset):
    kw = {"splits": ["train", "dev", "test"]}
    assert ids(run(dataset, 1, **kw)) == ids(run(dataset, 1, **kw))
    assert any(ids(run(dataset, s, **kw)) != ids(run(dataset, 1, **kw)) for s in range(2, 8))


def test_interleave_keeps_file_order_within_topic_and_rarely_repeats_topic():
    out = interleave({"a": [1, 2, 3], "b": [4, 5, 6], "c": [7, 8, 9]}, seed=0)
    topic = lambda x: (x - 1) // 3
    assert [x for x in out if x < 4] == [1, 2, 3]
    assert all(topic(x) != topic(y) for x, y in zip(out, out[1:]))


def test_interleave_topic_limit_keeps_first_after_shuffle():
    full = {"a": [1], "b": [2], "c": [3]}
    assert interleave(full, 3, topic_limit=2) == interleave(full, 3)[:2]


def test_documents_reveal_no_topic_or_split(result):
    assert result.documents
    for d in result.documents:
        assert "t1" not in d.id + d.external_id and "test" not in d.external_id
        assert (d.title, d.byline, d.outlet, d.url, d.published_at) == ("", "", "", "", None)
        assert d.provider == "mcecr" and d.language == "en"


def test_gold_rows_and_partition_hint(result):
    assert {r["topic"] for r in result.rows["gold_documents"]} == {"t1"}
    assert {r["split"] for r in result.rows["gold_documents"]} == {"test"}
    assert all(u.partition_hint == "en/t1" for u in result.emits)
    assert [u.order_key for u in result.emits] == [d.order_key for d in result.documents]
    doc_ids = set(ids(result))
    assert {r["document_id"] for r in result.rows["gold_mentions"]} <= doc_ids
    for p in result.rows["gold_pairs"]:
        assert p["source_document_id"] in doc_ids and p["target_document_id"] in doc_ids
    assert {p["kind"] for p in result.rows["gold_pairs"]} == {"within", "cross"}


def test_unsplit_topic_excluded_and_reported(result):
    assert result.output["topics_without_split"] == {"english": ["t3"]}
    assert result.output["documents"] == len(result.documents) == 2
    assert result.output["files"]["english/events/t1.json"]


def test_identical_text_in_two_topics_gets_two_ids(dataset):
    r = run(dataset, splits=["test", "dev"])
    by_topic = {g["document_id"]: g["topic"] for g in r.rows["gold_documents"]}
    t1 = next(d for d in r.documents if by_topic[d.id] == "t1" and d.text == "Obama visited Hawaii .")
    t4 = next(d for d in r.documents if by_topic[d.id] == "t4")
    assert t1.text == t4.text and t1.content_hash == t4.content_hash
    assert t1.id != t4.id and t1.external_id != t4.external_id


def test_same_topic_id_in_two_languages_gets_two_partitions(dataset):
    r = run(dataset, languages=["english", "spanish"])
    assert {u.partition_hint for u in r.emits} == {"en/t1", "es/t1"}
    assert {g["topic"] for g in r.rows["gold_documents"]} == {"t1"}


def test_only_known_language_directories_and_unknown_language_fails(dataset):
    with pytest.raises(AdapterFailure) as e:
        run(dataset, languages=[".DS_Store"])
    assert not e.value.retryable


def test_path_expands_environment_and_unset_fails(dataset, monkeypatch):
    monkeypatch.setenv("WEAVE_MCECR_PATH", str(dataset))
    ctx = AdapterContext(run_id=uuid4(), seed=0, deployments={})
    r = McecrProvider(McecrSettings(), ctx).process(UnitRef(UnitKind.run, "r", "h"), {})
    assert r.documents
    monkeypatch.delenv("WEAVE_MCECR_PATH")
    with pytest.raises(AdapterFailure):
        McecrProvider(McecrSettings(), ctx).process(UnitRef(UnitKind.run, "r", "h"), {})
