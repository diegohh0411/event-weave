from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from weave_api.deps import get_engine
from weave_api.paging import Page, Paging, paginate
from weave_api.queries import library as q

router = APIRouter()
Engine = Annotated[Any, Depends(get_engine)]
Filter = Annotated[q.LibraryFilter, Depends()]


class LibraryDocument(BaseModel):
    id: str
    article_id: str
    version: int
    title: str
    outlet: str
    language: str
    url: str
    published_at: datetime | None
    versions: int
    removed_at: datetime | None = None
    removed_reason: str | None = None
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None


class DocumentHistory(BaseModel):
    id: str
    version: int
    superseded_at: datetime | None


class RecheckOut(BaseModel):
    checked_at: datetime
    stage: int
    outcome: Literal['unchanged', 'edited', 'gone', 'redirected', 'error']
    http_status: int | None
    final_url: str | None
    reason: str | None
    document_id: str | None


class LibraryDocumentDetail(LibraryDocument):
    byline: str
    text: str
    history: list[DocumentHistory]
    rechecks: list[RecheckOut]


class Facet(BaseModel):
    value: str
    count: int


class LibraryFacets(BaseModel):
    outlets: list[Facet]
    languages: list[Facet]
    changes: list[Facet]
    total: int


def summary(row, versions: int) -> LibraryDocument:
    return LibraryDocument(id=row.id, article_id=row.article_id, version=row.version, title=row.title,
                           outlet=row.outlet, language=row.language, url=row.url,
                           published_at=row.published_at, versions=versions, removed_at=row.removed_at,
                           removed_reason=row.removed_reason, last_checked_at=row.last_checked_at,
                           next_check_at=row.next_check_at)


@router.get('/library/documents')
def list_documents(f: Filter, paging: Annotated[Paging, Depends()], engine: Engine) -> Page[LibraryDocument]:
    with engine.connect() as conn:
        return paginate(conn, q.listing(f), paging, lambda row: summary(row, row.versions))


@router.get('/library/documents/{document_id}')
def get_document(document_id: str, engine: Engine) -> LibraryDocumentDetail:
    with engine.connect() as conn:
        row = q.document_row(conn, document_id)
        history = q.history(conn, row.article_id)
        return LibraryDocumentDetail(**summary(row, len(history)).model_dump(), byline=row.byline, text=row.text,
                                     history=[DocumentHistory.model_validate(r._mapping) for r in history],
                                     rechecks=[RecheckOut.model_validate(r._mapping) for r in q.rechecks(conn, row.article_id)])


@router.get('/library/facets')
def get_facets(f: Filter, engine: Engine) -> LibraryFacets:
    with engine.connect() as conn:
        total, outlets, languages, changes = q.facets(conn, f)
        return LibraryFacets(total=total, outlets=[Facet.model_validate(r._mapping) for r in outlets],
                             languages=[Facet.model_validate(r._mapping) for r in languages],
                             changes=[Facet(value=value, count=n) for value, n in changes])
