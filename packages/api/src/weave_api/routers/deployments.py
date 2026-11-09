from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from weave_api.deps import get_service
from weave_api.paging import Page, Paging, paginate_list
from weave_core.config import StaticCatalog
from weave_core.runs import RunService
from weave_core.stages import Capability
from weave_inference.probe import probe

router = APIRouter()


class DeploymentOut(BaseModel):
    id: str
    capability: Capability
    ready: bool
    reason: str | None
    is_default: bool
    local: bool


def _all(catalog) -> list[DeploymentOut]:
    if isinstance(catalog, StaticCatalog):
        return [DeploymentOut(id=k, capability=cap, ready=ready, reason=None, is_default=False, local=False)
                for k, (cap, ready) in sorted(catalog.items())]
    defaults = set(catalog.defaults.values())
    out = []
    for dep_id, dep in sorted(catalog.deployments.items()):
        readiness = catalog.readiness.get(dep_id)
        ready = catalog.is_ready(dep_id)
        out.append(DeploymentOut(id=dep_id, capability=catalog.capability_of(dep_id), ready=ready,
                                 reason=(readiness.reason if readiness and not ready else 'not probed') if not ready else None,
                                 is_default=dep_id in defaults, local=dep.local))
    return out


@router.get("/deployments")
def list_deployments(paging: Annotated[Paging, Depends()], service: Annotated[RunService, Depends(get_service)],
                     capability: Capability | None = None, ready: bool | None = None) -> Page[DeploymentOut]:
    items = [d for d in _all(service.catalog)
             if capability in (None, d.capability) and ready in (None, d.ready)]
    return paginate_list(items, paging)


@router.post("/deployments/probe")
def reprobe(paging: Annotated[Paging, Depends()], service: Annotated[RunService, Depends(get_service)]) -> Page[DeploymentOut]:
    catalog = service.catalog
    if isinstance(catalog, StaticCatalog):
        raise HTTPException(409, "no inference catalog configured")
    catalog.readiness = probe(catalog)
    return paginate_list(_all(catalog), paging)
