from __future__ import annotations

import os
from contextvars import ContextVar
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx
from weave_core.stages import Capability

from .catalog import Catalog
from .types import Deployment, Provider


@dataclass(frozen=True)
class Readiness:
    ready: bool
    reason: str | None = None
    server_version: str | None = None
    reported_model_id: str | None = None
    advertised: frozenset[Capability] = field(default_factory=frozenset)

    def render(self) -> str:
        return 'ready' if self.ready else f'not ready: {self.reason}'


def _not_ready(reason: str, **kw) -> Readiness:
    return Readiness(False, reason, **kw)


def _modality_caps(modalities: list[str]) -> frozenset[Capability]:
    caps = set()
    if 'decisions' in modalities:
        caps.add(Capability.system_one)
    if 'embeddings' in modalities:
        caps.add(Capability.embeddings)
    if not caps or 'text' in modalities:
        if 'decisions' not in modalities and 'embeddings' not in modalities:
            caps.add(Capability.llm)
    return frozenset(caps)


def _matches(listed: str, wanted: str) -> bool:
    return listed == wanted or listed.endswith('/' + wanted)


# A listing is {listed_model_id: advertised capabilities}.
Listing = dict[str, frozenset[Capability]]


def _entries(data: dict) -> list[dict]:
    return [m for m in data.get('data', []) if isinstance(m, dict) and 'id' in m]


def _from_architecture(data: dict) -> Listing:
    return {m['id']: _modality_caps((m.get('architecture') or {}).get('output_modalities') or ['text'])
            for m in _entries(data)}


def _list_llamacpp(client: httpx.Client, p: Provider, headers: dict, env) -> Listing:
    return _from_architecture(_get(client, f'{p.base_url.rstrip("/")}/models', headers))


def _list_openrouter(client, p, headers, env) -> Listing:
    base = p.base_url.rstrip('/')
    listing = _from_architecture(_get(client, f'{base}/models', headers))
    try:  # embeddings models are listed separately; best effort
        listing.update({k: frozenset({Capability.embeddings})
                        for k in _from_architecture(_get(client, f'{base}/embeddings/models', headers))})
    except httpx.HTTPError:
        pass
    return listing


def _list_typesafe(client, p, headers, env) -> Listing:
    data = _get(client, f'{p.base_url.rstrip("/")}/v1/models', headers)
    return {m['id']: frozenset({Capability.system_one}) for m in _entries(data)}


def _cloudflare_caps(task: str) -> frozenset[Capability]:
    t = task.lower()
    if 'embed' in t:
        return frozenset({Capability.embeddings})
    if 'generation' in t:
        return frozenset({Capability.llm})
    return frozenset({Capability.extraction})


def _list_cloudflare(client, p, headers, env) -> Listing:
    account = env['CLOUDFLARE_ACCOUNT_ID']
    data = _get(client, f'{p.base_url.rstrip("/")}/accounts/{account}/ai/models/search', headers)
    return {m['name']: _cloudflare_caps((m.get('task') or {}).get('name', ''))
            for m in data.get('result', []) if isinstance(m, dict) and 'name' in m}


_server: ContextVar[str | None] = ContextVar('server', default=None)


def _get(client: httpx.Client, url: str, headers: dict) -> dict:
    r = client.get(url, headers=headers)
    r.raise_for_status()
    _server.set(r.headers.get('server'))
    return r.json()


# Generic OpenAI-compatible server: GET {base}/models, capability from architecture if given, else llm.
_LISTERS = {'llamacpp': _list_llamacpp, 'openrouter': _list_openrouter, 'typesafe': _list_typesafe,
            'cloudflare': _list_cloudflare, 'openai': _list_llamacpp}

# Extra env var a kind needs besides provider.credential_env.
_EXTRA_ENV = {'cloudflare': 'CLOUDFLARE_ACCOUNT_ID'}


def _probe_provider(catalog: Catalog, p: Provider, deployments: list[Deployment], env: Mapping[str, str],
                    transport: httpx.BaseTransport | None, timeout: float) -> dict[str, Readiness]:
    if p.kind == 'stub':
        return {d.id: Readiness(True, advertised=frozenset(Capability)) for d in deployments}
    missing = [v for v in (p.credential_env, _EXTRA_ENV.get(p.kind)) if v and not env.get(v)]
    if missing:
        return {d.id: _not_ready(f'credential missing: {missing[0]}') for d in deployments}
    headers = {'authorization': f'Bearer {env[p.credential_env]}'} if p.credential_env else {}
    _server.set(None)
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            listing = _LISTERS[p.kind](client, p, headers, env)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {d.id: _not_ready('unreachable') for d in deployments}
    version = _server.get()
    out = {}
    for d in deployments:
        wanted = d.settings.get('remote_model') or d.model
        hit = next((k for k in listing if _matches(k, wanted)), None)
        if hit is None:
            out[d.id] = _not_ready('model missing')
            continue
        caps = listing[hit]
        needed = catalog.models[d.model].capability
        if needed not in caps:
            out[d.id] = _not_ready('capability not advertised', reported_model_id=hit, advertised=caps, server_version=version)
        else:
            out[d.id] = Readiness(True, reported_model_id=hit, advertised=caps, server_version=version)
    return out


def probe(catalog: Catalog, env: Mapping[str, str] = os.environ,
          transport: httpx.BaseTransport | None = None, timeout: float = 5.0) -> dict[str, Readiness]:
    by_provider: dict[str, list[Deployment]] = {}
    for d in catalog.deployments.values():
        by_provider.setdefault(d.provider, []).append(d)
    result: dict[str, Readiness] = {}
    if by_provider:
        with ThreadPoolExecutor(max_workers=len(by_provider)) as pool:
            futures = [pool.submit(_probe_provider, catalog, catalog.providers[pid], deps, env, transport, timeout)
                       for pid, deps in by_provider.items()]
            for f in futures:
                result.update(f.result())
    catalog.readiness = result
    return result
