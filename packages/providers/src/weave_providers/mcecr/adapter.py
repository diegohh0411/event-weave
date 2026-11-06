"""The MCECR provide-stage adapter: dataset files in, documents, emits and gold rows out."""

import os
import random
from pathlib import Path
from typing import Literal, Mapping, TypeVar

from pydantic import BaseModel, Field

from weave_core.adapters import (
    AdapterContext, AdapterFailure, AdapterSpec, DocumentRecord, StageResult, UnitRef,
)
from weave_core.stages import Stage, UnitKind

from ..shape import content_hash, opaque_id
from .reader import ISO_CODES, LANGUAGES, file_sha256, read_splits, read_topic

T = TypeVar("T")
PROVIDER = "mcecr"


class McecrSettings(BaseModel):
    path: str = "${WEAVE_MCECR_PATH}"
    languages: list[str] = Field(default_factory=lambda: ["english"])
    splits: list[Literal["train", "dev", "test"]] = Field(default_factory=lambda: ["test"])
    topic_limit: int | None = None


def interleave(
    topics: dict[str, list[T]], seed: int, topic_limit: int | None = None
) -> list[T]:
    """Seeded round-robin across topics, file order kept within each topic."""
    keys = sorted(topics)
    random.Random(seed).shuffle(keys)
    if topic_limit is not None:
        keys = keys[:topic_limit]
    queues = [topics[k] for k in keys]
    out: list[T] = []
    for rank in range(max((len(q) for q in queues), default=0)):
        out.extend(q[rank] for q in queues if rank < len(q))
    return out


class McecrProvider:
    spec = AdapterSpec(
        name="mcecr", version="1", stage=Stage.provide, input_kind=UnitKind.run,
        settings_model=McecrSettings, cacheable=False,  # order depends on run seed and dataset files
    )

    def __init__(self, settings: McecrSettings, ctx: AdapterContext):
        self.settings = settings
        self.ctx = ctx

    def _root(self) -> Path:
        raw = os.path.expandvars(self.settings.path)
        if not raw or "${WEAVE_MCECR_PATH}" in raw:
            raise AdapterFailure(
                f"MCECR path {self.settings.path!r} is not set; export WEAVE_MCECR_PATH",
                retryable=False,
            )
        root = Path(raw).expanduser()
        if not root.is_dir():
            raise AdapterFailure(f"MCECR path {root} is not a directory", retryable=False)
        return root

    def process(self, unit: UnitRef, upstream: Mapping[Stage, dict]) -> StageResult:
        s = self.settings
        unknown = [lang for lang in s.languages if lang not in LANGUAGES]
        if unknown:
            raise AdapterFailure(f"unknown MCECR languages: {unknown}", retryable=False)
        root = self._root()

        files: dict[str, str] = {}
        without_split: dict[str, list[str]] = {}
        skipped_pairs: dict[str, int] = {}
        docs_by_topic: dict[str, list] = {}   # "lang/topic" -> [McecrDoc]
        splits_of: dict[str, str] = {}        # "lang/topic" -> split
        pairs_of: dict[str, list] = {}        # language -> [GoldPair]

        for lang in dict.fromkeys(s.languages):
            base = root / lang
            if not (base / "events").is_dir():
                raise AdapterFailure(f"MCECR language directory {base}/events missing", retryable=False)
            topic_splits, pairs, skipped = read_splits(root, lang)
            skipped_pairs[lang] = skipped
            for dirname in ("withinDoc", "crossDoc"):
                for split in ("train", "dev", "test"):
                    p = base / dirname / f"{split}.json"
                    if p.exists():
                        files[p.relative_to(root).as_posix()] = file_sha256(p)
            unsplit = []
            for path in sorted((base / "events").glob("*.json")):
                topic = path.stem
                split = topic_splits.get(topic)
                if split is None:
                    unsplit.append(topic)
                elif split in s.splits:
                    key = f"{lang}/{topic}"
                    docs_by_topic[key] = read_topic(path, lang)
                    splits_of[key] = split
                    files[path.relative_to(root).as_posix()] = file_sha256(path)
            if unsplit:
                without_split[lang] = unsplit
            pairs_of[lang] = pairs

        ordered = interleave(docs_by_topic, self.ctx.seed, s.topic_limit)
        kept = {f"{d.language}/{d.topic}" for d in ordered}

        documents: list[DocumentRecord] = []
        emits: list[UnitRef] = []
        gold_documents: list[dict] = []
        gold_mentions: list[dict] = []
        ids: dict[tuple[str, str, int], str] = {}
        for i, d in enumerate(ordered):
            rec = DocumentRecord(
                provider=PROVIDER,
                external_id=opaque_id(d.language, d.topic, str(d.doc_index)),
                language=ISO_CODES[d.language], title="", byline="", outlet="", url="",
                published_at=None, text=d.text,
                token_offsets=[list(t) for t in d.token_offsets],
                sentence_offsets=[list(t) for t in d.sentence_offsets],
                content_hash=content_hash(d.text), order_key=(i,),
            )
            ids[d.language, d.topic, d.doc_index] = rec.id
            documents.append(rec)
            emits.append(UnitRef(
                UnitKind.document, rec.id, rec.content_hash, (i,), f"{rec.language}/{d.topic}"))
            gold_documents.append({
                "document_id": rec.id, "language": d.language, "topic": d.topic,
                "split": splits_of[f"{d.language}/{d.topic}"], "doc_index": d.doc_index,
            })
            gold_mentions.extend(
                {"document_id": rec.id, "mention_id": m.mention_id, "start": m.start,
                 "end": m.end, "text": m.text}
                for m in d.mentions
            )

        gold_pairs: list[dict] = []
        seen: set[tuple] = set()
        for lang, pairs in pairs_of.items():
            for p in pairs:
                if f"{lang}/{p.topic}" not in kept:
                    continue
                src = ids.get((lang, p.topic, p.source_doc))
                tgt = ids.get((lang, p.topic, p.target_doc))
                key = (p.kind, src, p.source_id, tgt, p.target_id)
                if src is None or tgt is None or key in seen:
                    skipped_pairs[lang] += 1
                    continue
                seen.add(key)
                gold_pairs.append({
                    "language": lang, "kind": p.kind, "split": p.split, "topic": p.topic,
                    "source_document_id": src, "source_mention_id": p.source_id,
                    "target_document_id": tgt, "target_mention_id": p.target_id,
                    "label": p.label,
                })

        return StageResult(
            output={
                "files": files, "topics_without_split": without_split,
                "skipped_pairs": skipped_pairs, "documents": len(documents),
            },
            emits=emits, documents=documents,
            rows={"gold_documents": gold_documents, "gold_mentions": gold_mentions,
                  "gold_pairs": gold_pairs},
        )
