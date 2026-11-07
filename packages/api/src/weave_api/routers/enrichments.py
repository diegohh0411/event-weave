from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from weave_api.deps import get_engine
from weave_api.paging import Page, Paging
from weave_api.queries import runs as run_q
from weave_enrichers.report import sample

router = APIRouter()

MAX_OVERSAMPLE = 800


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
    """`limit` is the sample size; `offset` slices the seeded order. With `enricher`, oversample 4x (cap 800), then filter."""
    with engine.connect() as conn:
        run_q.run_row(conn, run_id)
    want = paging.offset + paging.limit
    rows = sample(engine, run_id, want if enricher is None else min(want * 4, MAX_OVERSAMPLE), seed)
    if enricher is not None:
        rows = [r for r in rows if r["enricher"] == enricher]
    rows = rows[paging.offset:want]
    return Page(items=[SampleItem(**r) for r in rows], total=len(rows), offset=paging.offset, limit=paging.limit)
