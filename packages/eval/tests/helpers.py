import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from weave_core.schema import (cluster_members, clusters, documents, gold_documents, gold_mentions, gold_pairs,
                               mentions, runs)

NAMES = {"en": "english", "es": "spanish", "hi": "hindi", "tr": "turkish", "uk": "ukrainian"}


def seed_corpus(engine, spec: dict) -> dict:
    """Seed a run, documents, gold and (optionally) system mentions/clusters. Later tasks extend `spec`.

    spec = {
      "state": "done",                                  # run state, default "done"
      "docs": [{
          "id": "d1", "language": "en", "topic": "t1", "split": "test",   # ISO language; split may be None
          "tokens": [[0, 3], [4, 7]],                   # documents.token_offsets, default: one token per 4 chars
          "gold": {"m1": [0, 3], "m2": [4, 7]},         # gold_mentions: mention_id -> [start, end]
          "mentions": {"s1": [0, 3, 0, 1]},             # system mentions: id -> [start, end, tok_start, tok_end]
      }],
      "pairs": [["d1", "m1", "d1", "m2"],               # gold_pairs, label 1; optional 5th item = label,
                ["d1", "m1", "d2", "m3", 0]],           # optional dict as 6th item overrides row fields
      "clusters": {"c1": ["s1", "s2"]},                 # system clusters: cluster id -> mention ids
    }
    returns {"run_id": UUID, "docs": {doc_id: spec doc}}.

    gold_documents/gold_pairs store the dataset's language name ("english"), as the MCECR adapter does;
    documents.language is the ISO code. A pair's kind is "within" when both ends share a document.
    """
    run_id = uuid.uuid4()
    docs = {d["id"]: d for d in spec["docs"]}
    with engine.begin() as c:
        c.execute(sa.insert(runs).values(id=run_id, config={}, config_hash="h", state=spec.get("state", "done"),
                                         created_at=datetime.now(timezone.utc)))
        for i, d in enumerate(spec["docs"]):
            gold = d.get("gold", {})
            tokens = d.get("tokens") or [[s, s + 3] for s in range(0, max([e for _, e in gold.values()] + [0]), 4)]
            text = d.get("text", "x" * max([t[1] for t in tokens] + [1]))
            c.execute(sa.insert(documents).values(
                id=d["id"], provider="mcecr", external_id=d["id"], language=d["language"], title="", byline="",
                outlet="", url="", text=text, token_offsets=tokens, sentence_offsets=[[0, len(text)]],
                content_hash="x", order_key=[i]))
            c.execute(sa.insert(gold_documents).values(
                document_id=d["id"], language=NAMES[d["language"]], topic=d["topic"],
                split=d.get("split", "test"), doc_index=i))
            for mid, (s, e) in gold.items():
                c.execute(sa.insert(gold_mentions).values(
                    document_id=d["id"], mention_id=mid, start=s, end=e, text=text[s:e]))
            for mid, (s, e, ts, te) in d.get("mentions", {}).items():
                c.execute(sa.insert(mentions).values(
                    id=mid, document_id=d["id"], start=s, end=e, tok_start=ts, tok_end=te, sentence=0,
                    text=text[s:e], extractor="e", extractor_version="1"))
        for p in spec.get("pairs", []):
            sd, sm, td, tm, *rest = p
            label = rest[0] if rest else 1
            row = dict(docs_row_defaults(docs, sd), kind="within" if sd == td else "cross",
                       source_document_id=sd, source_mention_id=sm, target_document_id=td, target_mention_id=tm,
                       label=label)
            row.update(rest[1] if len(rest) > 1 else {})
            c.execute(sa.insert(gold_pairs).values(**row))
        for cid, members in spec.get("clusters", {}).items():
            c.execute(sa.insert(clusters).values(id=cid, run_id=run_id, partition="p", size=len(members)))
            for j, m in enumerate(members):
                c.execute(sa.insert(cluster_members).values(
                    run_id=run_id, mention_id=m, cluster_id=cid, partition="p", order_key=[j]))
    return {"run_id": run_id, "docs": docs}


def docs_row_defaults(docs, doc_id):
    d = docs[doc_id]
    return {"language": NAMES[d["language"]], "topic": d["topic"], "split": d.get("split", "test")}
