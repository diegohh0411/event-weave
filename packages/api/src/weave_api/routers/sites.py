from datetime import date, datetime, timezone, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError

from weave_api.paging import Page, Paging, paginate
from weave_api.routers.crawls import Engine, CrawlOut, _crawl_out
from weave_core.schema import sites, crawl_runs, crawl_articles, backfill_sitemaps, backfill_urls
from weave_providers.crawl.jobs import crawl_counters_many
from weave_providers.crawl.requests import CrawlSite, _domain, _source_url
from weave_providers.crawl.scheduler import queue_check, AlreadyQueued, SiteNotFound

router = APIRouter()


def validate_backfill(mode, start):
    if mode == 'date' and start is None:
        raise ValueError('backfill_from is required for date mode')
    if start is not None and start > datetime.now(timezone.utc).date():
        raise ValueError('backfill_from cannot be in the future')


class SiteCreate(CrawlSite):
    enabled: bool = True
    check_every_minutes: int = Field(default=60, ge=5)
    daily_cap: int = Field(default=50, ge=1)
    min_interval_seconds: float = Field(default=2, ge=0.5, allow_inf_nan=False)
    max_parallel: int = Field(default=1, ge=1, le=4)
    backfill_mode: Literal['off', 'date', 'all'] = 'off'
    backfill_from: date | None = None
    backfill_per_hour: int = Field(default=60, ge=1, le=1000)

    @model_validator(mode='after')
    def validate_backfill(self):
        validate_backfill(self.backfill_mode, self.backfill_from)
        return self


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

    backfill_mode: Literal['off', 'date', 'all'] | None = None
    backfill_from: date | None = None
    backfill_per_hour: int | None = Field(default=None, ge=1, le=1000)

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
    backfill_state: str = 'off'
    backfill_progress: dict[str, int] = Field(default_factory=lambda: {'done': 0, 'found': 0})
    last_crawl: CrawlOut | None = None


def site_row(conn, site_id, lock=False):
    query = sa.select(sites).where(sites.c.id == site_id)
    row = conn.execute(query.with_for_update() if lock else query).first()
    if row is None:
        raise HTTPException(404, "site not found")
    return row


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
        page = paginate(conn, sa.select(sites).order_by(sites.c.name, sites.c.id), paging, lambda row: row)
        ids = [row.id for row in page.items]
        last = conn.execute(sa.select(crawl_runs).where(crawl_runs.c.site_id.in_(ids))
                            .distinct(crawl_runs.c.site_id)
                            .order_by(crawl_runs.c.site_id, crawl_runs.c.created_at.desc(), crawl_runs.c.id.desc())).all() if ids else []
        counters = crawl_counters_many(conn, [row.id for row in last])
        by_site = {row.site_id: _crawl_out(conn, row, counters[row.id]) for row in last}
        counts = conn.execute(sa.select(backfill_urls.c.site_id, sa.func.count().label('found'),
            sa.func.count().filter(backfill_urls.c.status == 'done').label('done'))
            .where(backfill_urls.c.site_id.in_(ids)).group_by(backfill_urls.c.site_id)).all() if ids else []
        progress = {r.site_id: {'found': r.found, 'done': r.done} for r in counts}
        return Page(items=[SiteOut(**row._mapping, last_crawl=by_site.get(row.id), backfill_progress=progress.get(row.id, {'done': 0, 'found': 0})) for row in page.items],
                    total=page.total, offset=page.offset, limit=page.limit)


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
    if any(v is None for k, v in values.items() if k not in ('per_day', 'backfill_from')):
        raise HTTPException(422, 'null is not allowed')
    try:
        with engine.begin() as conn:
            row = site_row(conn, site_id, lock=True)
            try:
                validate_backfill(values.get('backfill_mode', row.backfill_mode), values.get('backfill_from', row.backfill_from))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if row.backfill_state in ('running', 'paused') and any(k in values for k in ('backfill_mode', 'backfill_from')):
                raise HTTPException(409, 'cancel backfill before changing settings')
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
        row = conn.execute(sa.select(crawl_runs).where(crawl_runs.c.id == crawl_id)).first()
        if row is None:
            raise HTTPException(404, "queued crawl no longer exists")
        return _crawl_out(conn, row)


@router.get('/sites/{site_id}/crawls')
def site_crawls(site_id: UUID, paging: Annotated[Paging, Depends()], engine: Engine, trigger: Literal['adhoc', 'manual', 'schedule', 'backfill'] | None = None) -> Page[CrawlOut]:
    with engine.connect() as conn:
        site_row(conn, site_id)
        query = sa.select(crawl_runs).where(crawl_runs.c.site_id == site_id).order_by(crawl_runs.c.created_at.desc(), crawl_runs.c.id.desc())
        if trigger is not None:
            query = query.where(crawl_runs.c.trigger == trigger)
        page = paginate(conn, query, paging, lambda row: row)
        counters = crawl_counters_many(conn, [row.id for row in page.items])
        return Page(items=[_crawl_out(conn, row, counters[row.id]) for row in page.items], total=page.total, offset=page.offset, limit=page.limit)


