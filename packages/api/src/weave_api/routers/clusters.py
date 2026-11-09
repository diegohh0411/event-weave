from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from weave_api.deps import get_engine
from weave_api.paging import Page, Paging, paginate
from weave_api.queries import clusters as q
from weave_api.queries import runs as run_q

router = APIRouter()


class ClusterSummary(BaseModel):
    id: str
    partition: str | None
    size: int
    card: dict[str, Any] | None
    languages: list[str]
    event_types: list[str]
    max_significance: float | None


class ClusterOut(BaseModel):
    cluster: ClusterSummary
    card_hash: str | None


class Candidate(BaseModel):
    cluster_id: str
    label: str | None = None
    score: float | None = None
    field_scores: dict[str, float] | None = None


class Decision(BaseModel):
    adapter: str | None
    deployment: str | None
    outcome: str | None
    reason: str | None
    noul: float | None
    probabilities: dict[str, float] | None
    candidates: list[Candidate]


class Member(BaseModel):
    mention_id: str
    document_id: str
    document_title: str
    language: str
    text: str
    sentence: str
    trigger: tuple[int, int]
    significance: float | None
    event_type: str | None
    decision: Decision | None


Engine = Annotated[Any, Depends(get_engine)]


def _summary(r) -> ClusterSummary:
    return ClusterSummary(id=r.id, partition=r.partition, size=r.size, card=r.card, languages=sorted(r.languages),
                          event_types=sorted(r.event_types), max_significance=r.max_significance)


def _member(r) -> Member:
    decision = None
    if r.decided is not None:
        decision = Decision(adapter=r.adapter, deployment=r.deployment, outcome=r.outcome, reason=r.reason, noul=r.noul,
                            probabilities=r.probabilities, candidates=[Candidate(**c) for c in (r.candidates or [])])
    return Member(mention_id=r.mention_id, document_id=r.document_id, document_title=r.document_title, language=r.language,
                  text=r.text, sentence=r.sentence, trigger=(r.trigger_start, r.trigger_end), significance=r.significance,
                  event_type=r.event_type, decision=decision)


@router.get("/runs/{run_id}/clusters")
def search_clusters(run_id: UUID, paging: Annotated[Paging, Depends()], engine: Engine, q_: Annotated[str | None, Query(alias="q")] = None,
                    language: str | None = None, min_size: Annotated[int | None, Query(ge=0)] = None,
                    max_size: Annotated[int | None, Query(ge=0)] = None, event_type: str | None = None,
                    min_significance: float | None = None, sort: Literal["size", "recent"] = "size") -> Page[ClusterSummary]:
    with engine.connect() as conn:
        run_q.run_row(conn, run_id)
        return paginate(conn, q.search(run_id, q_, language, min_size, max_size, event_type, min_significance, sort), paging, _summary)


@router.get("/runs/{run_id}/clusters/{cluster_id}")
def get_cluster(run_id: UUID, cluster_id: str, engine: Engine) -> ClusterOut:
    with engine.connect() as conn:
        run_q.run_row(conn, run_id)
        row = q.cluster_row(conn, run_id, cluster_id)
        return ClusterOut(cluster=_summary(row), card_hash=row.card_hash)


@router.get("/runs/{run_id}/clusters/{cluster_id}/members")
def cluster_members(run_id: UUID, cluster_id: str, paging: Annotated[Paging, Depends()], engine: Engine) -> Page[Member]:
    with engine.connect() as conn:
        run_q.run_row(conn, run_id)
        q.cluster_row(conn, run_id, cluster_id)
        return paginate(conn, q.members(run_id, cluster_id), paging, _member)
