from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from weave_api.deps import get_engine
from weave_api.paging import Page, Paging
from weave_api.queries import runs as run_q
from weave_enrichers.report import sample, sample_total

router = APIRouter()


class SampleItem(BaseModel):
    id: str
    mention_id: str
    document_id: str
    enricher: str
    enricher_version: str
    sentence: str
    enrichment: dict[str, Any]
    discarded: dict[str, Any]


@router.get("/runs/{run_id}/enrichments/sample")
def enrichment_sample(run_id: UUID, paging: Annotated[Paging, Depends()], engine: Annotated[Any, Depends(get_engine)],
                      seed: int = 0, enricher: str | None = None) -> Page[SampleItem]:
    """`limit` is the sample size; `offset` slices the seeded order; `total` counts the (enricher-filtered) population."""
    with engine.connect() as conn:
        run_q.run_row(conn, run_id)
    rows = sample(engine, run_id, paging.limit, seed, paging.offset, enricher)
    return Page(items=[SampleItem(**r) for r in rows], total=sample_total(engine, run_id, enricher),
                offset=paging.offset, limit=paging.limit)
