"""Run-level extraction counts: nothing dropped silently (spec 04, overview rule 7)."""
from uuid import UUID

import sqlalchemy as sa
from weave_core.schema import jobs, stage_outputs
from weave_core.stages import JobStatus, Stage

COUNTERS = ("misaligned", "unlocated", "unlocated_windows")


def extract_report(engine, run_id: UUID) -> dict:
    j, o = jobs.c, stage_outputs.c
    with engine.connect() as conn:
        outputs = conn.execute(sa.select(o.output).join(stage_outputs, o.cache_key == j.cache_key).where(
            j.run_id == run_id, j.stage == Stage.extract, j.status == JobStatus.done)).scalars().all()
        failed = conn.scalar(sa.select(sa.func.count()).select_from(jobs).where(
            j.run_id == run_id, j.stage == Stage.extract, j.status == JobStatus.failed))
    report = {"documents": len(outputs), "mentions": sum(len(out.get("mentions", [])) for out in outputs)}
    for key in COUNTERS:
        report[key] = sum(int(out.get(key, 0) or 0) for out in outputs)
    report["failed_jobs"] = failed
    return report
