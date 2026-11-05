import json
from hashlib import sha256
from typing import Mapping

import sqlalchemy as sa

from .adapters import AdapterSpec, StageResult, UnitRef
from .schema import stage_outputs
from .stages import Capability, UnitKind


def cache_key(input_hash: str, spec: AdapterSpec, settings: dict, deployments: Mapping[Capability, str]) -> str:
    payload = {"input": input_hash, "adapter": spec.name, "version": spec.version,
               "settings": settings, "deployments": dict(deployments)}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def lookup(session, key: str) -> StageResult | None:
    row = session.execute(sa.select(stage_outputs.c.output, stage_outputs.c.emits)
                          .where(stage_outputs.c.cache_key == key)).first()
    if row is None:
        return None
    emits = None if row.emits is None else [
        UnitRef(UnitKind(unit["kind"]), unit["id"], unit["content_hash"],
                tuple(unit["order_key"]), unit["partition_hint"])
        for unit in row.emits
    ]
    return StageResult(output=row.output, emits=emits)