class SitemapProgress(BaseModel):
    total: int
    pending: int
    done: int
    skipped: int
    error: int


class UrlProgress(BaseModel):
    found: int
    pending: int
    done: int
    stored: int
    existing: int
    rejected: int
    error: int


class BackfillProgress(BaseModel):
    state: Literal['off', 'running', 'paused', 'done', 'failed']
    mode: Literal['off', 'date', 'all']
    from_: date | None = Field(alias='from')
    until: datetime | None
    per_hour: int
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    sitemaps: SitemapProgress
    urls: UrlProgress
    oldest_reached: datetime | None
    fetched_last_hour: int
    eta: float | None


class BackfillAction(BaseModel):
    action: Literal['start', 'pause', 'resume', 'cancel']


def backfill_progress(conn, row):
    maps = conn.execute(sa.select(backfill_sitemaps.c.status, sa.func.count()).where(backfill_sitemaps.c.site_id == row.id).group_by(backfill_sitemaps.c.status)).all()
    urls = conn.execute(sa.select(backfill_urls.c.status, backfill_urls.c.outcome, sa.func.count()).where(backfill_urls.c.site_id == row.id).group_by(backfill_urls.c.status, backfill_urls.c.outcome)).all()
    sm = {key: 0 for key in ('total', 'pending', 'done', 'skipped', 'error')}
    for status, count in maps:
        sm[status] += count
        sm['total'] += count
    u = {key: 0 for key in ('found', 'pending', 'done', 'stored', 'existing', 'rejected', 'error')}
    for status, outcome, count in urls:
        u[status] += count
        u['found'] += count
        if outcome in u:
            u[outcome] += count
    oldest = conn.scalar(sa.select(sa.func.min(backfill_urls.c.published_at)).where(backfill_urls.c.site_id == row.id, backfill_urls.c.status == 'done'))
    fetched = conn.scalar(sa.select(sa.func.count()).select_from(crawl_articles.join(crawl_runs, crawl_articles.c.crawl_id == crawl_runs.c.id)).where(crawl_runs.c.site_id == row.id, crawl_runs.c.trigger == 'backfill', crawl_articles.c.fetched.is_(True), crawl_articles.c.updated_at >= datetime.now(timezone.utc) - timedelta(hours=1)))
    return dict(state=row.backfill_state, mode=row.backfill_mode, **{'from': row.backfill_from}, until=row.backfill_until, per_hour=row.backfill_per_hour, started_at=row.backfill_started_at, finished_at=row.backfill_finished_at, error=row.backfill_error, sitemaps=sm, urls=u, oldest_reached=oldest, fetched_last_hour=fetched, eta=None if sm['pending'] else u['pending'] / row.backfill_per_hour)


@router.get('/sites/{site_id}/backfill')
def get_backfill(site_id: UUID, engine: Engine) -> BackfillProgress:
    with engine.connect() as conn:
        return backfill_progress(conn, site_row(conn, site_id))


@router.post('/sites/{site_id}/backfill')
def control_backfill(site_id: UUID, body: BackfillAction, engine: Engine) -> BackfillProgress:
    with engine.begin() as conn:
        row = site_row(conn, site_id, lock=True)
        if not row.enabled:
            raise HTTPException(409, 'site disabled')
        if row.backfill_mode == 'off':
            raise HTTPException(422, 'backfill mode is off')
        transitions = {'start': ('off', 'done', 'failed'), 'pause': ('running',), 'resume': ('paused',), 'cancel': ('running', 'paused', 'done', 'failed')}
        if row.backfill_state not in transitions[body.action]:
            raise HTTPException(409, 'invalid backfill transition')
        now = datetime.now(timezone.utc)
        values = {'backfill_state': {'start': 'running', 'pause': 'paused', 'resume': 'running', 'cancel': 'off'}[body.action]}
        if body.action == 'start':
            if (row.backfill_mode, row.backfill_from) != (row.backfill_frontier_mode, row.backfill_frontier_from):
                conn.execute(backfill_urls.delete().where(backfill_urls.c.site_id == site_id))
                conn.execute(backfill_sitemaps.delete().where(backfill_sitemaps.c.site_id == site_id))
            values.update(backfill_frontier_mode=row.backfill_mode, backfill_frontier_from=row.backfill_from)
            values.update(backfill_until=now, backfill_started_at=now, backfill_finished_at=None, backfill_error=None)
        if body.action == 'cancel':
            conn.execute(backfill_urls.delete().where(backfill_urls.c.site_id == site_id))
            conn.execute(backfill_sitemaps.delete().where(backfill_sitemaps.c.site_id == site_id))
        row = conn.execute(sites.update().where(sites.c.id == site_id).values(**values).returning(sites)).one()
        return backfill_progress(conn, row)
