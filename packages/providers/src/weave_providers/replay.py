"""The replay provide-stage adapter: documents already stored are fed into a new run."""

from datetime import datetime
from typing import Mapping
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel

from weave_core.adapters import AdapterContext, AdapterSpec, StageResult, UnitRef
from weave_core.db import make_engine
from weave_core.schema import crawl_articles, crawl_runs, documents, run_documents, runs
from weave_core.settings import Settings
from weave_core.stages import Stage, UnitKind


class ReplaySettings(BaseModel):
    provider: str = "crawl"
    crawl_id: UUID | None = None
    languages: list[str] | None = None
    outlets: list[str] | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None
    limit: int | None = None


class ReplayProvider:
    spec = AdapterSpec(
        name="replay", version="1", stage=Stage.provide, input_kind=UnitKind.run,
        settings_model=ReplaySettings,
        cacheable=False,  # each run creates its own document snapshot
    )

    def __init__(self, settings: ReplaySettings, ctx: AdapterContext):
        self.settings = settings
        self.ctx = ctx

    def process(self, unit: UnitRef, upstream: Mapping[Stage, dict]) -> StageResult:
        s = self.settings
        d = documents.c
        query = sa.select(d.id, d.content_hash, d.order_key).where(d.provider == s.provider, d.superseded_at.is_(None))
        if s.crawl_id is not None:
            members = sa.select(crawl_articles.c.document_id).where(
                crawl_articles.c.crawl_id == s.crawl_id,
                crawl_articles.c.document_id.is_not(None))
            query = query.where(d.id.in_(members))
        if s.languages is not None:
            query = query.where(d.language.in_(s.languages))
        if s.outlets is not None:
            query = query.where(d.outlet.in_(s.outlets))
        if s.published_from is not None:
            query = query.where(d.published_at >= s.published_from)
        if s.published_to is not None:
            query = query.where(d.published_at < s.published_to)
        query = query.order_by(d.order_key, d.url, d.id)
        if s.limit is not None:
            query = query.limit(s.limit)

        # Adapters receive no engine; like the CLI and API, read the database from the environment.
        engine = make_engine(Settings.from_env().database_url)
        try:
            with engine.begin() as conn:
                snapshot_at = conn.execute(
                    sa.select(runs.c.snapshot_at).where(runs.c.id == self.ctx.run_id).with_for_update()
                ).one().snapshot_at
                if s.crawl_id is not None:
                    status = conn.scalar(sa.select(crawl_runs.c.status).where(crawl_runs.c.id == s.crawl_id))
                    if status is None:
                        raise ValueError(f'Unknown crawl {s.crawl_id}; choose an existing completed crawl')
                    if status != 'completed':
                        raise ValueError(f'Crawl {s.crawl_id} is {status}, not completed; choose a completed crawl')
                rows = conn.execute(
                    sa.select(d.id, d.content_hash, d.order_key)
                    .select_from(run_documents.join(documents, run_documents.c.document_id == d.id))
                    .where(run_documents.c.run_id == self.ctx.run_id)
                    .order_by(run_documents.c.position)
                ).all()
                if not rows and snapshot_at is None:
                    rows = conn.execute(query).all()
                    if rows:
                        conn.execute(sa.insert(run_documents), [
                            {"run_id": self.ctx.run_id, "position": position, "document_id": row.id}
                            for position, row in enumerate(rows)
                        ])
                    conn.execute(sa.update(runs).where(runs.c.id == self.ctx.run_id)
                                 .values(snapshot_at=sa.func.now()))
        finally:
            engine.dispose()

        emits = [UnitRef(UnitKind.document, r.id, r.content_hash, tuple(r.order_key)) for r in rows]
        return StageResult(output={"documents": len(emits)}, emits=emits, documents=[])
