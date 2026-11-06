"""Gold extractor (Track 1): exactly the MCECR gold spans, with no ids or cluster information."""
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from weave_core.adapters import AdapterSpec
from weave_core.db import make_engine
from weave_core.schema import gold_mentions
from weave_core.settings import Settings
from weave_core.stages import Stage, UnitKind

from .base import ExtractorBase, SpanBatch
from .shape import Doc


class GoldSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldExtractor(ExtractorBase):
    spec = AdapterSpec(name="gold", version="1", stage=Stage.extract, input_kind=UnitKind.document,
                       settings_model=GoldSettings)

    def spans(self, doc: Doc) -> SpanBatch:
        g = gold_mentions.c
        # Spans only: mention_id, pairs, topic and split are never read.
        query = sa.select(g.start, g.end).where(g.document_id == doc.id)
        engine = make_engine(Settings.from_env().database_url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(query).all()
        finally:
            engine.dispose()
        return SpanBatch([(r.start, r.end, None) for r in rows])
