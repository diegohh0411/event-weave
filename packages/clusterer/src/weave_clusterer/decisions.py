"""Decision cache: identical System One requests are answered from the table, not the model."""
import hashlib
import json

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
import sqlalchemy as sa
from weave_core.schema import decision_cache
from weave_core.stages import Capability
from weave_inference.gateway import Inference
from weave_inference.types import SystemOneRequest, SystemOneResponse


def decision_key(deployment: str, request: SystemOneRequest) -> str:
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{deployment}\n{canonical}".encode()).hexdigest()


def cached_system_one(inference: Inference, session: Session, request: SystemOneRequest) -> SystemOneResponse:
    """A hit returns the stored response with no call and no ledger row; a miss calls and stores."""
    deployment = inference.deployment(Capability.system_one).id
    key = decision_key(deployment, request)
    hit = session.scalar(sa.select(decision_cache.c.response).where(decision_cache.c.key == key))
    if hit is not None:
        return SystemOneResponse.model_validate(hit)
    response = inference.system_one(request).value
    session.execute(insert(decision_cache).values(key=key, deployment=deployment,
                                                  response=response.model_dump(mode="json"))
                    .on_conflict_do_nothing(index_elements=["key"]))
    return response
