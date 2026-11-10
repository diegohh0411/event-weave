from datetime import datetime
from typing import Annotated, Any

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


class DocumentHistory(BaseModel):
    id: str
    version: int
    superseded_at: datetime | None


class LibraryDocumentDetail(LibraryDocument):
    byline: str
    text: str
    history: list[DocumentHistory]


class Facet(BaseModel):
    value: str
    count: int


class LibraryFacets(BaseModel):
    outlets: list[Facet]
    languages: list[Facet]
    total: int


def summary(row, versions: int) -> LibraryDocument:
    return LibraryDocument(id=row.id, article_id=row.article_id, version=row.version, title=row.title,
                           outlet=row.outlet, language=row.language, url=row.url,
                           published_at=row.published_at, versions=versions)


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
                                     history=[DocumentHistory.model_validate(r._mapping) for r in history])


@router.get('/library/facets')
def get_facets(f: Filter, engine: Engine) -> LibraryFacets:
    with engine.connect() as conn:
        total, outlets, languages = q.facets(conn, f)
        return LibraryFacets(total=total, outlets=[Facet.model_validate(r._mapping) for r in outlets],
                             languages=[Facet.model_validate(r._mapping) for r in languages])
