from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Mapping, Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from .stages import Capability, Stage, UnitKind


@dataclass(frozen=True)
class AdapterSpec:
    name: str
    version: str
    stage: Stage
    input_kind: UnitKind
    settings_model: type[BaseModel]
    capabilities: frozenset[Capability] = frozenset()
    max_concurrency: int = 4


@dataclass(frozen=True)
class UnitRef:
    kind: UnitKind
    id: str
    content_hash: str
    order_key: tuple[int, ...] = ()
    partition_hint: str | None = None


@dataclass(frozen=True)
class DocumentRecord:
    provider: str
    external_id: str
    language: str
    title: str
    byline: str
    outlet: str
    url: str
    published_at: datetime | None
    text: str
    token_offsets: list
    sentence_offsets: list
    content_hash: str
    order_key: tuple[int, ...] = ()

    @property
    def id(self) -> str:
        return f'{self.provider}:{self.external_id}'


@dataclass
class StageResult:
    output: dict
    emits: list[UnitRef] | None = None
    documents: list[DocumentRecord] = field(default_factory=list)
    rows: dict[str, list[dict]] = field(default_factory=dict)


class AdapterFailure(Exception):
    reason: str
    retryable: bool = True

    def __init__(self, reason: str, retryable: bool = True):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass(frozen=True)
class AdapterContext:
    run_id: UUID
    seed: int
    deployments: Mapping[Capability, str]
    stage: Stage | None = None
    adapter: str | None = None


class StageAdapter(Protocol):
    spec: ClassVar[AdapterSpec]

    def __init__(self, settings: BaseModel, ctx: AdapterContext): ...
    def process(self, unit: UnitRef, upstream: Mapping[Stage, dict]) -> StageResult: ...


class OrderedStageAdapter(Protocol):
    spec: ClassVar[AdapterSpec]

    def __init__(self, settings: BaseModel, ctx: AdapterContext): ...
    def process(self, unit: UnitRef, upstream: Mapping[Stage, dict], partition: str, session: Session) -> StageResult:
        """Writes to session commit atomically with the job."""
        ...
