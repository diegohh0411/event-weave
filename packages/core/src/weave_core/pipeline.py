from dataclasses import replace
from typing import TYPE_CHECKING

from .adapters import AdapterFailure, StageResult, UnitRef
from .config import RunConfig
from .stages import PIPELINE, Stage, Track, UnitKind

if TYPE_CHECKING:
    from .queue import ClaimedJob


def next_stage(cfg: RunConfig, stage: Stage) -> Stage | None:
    return next((s for s in PIPELINE[PIPELINE.index(stage) + 1:] if s in cfg.stages), None)


def child_units(parent: "ClaimedJob", result: StageResult) -> list[UnitRef]:
    if result.emits is None:
        return [parent.unit]
    if parent.stage == Stage.provide:
        return result.emits
    return [replace(unit, order_key=parent.order_key + (i,)) for i, unit in enumerate(result.emits)]


def child_partition(track: Track, parent_partition: str, unit: UnitRef) -> str:
    if track == Track.realistic:
        return "all"
    if parent_partition != "*":
        return parent_partition
    if unit.kind == UnitKind.document and not unit.partition_hint:
        raise AdapterFailure(f"paper track needs a topic for document '{unit.id}'", retryable=False)
    return unit.partition_hint or parent_partition
