from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from weave_api.deps import get_engine
from weave_api.paging import Page, Paging, paginate
from weave_api.queries import documents as q
from weave_api.queries import runs as run_q

router = APIRouter()


class DocumentSummary(BaseModel):
    id: str
    title: str
    outlet: str
    language: str
    published_at: datetime | None
    mentions: int


class DocumentMention(BaseModel):
    id: str
    start: int
    end: int
    cluster_id: str | None
    cluster_size: int | None
    enrichment: dict[str, Any] | None


class DocumentOut(BaseModel):
    id: str
    title: str
    byline: str
    outlet: str
    url: str
    language: str
    published_at: datetime | None
    text: str
    mentions: list[DocumentMention]


Engine = Annotated[Any, Depends(get_engine)]


@router.get("/runs/{run_id}/documents")
def list_documents(run_id: UUID, paging: Annotated[Paging, Depends()], engine: Engine,
                   q_: Annotated[str | None, Query(alias="q")] = None, language: str | None = None) -> Page[DocumentSummary]:
    with engine.connect() as conn:
        run_q.run_row(conn, run_id)
        stage = q.mention_stage(conn, run_id)
        return paginate(conn, q.listing(run_id, stage, q_, language), paging,
                        lambda r: DocumentSummary(id=r.id, title=r.title, outlet=r.outlet, language=r.language,
                                                  published_at=r.published_at, mentions=r.mentions))


@router.get("/runs/{run_id}/documents/{document_id}")
def get_document(run_id: UUID, document_id: str, engine: Engine) -> DocumentOut:
    with engine.connect() as conn:
        run_q.run_row(conn, run_id)
        d = q.document_row(conn, run_id, document_id)
        rows = q.document_mentions(conn, run_id, q.mention_stage(conn, run_id), document_id)
        return DocumentOut(id=d.id, title=d.title, byline=d.byline, outlet=d.outlet, url=d.url, language=d.language,
                           published_at=d.published_at, text=d.text,
                           mentions=[DocumentMention(id=r.id, start=r.start, end=r.end, cluster_id=r.cluster_id,
                                                     cluster_size=r.cluster_size, enrichment=r.enrichment) for r in rows])
