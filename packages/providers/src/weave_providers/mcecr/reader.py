"""Pure parsing of the MCECR dataset: topic token files and pair files."""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

LANGUAGES = ("english", "spanish", "hindi", "turkish", "ukrainian")
ISO_CODES = {
    "english": "en",
    "spanish": "es",
    "hindi": "hi",
    "turkish": "tr",
    "ukrainian": "uk",
}
# Order of precedence when a topic appears in more than one split.
_SPLIT_PRECEDENCE = ("test", "dev", "train")


@dataclass
class GoldMention:
    mention_id: str
    start: int
    end: int
    text: str


@dataclass
class McecrDoc:
    language: str
    topic: str
    doc_index: int
    text: str
    token_offsets: list[tuple[int, int]]
    sentence_offsets: list[tuple[int, int]]
    mentions: list[GoldMention] = field(default_factory=list)


@dataclass
class GoldPair:
    language: str
    kind: Literal["within", "cross"]
    split: str
    topic: str
    source_doc: int
    source_id: str
    target_doc: int
    target_id: str
    label: int


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_doc(language: str, topic: str, doc_index: int, tokens: list[dict]) -> McecrDoc:
    parts: list[str] = []
    cursor = 0
    prev_end: int | None = None  # source coordinates
    offsets: list[tuple[int, int]] = []
    for tok in tokens:
        s, e = tok["span"]
        if prev_end is None:
            gap = 0
        elif s < prev_end:
            gap = 1  # overlap or backwards: one space, continue from cursor
        else:
            gap = s - prev_end
        parts.append(" " * gap + tok["text"])
        start = cursor + gap
        end = start + len(tok["text"])
        offsets.append((start, end))
        cursor = end
        prev_end = e
    text = "".join(parts)

    sentences: list[tuple[int, int]] = []
    sent_start = 0
    for i, tok in enumerate(tokens):
        last = i == len(tokens) - 1 or tokens[i + 1]["sent_ind"] != tok["sent_ind"]
        if last:
            sentences.append((offsets[sent_start][0], offsets[i][1]))
            sent_start = i + 1

    spans: dict[str, list[int]] = {}
    for i, tok in enumerate(tokens):
        mid = tok.get("id")
        if mid is None or tok.get("label", "O") == "O":
            continue
        spans.setdefault(mid, []).append(i)
    mentions = [
        GoldMention(mid, offsets[idx[0]][0], offsets[idx[-1]][1], "")
        for mid, idx in spans.items()
    ]
    for m in mentions:
        m.text = text[m.start : m.end]
    return McecrDoc(language, topic, doc_index, text, offsets, sentences, mentions)


def read_topic(path: Path, language: str) -> list[McecrDoc]:
    tokens = json.loads(Path(path).read_text(encoding="utf-8"))
    by_doc: dict[int, list[dict]] = {}
    for tok in tokens:
        by_doc.setdefault(tok["doc_ind"], []).append(tok)
    topic = Path(path).stem
    return [_build_doc(language, topic, d, by_doc[d]) for d in sorted(by_doc)]


def read_pairs(path: Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped[0] == "[":
        return json.loads(stripped)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def read_splits(root: Path, language: str) -> tuple[dict[str, str], list[GoldPair], int]:
    """Return (topic -> split, gold pairs, count of pairs skipped as unusable).

    Hindi crossDoc files carry no topic or mention ids, so every pair there is
    skipped; Hindi topic splits come from withinDoc.
    """
    base = Path(root) / language
    splits: dict[str, str] = {}
    pairs: list[GoldPair] = []
    skipped = 0
    for split in _SPLIT_PRECEDENCE:
        for kind, dirname in (("within", "withinDoc"), ("cross", "crossDoc")):
            path = base / dirname / f"{split}.json"
            if not path.exists():
                continue
            for rec in read_pairs(path):
                topic = rec.get("topic_id")
                sid, tid = rec.get("source_id"), rec.get("target_id")
                docs = rec.get("doc_ids")
                if topic is None or sid is None or tid is None or not docs:
                    skipped += 1
                    continue
                topic = str(topic)
                if topic in splits and splits[topic] != split:
                    log.warning(
                        "topic %s/%s in splits %s and %s; keeping %s",
                        language, topic, splits[topic], split, splits[topic],
                    )
                else:
                    splits[topic] = split
                src_doc, tgt_doc = docs[0], docs[-1]
                pairs.append(
                    GoldPair(language, kind, split, topic, src_doc, sid, tgt_doc, tid, rec["label"])
                )
    return splits, pairs, skipped
