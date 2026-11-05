from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from weave_core.schema import ledger

from .types import Deployment, Usage


@dataclass(frozen=True)
class LedgerEntry:
    run_id: UUID | None
    stage: str | None
    adapter: str | None
    deployment: str
    request_hash: str
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cost: Decimal
    estimated: bool
    latency_ms: int
    attempts: int
    status: str
    error: str | None = None


def request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def cost_of(deployment: Deployment, usage: Usage, reported_cost: Decimal | None) -> tuple[Decimal, bool]:
    """Never NULL: missing usage counts as zero tokens, still flagged estimated."""
    if reported_cost is not None:
        return reported_cost, False
    if deployment.local:
        return Decimal(0), False
    price = deployment.price
    total = (usage.input_tokens or 0) * price.input_per_mtok + (usage.output_tokens or 0) * price.output_per_mtok
    return total / Decimal(1_000_000), True


def record(engine: sa.Engine, entry: LedgerEntry) -> None:
    with engine.begin() as connection:
        connection.execute(ledger.insert().values(**asdict(entry), created_at=sa.func.now()))
