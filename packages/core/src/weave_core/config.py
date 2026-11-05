import hashlib
import json
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from .registry import Registry
from .stages import Capability, OPTIONAL_STAGES, PIPELINE, Stage, Track


class StageChoice(BaseModel):
    adapter: str
    settings: dict = Field(default_factory=dict)
    deployments: dict[Capability, str] = Field(default_factory=dict)


class RunConfig(BaseModel):
    name: str
    track: Track
    seed: int
    stages: dict[Stage, StageChoice]


class DeploymentCatalog(Protocol):
    def capability_of(self, deployment_id: str) -> Capability | None: ...
    def is_ready(self, deployment_id: str) -> bool: ...


class StaticCatalog(dict[str, tuple[Capability, bool]]):
    def capability_of(self, deployment_id: str) -> Capability | None:
        item = self.get(deployment_id)
        return item[0] if item else None

    def is_ready(self, deployment_id: str) -> bool:
        item = self.get(deployment_id)
        return item[1] if item else False


class ConfigError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__('; '.join(problems))


def validate(cfg: RunConfig, registry: Registry, catalog: DeploymentCatalog) -> RunConfig:
    normalized = cfg.model_copy(deep=True)
    problems = [f'missing stage: {s}' for s in PIPELINE if s not in cfg.stages and s not in OPTIONAL_STAGES]
    for stage in PIPELINE:
        if stage not in normalized.stages:
            continue
        choice = normalized.stages[stage]
        prefix = f'stage {stage}:'
        try:
            spec = registry.get(stage, choice.adapter).spec
        except KeyError:
            problems.append(f"{prefix} unknown adapter '{choice.adapter}'")
            continue
        try:
            choice.settings = spec.settings_model.model_validate(choice.settings).model_dump(mode='json')
        except ValidationError as exc:
            problems.append(f'{prefix} invalid settings: {exc}')
        for capability in sorted(spec.capabilities):
            if capability not in choice.deployments:
                default = getattr(catalog, 'default_for', lambda _c: None)(capability)
                if default:
                    choice.deployments[capability] = default
                    continue
                problems.append(f"{prefix} adapter '{choice.adapter}' needs a deployment for '{capability}'")
        for capability, deployment in sorted(choice.deployments.items()):
            actual = catalog.capability_of(deployment)
            if actual is None:
                problems.append(f"{prefix} deployment '{deployment}' is unknown")
            elif actual != capability:
                problems.append(f"{prefix} deployment '{deployment}' does not provide '{capability}'")
            elif not catalog.is_ready(deployment):
                problems.append(f"{prefix} deployment '{deployment}' is not ready")
    if problems:
        raise ConfigError(problems)
    return normalized


def config_hash(cfg: RunConfig) -> str:
    return hashlib.sha256(json.dumps(cfg.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
