"""Vector helpers: the pgvector literal and content-addressed member-vector ids."""
import hashlib

from weave_core.vector import Vector  # noqa: F401  (re-exported: the column type)


def to_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def member_vector_id(enrichment_id: str, field: str, kind: str, deployment: str, dimension: int) -> str:
    return hashlib.sha256(":".join([enrichment_id, field, kind, deployment, str(dimension)]).encode()).hexdigest()[:32]
