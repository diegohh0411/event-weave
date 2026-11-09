from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from weave_core.schema import crawl_articles, crawl_runs, crawl_sources
from .requests import CrawlRequest


@dataclass(frozen=True)
class ClaimedCrawl:
    id: UUID
    token: UUID
    request: CrawlRequest
    started_at: datetime


def create_crawl(engine, request: CrawlRequest) -> UUID:
    request = CrawlRequest.model_validate(request)
    crawl_id = uuid4()
    with engine.begin() as conn:
        conn.execute(crawl_runs.insert().values(id=crawl_id, request=request.model_dump(mode="json"),
            status="queued", created_at=datetime.now(timezone.utc), progress={}))
    return crawl_id


def claim_crawl(engine, now: datetime, lease_seconds: int = 120) -> ClaimedCrawl | None:
    with engine.begin() as conn:
        row = conn.execute(sa.select(crawl_runs).where(sa.or_(
            crawl_runs.c.status == "queued",
            sa.and_(crawl_runs.c.status == "running", crawl_runs.c.lease_until <= now),
        )).order_by(crawl_runs.c.created_at, crawl_runs.c.id).with_for_update(skip_locked=True).limit(1)).mappings().first()
        if row is None:
            return None
        token = uuid4()
        conn.execute(crawl_runs.update().where(crawl_runs.c.id == row["id"]).values(
            status="running", claim_token=token, lease_until=now + timedelta(seconds=lease_seconds),
            started_at=row["started_at"] or now))
        return ClaimedCrawl(row["id"], token, CrawlRequest.model_validate(row["request"]),
                            row["started_at"] or now)


def lock_owned(conn, crawl_id, token):
    """Lock the claim row in the caller's write transaction; serialize with reclaim."""
    return conn.scalar(sa.select(crawl_runs.c.id).where(
        crawl_runs.c.id == crawl_id, crawl_runs.c.status == "running",
        crawl_runs.c.claim_token == token).with_for_update()) is not None


def heartbeat(engine, id, token, now) -> bool:
    with engine.begin() as conn:
        result = conn.execute(crawl_runs.update().where(crawl_runs.c.id == id,
            crawl_runs.c.status == "running", crawl_runs.c.claim_token == token).values(
                lease_until=now + timedelta(seconds=120)))
        return result.rowcount == 1


def release_crawl(engine, id, token, now) -> bool:
    """Expire a stopped worker's claim without allowing it to release a newer owner."""
    with engine.begin() as conn:
        result = conn.execute(crawl_runs.update().where(
            crawl_runs.c.id == id, crawl_runs.c.status == 'running',
            crawl_runs.c.claim_token == token).values(lease_until=now))
        return result.rowcount == 1


def finish_crawl(engine, id, token, error=None) -> bool:
    with engine.begin() as conn:
        result = conn.execute(crawl_runs.update().where(crawl_runs.c.id == id,
            crawl_runs.c.status == "running", crawl_runs.c.claim_token == token).values(
                status="failed" if error is not None else "completed", error=error,
                finished_at=datetime.now(timezone.utc), claim_token=None, lease_until=None))
        return result.rowcount == 1


def record_article(engine, id, token, outcome) -> bool:
    values = dict(outcome)
    values.update(crawl_id=id, updated_at=datetime.now(timezone.utc))
    values.setdefault("fetched", False)
    values.setdefault("reason", None)
    values.setdefault("document_id", None)
    with engine.begin() as conn:
        if not lock_owned(conn, id, token):
            return False
        stmt = insert(crawl_articles).values(**values)
        conn.execute(stmt.on_conflict_do_update(index_elements=["crawl_id", "canonical_url"],
            set_={key: getattr(stmt.excluded, key) for key in values if key not in ("crawl_id", "canonical_url")},
            where=crawl_articles.c.document_id.is_(None)))
        return True


def record_source(engine, id, token, outcome) -> bool:
    values = dict(outcome)
    values["crawl_id"] = id
    values.setdefault("error", None)
    with engine.begin() as conn:
        if not lock_owned(conn, id, token):
            return False
        stmt = insert(crawl_sources).values(**values)
        conn.execute(stmt.on_conflict_do_update(index_elements=["crawl_id", "url"],
            set_={key: getattr(stmt.excluded, key) for key in values if key not in ("crawl_id", "url")}))
        return True


def crawl_counters(conn, crawl_id):
    rows = conn.execute(sa.select(crawl_articles.c.status, crawl_articles.c.reason, crawl_articles.c.fetched)
                        .where(crawl_articles.c.crawl_id == crawl_id)).all()
    counters = {key: 0 for key in ("discovered", "fetched", "stored", "existing", "duplicates", "rejected", "errors")}
    reasons = {}
    for status, reason, fetched in rows:
        counters["discovered"] += 1
        counters["fetched"] += int(fetched)
        counters[{"duplicate": "duplicates", "error": "errors"}.get(status, status)] += 1
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    counters["reasons"] = reasons
    return counters
