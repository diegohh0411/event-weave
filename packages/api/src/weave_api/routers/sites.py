from datetime import datetime, timezone, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from weave_api.paging import Page, Paging, paginate
from weave_api.routers.crawls import Engine, CrawlOut, _crawl_out
from weave_core.schema import sites, crawl_runs
from weave_providers.crawl.jobs import crawl_counters_many
from weave_providers.crawl.requests import CrawlSite, _domain, _source_url
from weave_providers.crawl.scheduler import queue_check, AlreadyQueued, SiteNotFound

router = APIRouter()


class SiteCreate(CrawlSite):
    enabled: bool = True
    check_every_minutes: int = Field(default=60, ge=5)
    daily_cap: int = Field(default=50, ge=1)
    min_interval_seconds: float = Field(default=2, ge=0.5, allow_inf_nan=False)
    max_parallel: int = Field(default=1, ge=1, le=4)


class SitePatch(BaseModel):
    name: str | None = None
    domain: str | None = None
    language: Literal['en', 'es', 'hi', 'tr', 'uk'] | None = None
    per_day: int | None = Field(default=None, ge=1, le=200)
    feeds: list[str] | None = Field(default=None, max_length=20)
    sitemaps: list[str] | None = Field(default=None, max_length=20)
    enabled: bool | None = None
    check_every_minutes: int | None = Field(default=None, ge=5)
    daily_cap: int | None = Field(default=None, ge=1)
    min_interval_seconds: float | None = Field(default=None, ge=0.5, allow_inf_nan=False)
    max_parallel: int | None = Field(default=None, ge=1, le=4)

    @field_validator('domain')
    @classmethod
    def domain_valid(cls, value):
        return _domain(value) if value is not None else value

    @field_validator('feeds', 'sitemaps')
    @classmethod
    def sources_valid(cls, value):
        return [_source_url(v) for v in value] if value is not None else value


class SiteOut(SiteCreate):
    id: UUID
    next_check_at: datetime
    last_scheduled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_crawl: CrawlOut | None = None


def site_row(conn, site_id, lock=False):
    query = sa.select(sites).where(sites.c.id == site_id)
    return conn.execute(query.with_for_update() if lock else query).one()


def site_out(conn, row):
    last = conn.execute(sa.select(crawl_runs).where(crawl_runs.c.site_id == row.id)
                        .order_by(crawl_runs.c.created_at.desc(), crawl_runs.c.id.desc()).limit(1)).first()
    return SiteOut(**row._mapping, last_crawl=_crawl_out(conn, last) if last else None)


def conflict(exc):
    if getattr(exc.orig, 'sqlstate', None) == '23505':
        raise HTTPException(409, 'site name or domain already exists') from exc
    raise exc


@router.get('/sites')
def list_sites(paging: Annotated[Paging, Depends()], engine: Engine) -> Page[SiteOut]:
    with engine.connect() as conn:
        return paginate(conn, sa.select(sites).order_by(sites.c.name, sites.c.id), paging, lambda row: site_out(conn, row))


@router.post('/sites', status_code=201)
def create_site(body: SiteCreate, engine: Engine) -> SiteOut:
    try:
        with engine.begin() as conn:
            row = conn.execute(sites.insert().values(id=uuid4(), **body.model_dump()).returning(sites)).one()
            return site_out(conn, row)
    except IntegrityError as exc:
        conflict(exc)


@router.get('/sites/{site_id}')
def get_site(site_id: UUID, engine: Engine) -> SiteOut:
    with engine.connect() as conn:
        return site_out(conn, site_row(conn, site_id))


@router.patch('/sites/{site_id}')
def update_site(site_id: UUID, body: SitePatch, engine: Engine) -> SiteOut:
    values = body.model_dump(exclude_unset=True)
    if any(v is None for k, v in values.items() if k != 'per_day'):
        raise HTTPException(422, 'null is not allowed')
    try:
        with engine.begin() as conn:
            row = site_row(conn, site_id, lock=True)
            now = datetime.now(timezone.utc)
            if 'check_every_minutes' in values and values['check_every_minutes'] != row.check_every_minutes:
                values['next_check_at'] = (row.last_scheduled_at or now) + timedelta(minutes=values['check_every_minutes'])
            values['updated_at'] = now
            row = conn.execute(sites.update().where(sites.c.id == site_id).values(**values).returning(sites)).one()
            return site_out(conn, row)
    except IntegrityError as exc:
        conflict(exc)


@router.delete('/sites/{site_id}', status_code=204)
def delete_site(site_id: UUID, engine: Engine) -> Response:
    with engine.begin() as conn:
        site_row(conn, site_id, lock=True)
        if conn.scalar(sa.select(sa.exists().where(crawl_runs.c.site_id == site_id))):
            raise HTTPException(409, 'site has crawls; disable it instead')
        conn.execute(sites.delete().where(sites.c.id == site_id))
    return Response(status_code=204)


@router.post('/sites/{site_id}/check', status_code=202)
def check_site(site_id: UUID, engine: Engine) -> CrawlOut:
    try:
        crawl_id = queue_check(engine, site_id)
    except SiteNotFound as exc:
        raise HTTPException(404, 'site not found') from exc
    except AlreadyQueued as exc:
        raise HTTPException(409, 'crawl already active') from exc
    with engine.connect() as conn:
        return _crawl_out(conn, conn.execute(sa.select(crawl_runs).where(crawl_runs.c.id == crawl_id)).one())


@router.get('/sites/{site_id}/crawls')
def site_crawls(site_id: UUID, paging: Annotated[Paging, Depends()], engine: Engine) -> Page[CrawlOut]:
    with engine.connect() as conn:
        site_row(conn, site_id)
        query = sa.select(crawl_runs).where(crawl_runs.c.site_id == site_id).order_by(crawl_runs.c.created_at.desc(), crawl_runs.c.id.desc())
        page = paginate(conn, query, paging, lambda row: row)
        counters = crawl_counters_many(conn, [row.id for row in page.items])
        return Page(items=[_crawl_out(conn, row, counters[row.id]) for row in page.items], total=page.total, offset=page.offset, limit=page.limit)
