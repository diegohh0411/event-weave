from __future__ import annotations

import os
import time
from typing import Any

import httpx
import sqlalchemy as sa
from weave_core.adapters import AdapterContext
from weave_core.stages import Capability

from .catalog import Catalog
from .clients import WireClient
from .clients.chat import ChatClient
from .clients.cloudflare import CloudflareClient
from .clients.embeddings import EmbeddingsClient
from .clients.openrouter import OpenRouterDecisionsClient
from .clients.stub import StubClient
from .clients.systemone import SystemOneClient, merge
from .http import limiter_for, send_with_retries
from .ledger import LedgerEntry, cost_of, record, request_hash
from .limits import check_limits
from .types import (ChatRequest, ChatResponse, Deployment, EmbeddingsRequest, EmbeddingsResponse,
                    ExtractionRequest, ExtractionResponse, InferenceError, NotReady, Provider, ProviderError, Result,
                    SystemOneRequest, SystemOneResponse, Usage)

MAX_ATTEMPTS = 3

_WIRE: dict[tuple[Capability, str], type[WireClient]] = {
    (Capability.system_one, "llamacpp"): SystemOneClient,
    (Capability.system_one, "typesafe"): SystemOneClient,
    (Capability.system_one, "openrouter"): OpenRouterDecisionsClient,
    (Capability.system_one, "cloudflare"): CloudflareClient,
    (Capability.extraction, "cloudflare"): CloudflareClient,
    **{(Capability.llm, k): ChatClient for k in ("llamacpp", "openrouter", "openai")},
    **{(Capability.embeddings, k): EmbeddingsClient for k in ("llamacpp", "openrouter", "openai")},
}


class Inference:
    _catalog: Catalog | None = None
    _engine: sa.Engine | None = None
    _transport: httpx.BaseTransport | None = None

    def __init__(self, ctx: AdapterContext):
        self.ctx = ctx

    @classmethod
    def configure(cls, catalog: Catalog, engine: sa.Engine, transport: httpx.BaseTransport | None = None) -> None:
        cls._catalog, cls._engine, cls._transport = catalog, engine, transport

    @classmethod
    def for_context(cls, ctx: AdapterContext) -> "Inference":
        return cls(ctx)

    def system_one(self, req: SystemOneRequest) -> Result[SystemOneResponse]:
        return self._call(Capability.system_one, req)

    def chat(self, req: ChatRequest) -> Result[ChatResponse]:
        return self._call(Capability.llm, req)

    def embed(self, req: EmbeddingsRequest) -> Result[EmbeddingsResponse]:
        return self._call(Capability.embeddings, req)

    def extract(self, req: ExtractionRequest) -> Result[ExtractionResponse]:
        return self._call(Capability.extraction, req)

    # -- internals ---------------------------------------------------------
    def _ledger(self, deployment: str, digest: str, *, status: str, usage: Usage | None = None, cost=0,
                estimated: bool = False, latency_ms: int = 0, attempts: int = 0, error: str | None = None) -> None:
        usage = usage or Usage()
        record(self._engine, LedgerEntry(
            run_id=self.ctx.run_id, stage=str(self.ctx.stage.value) if self.ctx.stage else None,
            adapter=self.ctx.adapter, deployment=deployment, request_hash=digest,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, cached_tokens=usage.cached_tokens,
            cost=cost, estimated=estimated, latency_ms=latency_ms, attempts=attempts, status=status, error=error))

    def _call(self, capability: Capability, request: Any) -> Result:
        catalog, engine = self._catalog, self._engine
        if catalog is None or engine is None:
            raise RuntimeError("Inference.configure() has not been called")
        dep_id = self.ctx.deployments.get(capability)
        if dep_id is None or dep_id not in catalog.deployments:
            raise NotReady(f"no deployment for {capability.value}")  # no deployment id, so no ledger row is possible
        deployment = catalog.deployments[dep_id]
        provider = catalog.providers[deployment.provider]
        digest = request_hash(request.model_dump(mode="json"))
        try:
            if not catalog.is_ready(dep_id):
                r = catalog.readiness.get(dep_id)
                raise NotReady(r.reason if r and r.reason else "unknown")
            check_limits(deployment, request)
            credential = None
            if provider.credential_env:
                credential = os.environ.get(provider.credential_env)
                if not credential:
                    raise NotReady(f"credential missing: {provider.credential_env}")
        except InferenceError as error:
            self._ledger(dep_id, digest, status="refused", error=str(error))
            raise
        start = time.monotonic()
        try:
            value, usage, attempts = self._send(capability, deployment, provider, request, credential)
        except Exception as error:
            if not isinstance(error, InferenceError):
                wrapped = ProviderError(0, f"{type(error).__name__}: {error}")
                wrapped.__cause__ = error
                error = wrapped
            attempts = getattr(error, "attempts", 1)
            partial = getattr(error.__cause__, "partial_usage", None) or getattr(error, "partial_usage", None)
            cost, estimated = cost_of(deployment, partial, partial.reported_cost) if partial else (0, False)
            self._ledger(dep_id, digest, status="error", usage=partial, cost=cost, estimated=estimated,
                         latency_ms=_ms(start), attempts=attempts, error=str(error))
            raise error
        cost, estimated = cost_of(deployment, usage, usage.reported_cost)
        self._ledger(dep_id, digest, status="ok", usage=usage, cost=cost, estimated=estimated,
                     latency_ms=_ms(start), attempts=attempts)
        return Result(value=value, usage=usage, deployment=dep_id)

    def _send(self, capability: Capability, deployment: Deployment, provider: Provider, request: Any, credential: str | None):
        if provider.kind == "stub":
            value, usage = StubClient.for_provider(provider.id, deployment.settings).call(capability, request)
            return value, usage, 1
        wire_cls = _WIRE.get((capability, provider.kind))
        if wire_cls is None:
            raise NotReady(f"{provider.kind} does not support {capability.value}")
        wire = wire_cls()
        limiter = limiter_for(deployment, provider)
        requests = (wire.build_many(deployment, provider, request, credential)
                    if capability == Capability.system_one and hasattr(wire, "build_many")
                    else [wire.build(deployment, provider, request, credential)])
        many = hasattr(wire, "parse_many") and capability == Capability.system_one
        # one sub-request per HTTP request, so each response is parsed (usage and cost kept) as it arrives
        subs = ([request] if deployment.joint else [request.model_copy(update={"questions": [q]}) for q in request.questions]
                ) if many else [request]
        parts, attempts = [], 1
        try:
            with httpx.Client(transport=self._transport) as client:
                for r, sub in zip(requests, subs):
                    response, n = send_with_retries(client, r, limiter, attempts=MAX_ATTEMPTS, sleep=lambda s: time.sleep(s))
                    attempts = max(attempts, n)
                    parts.append(wire.parse_many(deployment, [response], sub) if many else wire.parse(deployment, response, sub))
        except Exception as error:
            if parts:
                error.partial_usage = merge(parts)[1] if many else parts[0][1]
            raise
        if many:
            return (*merge(parts), attempts)
        return (*parts[0], attempts)


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
