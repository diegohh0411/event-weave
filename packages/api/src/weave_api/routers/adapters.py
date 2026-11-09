from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from weave_api.deps import get_service
from weave_api.paging import Page, Paging, paginate_list
from weave_core.runs import RunService
from weave_core.stages import OPTIONAL_STAGES, PIPELINE, Capability, Stage, UnitKind

router = APIRouter()


class AdapterOut(BaseModel):
    stage: Stage
    name: str
    version: str
    input_kind: UnitKind
    capabilities: list[Capability]
    optional_stage: bool
    cacheable: bool
    settings_schema: dict


@router.get("/adapters")
def list_adapters(paging: Annotated[Paging, Depends()], service: Annotated[RunService, Depends(get_service)],
                  stage: Stage | None = None) -> Page[AdapterOut]:
    items = [AdapterOut(stage=spec.stage, name=spec.name, version=spec.version, input_kind=spec.input_kind,
                        capabilities=sorted(spec.capabilities), optional_stage=spec.stage in OPTIONAL_STAGES,
                        cacheable=spec.cacheable, settings_schema=spec.settings_model.model_json_schema())
             for s in PIPELINE if stage in (None, s)
             for spec in sorted(service.registry.adapters(s), key=lambda spec: spec.name)]
    return paginate_list(items, paging)
