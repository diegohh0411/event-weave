"""The enrichment shape: four fields whose spans are document character offsets."""
import hashlib

from pydantic import BaseModel


class Field(BaseModel):
    text: str | None = None
    spans: list[tuple[int, int]] = []
    confidence: float | None = None


class TimeField(Field):
    normalized: str | None = None  # ISO 8601 date, date-time, or start/end interval


class Enrichment(BaseModel):
    action: Field = Field()
    participants: Field = Field()
    location: Field = Field()
    time: TimeField = TimeField()


def enrichment_id(mention_id: str, enricher: str, version: str, deployment: str | None) -> str:
    return hashlib.sha256(":".join([mention_id, enricher, version, deployment or ""]).encode()).hexdigest()[:32]
