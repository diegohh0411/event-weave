"""Gold chains from the MCECR gold tables. This is the only module that reads them."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field

import sqlalchemy as sa

from weave_core.schema import documents, gold_documents, gold_mentions, gold_pairs

MentionKey = tuple[str, int, int]  # (document_id, start, end), character span

# gold_documents and gold_pairs store the dataset's language names; documents.language is ISO.
ISO = {"english": "en", "spanish": "es", "hindi": "hi", "turkish": "tr", "ukrainian": "uk"}

HINDI_NOTE = "crossDoc pairs unusable in the dataset (no topic or mention ids); chains are within-document only"
NOTES = {"hi": HINDI_NOTE}


def iso(language: str) -> str:
    return ISO.get(language, language)


@dataclass(frozen=True)
class GoldMention:
    document_id: str
    mention_id: str
    language: str
    topic: str
    split: str | None
    start: int
    end: int
    tok_start: int | None  # token span, end exclusive (as mentions.tok_end); None off token boundaries
    tok_end: int | None

    @property
    def key(self) -> MentionKey:
        return (self.document_id, self.start, self.end)


@dataclass
class Gold:
    mentions: dict[MentionKey, GoldMention] = field(default_factory=dict)
    cluster: dict[MentionKey, str] = field(default_factory=dict)
    skipped_pairs: int = 0
    skipped_by_language: dict[str, int] = field(default_factory=dict)  # by the pair's own language
    documents: dict[str, tuple[str, str]] = field(default_factory=dict)  # doc id -> (ISO language, topic)


@dataclass(frozen=True)
class ChainStats:
    mentions: int
    chains: int
    non_singleton: int
    within_links: int
    cross_links: int
    skipped_pairs: int
    note: str | None


def _token_span(offsets, start: int, end: int) -> tuple[int | None, int | None]:
    first = last = None
    for i, (s, e) in enumerate(offsets):
        if s == start and first is None:
            first = i
        if e == end and last is None:
            last = i + 1
    if first is None or last is None or last <= first:
        return None, None
    return first, last


def load_gold(conn, *, languages: set[str] | None = None, split: str | None = None,
              document_ids: set[str] | None = None) -> Gold:
    q = (sa.select(gold_documents.c.document_id, gold_documents.c.language, gold_documents.c.topic,
                   gold_documents.c.split, documents.c.token_offsets)
         .join(documents, documents.c.id == gold_documents.c.document_id))
    if split is not None:
        q = q.where(gold_documents.c.split == split)
    if document_ids is not None:
        q = q.where(gold_documents.c.document_id.in_(document_ids))
    docs = {r.document_id: r for r in conn.execute(q)
            if languages is None or iso(r.language) in languages}
    gold = Gold(documents={i: (iso(d.language), d.topic) for i, d in docs.items()})
    if not docs:
        return gold

    for r in conn.execute(sa.select(gold_mentions).where(gold_mentions.c.document_id.in_(list(docs)))):
        d = docs[r.document_id]
        ts, te = _token_span(d.token_offsets, r.start, r.end)
        m = GoldMention(r.document_id, r.mention_id, iso(d.language), d.topic, d.split, r.start, r.end, ts, te)
        gold.mentions[m.key] = m
    by_id = {(m.document_id, m.mention_id): m for m in gold.mentions.values()}

    parent: dict[MentionKey, MentionKey] = {k: k for k in gold.mentions}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ids = list(docs)
    pq = sa.select(gold_pairs).where(gold_pairs.c.label == 1).where(
        sa.or_(gold_pairs.c.source_document_id.in_(ids), gold_pairs.c.target_document_id.in_(ids)))
    for p in conn.execute(pq):
        a = by_id.get((p.source_document_id, p.source_mention_id))
        b = by_id.get((p.target_document_id, p.target_mention_id))
        if a is None or b is None or not (a.language == b.language == iso(p.language)
                                          and a.topic == b.topic == p.topic):
            gold.skipped_pairs += 1
            gold.skipped_by_language[iso(p.language)] = gold.skipped_by_language.get(iso(p.language), 0) + 1
            continue
        parent[find(a.key)] = find(b.key)

    groups: dict[MentionKey, list[MentionKey]] = defaultdict(list)
    for k in gold.mentions:
        groups[find(k)].append(k)
    for members in groups.values():
        cid = hashlib.sha256(json.dumps(sorted(members)).encode()).hexdigest()[:16]
        for k in members:
            gold.cluster[k] = cid
    return gold


def gold_hash(gold: Gold) -> str:
    rows = sorted([m.language, m.topic, *m.key, gold.cluster[m.key]] for m in gold.mentions.values())
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def chain_stats(gold: Gold) -> dict[str, ChainStats]:
    """Per language. Links are coreferent mention pairs of the closure (within = same document)."""
    langs = sorted({m.language for m in gold.mentions.values()})
    out = {}
    for lang in langs:
        members: dict[str, list[MentionKey]] = defaultdict(list)
        for m in gold.mentions.values():
            if m.language == lang:
                members[gold.cluster[m.key]].append(m.key)
        within = cross = 0
        for ks in members.values():
            n = len(ks)
            per_doc = defaultdict(int)
            for k in ks:
                per_doc[k[0]] += 1
            w = sum(c * (c - 1) // 2 for c in per_doc.values())
            within += w
            cross += n * (n - 1) // 2 - w
        out[lang] = ChainStats(
            mentions=sum(len(v) for v in members.values()), chains=len(members),
            non_singleton=sum(1 for v in members.values() if len(v) > 1),
            within_links=within, cross_links=cross,
            skipped_pairs=gold.skipped_by_language.get(lang, 0), note=NOTES.get(lang))
    return out
