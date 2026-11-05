from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError
from weave_core.stages import Capability

from .types import Deployment, Model, Provider

if TYPE_CHECKING:
    from .probe import Readiness


_SECRET_FIELDS = {'api_key', 'token', 'secret', 'password', 'api_token'}


class CatalogError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__('; '.join(problems))


@dataclass
class Catalog:
    models: dict[str, Model] = field(default_factory=dict)
    providers: dict[str, Provider] = field(default_factory=dict)
    deployments: dict[str, Deployment] = field(default_factory=dict)
    defaults: dict[Capability, str] = field(default_factory=dict)
    readiness: dict[str, Readiness] = field(default_factory=dict)

    def capability_of(self, deployment_id: str) -> Capability | None:
        d = self.deployments.get(deployment_id)
        return self.models[d.model].capability if d else None

    def is_ready(self, deployment_id: str) -> bool:
        r = self.readiness.get(deployment_id)
        return bool(r and r.ready)

    def default_for(self, capability: Capability) -> str | None:
        return self.defaults.get(capability)


def _build(kind, section: dict, cls, problems: list[str]) -> dict:
    out = {}
    for key, body in (section or {}).items():
        try:
            out[key] = cls(id=key, **(body or {}))
        except ValidationError as e:
            for err in e.errors():
                loc = '.'.join(str(x) for x in err['loc'])
                msg = err['msg']
                if kind == 'provider' and err['type'] == 'extra_forbidden' and loc in _SECRET_FIELDS:
                    msg = 'secrets are not allowed in the catalog; name an env var in credential_env'
                problems.append(f"{kind} '{key}': {loc}: {msg}")
    return out


def load_catalog(path: Path) -> Catalog:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    problems: list[str] = []
    models = _build('model', raw.get('models'), Model, problems)
    providers = _build('provider', raw.get('providers'), Provider, problems)
    deployments = _build('deployment', raw.get('deployments'), Deployment, problems)
    for d in deployments.values():
        if d.model not in models:
            problems.append(f"deployment '{d.id}': unknown model '{d.model}'")
        if d.provider not in providers:
            problems.append(f"deployment '{d.id}': unknown provider '{d.provider}'")
    defaults: dict[Capability, str] = {}
    for cap_name, dep_id in (raw.get('defaults') or {}).items():
        try:
            cap = Capability(cap_name)
        except ValueError:
            problems.append(f"default '{cap_name}': unknown capability")
            continue
        d = deployments.get(dep_id)
        if d is None:
            problems.append(f"default '{cap_name}': unknown deployment '{dep_id}'")
            continue
        prov = providers.get(d.provider)
        model = models.get(d.model)
        if prov is None or model is None:
            continue
        if prov.kind != 'openrouter':
            problems.append(f"default '{cap_name}': deployment '{dep_id}' is not on an openrouter provider")
        if model.capability != cap:
            problems.append(f"default '{cap_name}': deployment '{dep_id}' has capability '{model.capability}'")
        defaults[cap] = dep_id
    if problems:
        raise CatalogError(problems)
    return Catalog(models, providers, deployments, defaults)
