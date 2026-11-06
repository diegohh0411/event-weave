"""Run-level enrichment coverage per enricher, language and field, and a deterministic sample for spot-checks."""
import hashlib
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from weave_core.schema import documents, enrichments, jobs, mentions, runs, stage_outputs
from weave_core.stages import JobStatus, Stage

from ._shared import mark

FIELDS = ("action", "participants", "location", "time")


@dataclass(frozen=True)
class CoverageRow:
    enricher: str
    language: str
    field: str  # a field name, "misaligned" / "unparsed", or "(failed)"
    mentions: int
    filled: int
    share: float  # filled / mentions, 0 when there are no mentions
    discarded: int


def coverage(engine, run_id: UUID) -> list[CoverageRow]:
    j, o, m, d = jobs.c, stage_outputs.c, mentions.c, documents.c
    done = (sa.select(o.adapter, d.language, o.output).select_from(jobs)
            .join(stage_outputs, o.cache_key == j.cache_key)
            .join(mentions, m.id == j.unit_id).join(documents, d.id == m.document_id)
            .where(j.run_id == run_id, j.stage == Stage.enrich, j.status == JobStatus.done))
    failed = (sa.select(d.language, sa.func.count().label("n")).select_from(jobs)
              .join(mentions, m.id == j.unit_id).join(documents, d.id == m.document_id)
              .where(j.run_id == run_id, j.stage == Stage.enrich, j.status == JobStatus.failed).group_by(d.language))
    with engine.connect() as conn:
        outputs = conn.execute(done).all()
        failures = conn.execute(failed).all()
        config = conn.scalar(sa.select(runs.c.config).where(runs.c.id == run_id)) or {}
    counts: dict[tuple[str, str], dict] = {}
    for adapter, language, output in outputs:
        entry = counts.setdefault((adapter, language), {"mentions": 0, "filled": dict.fromkeys(FIELDS, 0), "discarded": {}})
        entry["mentions"] += 1
        enrichment, dropped = output.get("enrichment", {}), output.get("discarded", {})
        for name in FIELDS:
            if (enrichment.get(name) or {}).get("text") is not None:
                entry["filled"][name] += 1
        for key, n in dropped.items():
            entry["discarded"][key] = entry["discarded"].get(key, 0) + int(n or 0)
    rows = []
    for (adapter, language), entry in sorted(counts.items()):
        n = entry["mentions"]
        for name in FIELDS:
            filled = entry["filled"][name]
            rows.append(CoverageRow(adapter, language, name, n, filled, filled / n if n else 0.0,
                                    entry["discarded"].get(name, 0)))
        for key in ("misaligned", "unparsed"):
            if key in entry["discarded"]:
                rows.append(CoverageRow(adapter, language, key, n, 0, 0.0, entry["discarded"][key]))
    enricher = ((config.get("stages") or {}).get(Stage.enrich.value) or {}).get("adapter", "(unknown)")
    rows.extend(CoverageRow(enricher, language, "(failed)", n, 0, 0.0, 0) for language, n in sorted(failures))
    return rows


def sample(engine, run_id: UUID, n: int, seed: int) -> list[dict]:
    """n enrichments of the run ordered by sha256(seed:id), each with its mention's sentence, trigger marked."""
    j, o, e, m, d = jobs.c, stage_outputs.c, enrichments.c, mentions.c, documents.c
    query = (sa.select(e.id, e.mention_id, e.enricher, e.enricher_version, e.deployment, e.action, e.participants,
                       e.location, e.time, e.discarded, m.document_id, m.start, m.end, m.sentence, d.text,
                       d.sentence_offsets)
             .select_from(jobs).join(stage_outputs, o.cache_key == j.cache_key)
             .join(enrichments, sa.and_(e.mention_id == j.unit_id, e.enricher == o.adapter,
                                        e.enricher_version == o.adapter_version))
             .join(mentions, m.id == e.mention_id).join(documents, d.id == m.document_id)
             .where(j.run_id == run_id, j.stage == Stage.enrich, j.status == JobStatus.done))
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    rows.sort(key=lambda r: hashlib.sha256(f"{seed}:{r.id}".encode()).hexdigest())
    out = []
    for r in rows[:n]:
        s_start, s_end = r.sentence_offsets[r.sentence] if r.sentence < len(r.sentence_offsets) else (0, len(r.text))
        out.append({"id": r.id, "mention_id": r.mention_id, "document_id": r.document_id, "enricher": r.enricher,
                    "enricher_version": r.enricher_version, "deployment": r.deployment,
                    "sentence": mark(r.text[s_start:s_end], r.start - s_start, r.end - s_start),
                    "enrichment": {name: getattr(r, name) for name in FIELDS}, "discarded": r.discarded})
    return out
