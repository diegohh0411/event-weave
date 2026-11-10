from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from weave_api.deps import get_engine
from weave_api.paging import Page, Paging, paginate
from weave_core.schema import crawl_articles, crawl_runs, crawl_sources, documents
from weave_providers.crawl.jobs import crawl_counters, crawl_counters_many, create_crawl
from weave_providers.crawl.requests import CrawlRequest

router = APIRouter()
Engine = Annotated[Any, Depends(get_engine)]


class CrawlCounters(BaseModel):
    discovered: int
    fetched: int
    stored: int
    existing: int
    duplicates: int
    rejected: int
    errors: int
    updated: int
    unchanged: int
    removed: int
    reasons: dict[str, int]


class CrawlOut(BaseModel):
    id: UUID
    request: CrawlRequest
    site_id: UUID | None
    trigger: Literal['adhoc', 'manual', 'schedule']
    status: Literal['queued', 'running', 'completed', 'failed']
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    counters: CrawlCounters
    error: str | None


class CrawlArticleOut(BaseModel):
    canonical_url: str
    outlet: str
    url: str
    status: Literal['stored', 'existing', 'duplicate', 'rejected', 'error', 'updated', 'unchanged', 'removed']
    reason: str | None
    document_id: str | None
    fetched: bool
    updated_at: datetime


class CrawlSourceOut(BaseModel):
    url: str
    site: str
    kind: Literal['feed', 'sitemap']
    status: str
    error: str | None


class CrawlDocumentOut(BaseModel):
    id: str
    title: str
    byline: str
    outlet: str
    url: str
    language: str
    published_at: datetime | None
    text: str


def _crawl_row(conn, crawl_id: UUID):
    return conn.execute(sa.select(crawl_runs).where(crawl_runs.c.id == crawl_id)).one()


def _crawl_out(conn, row, counters=None) -> CrawlOut:
    return CrawlOut(id=row.id, request=row.request, site_id=row.site_id, trigger=row.trigger, status=row.status, created_at=row.created_at,
                    started_at=row.started_at, finished_at=row.finished_at,
                    counters=counters if counters is not None else crawl_counters(conn, row.id),
                    error=row.error)


@router.post('/crawls', status_code=202)
def create(request: CrawlRequest, engine: Engine) -> CrawlOut:
    created_at = datetime.now(timezone.utc)
    crawl_id = create_crawl(engine, request, created_at=created_at)
    return CrawlOut(id=crawl_id, request=request, site_id=None, trigger='adhoc', status='queued', created_at=created_at,
                    started_at=None, finished_at=None, error=None,
                    counters=CrawlCounters(discovered=0, fetched=0, stored=0, existing=0,
                                           duplicates=0, rejected=0, errors=0, updated=0, unchanged=0, removed=0, reasons={}))


@router.get('/crawls')
def list_crawls(paging: Annotated[Paging, Depends()], engine: Engine) -> Page[CrawlOut]:
    with engine.connect() as conn:
        query = sa.select(crawl_runs).order_by(crawl_runs.c.created_at.desc(), crawl_runs.c.id.desc())
        page = paginate(conn, query, paging, lambda row: row)
        counters = crawl_counters_many(conn, [row.id for row in page.items])
        return Page(items=[_crawl_out(conn, row, counters[row.id]) for row in page.items],
                    total=page.total, offset=page.offset, limit=page.limit)


@router.get('/crawls/{crawl_id}')
def get_crawl(crawl_id: UUID, engine: Engine) -> CrawlOut:
    with engine.connect() as conn:
        return _crawl_out(conn, _crawl_row(conn, crawl_id))


@router.get('/crawls/{crawl_id}/articles')
def list_articles(crawl_id: UUID, paging: Annotated[Paging, Depends()], engine: Engine) -> Page[CrawlArticleOut]:
    with engine.connect() as conn:
        _crawl_row(conn, crawl_id)
        query = sa.select(crawl_articles).where(crawl_articles.c.crawl_id == crawl_id).order_by(crawl_articles.c.canonical_url)
        return paginate(conn, query, paging, lambda row: CrawlArticleOut.model_validate(row._mapping))


@router.get('/crawls/{crawl_id}/sources')
def list_sources(crawl_id: UUID, paging: Annotated[Paging, Depends()], engine: Engine) -> Page[CrawlSourceOut]:
    with engine.connect() as conn:
        _crawl_row(conn, crawl_id)
        query = sa.select(crawl_sources).where(crawl_sources.c.crawl_id == crawl_id).order_by(crawl_sources.c.url)
        return paginate(conn, query, paging, lambda row: CrawlSourceOut.model_validate(row._mapping))


@router.get('/crawls/{crawl_id}/documents/{document_id}')
def get_document(crawl_id: UUID, document_id: str, engine: Engine) -> CrawlDocumentOut:
    with engine.connect() as conn:
        _crawl_row(conn, crawl_id)
        membership = sa.exists(sa.select(1).where(crawl_articles.c.crawl_id == crawl_id,
                                                   crawl_articles.c.document_id == documents.c.id))
        row = conn.execute(sa.select(documents).where(documents.c.id == document_id, membership)).one()
        return CrawlDocumentOut.model_validate(row._mapping)
