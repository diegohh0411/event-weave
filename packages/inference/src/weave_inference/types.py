from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from weave_core.adapters import AdapterFailure
from weave_core.stages import Capability

ProviderKind = Literal['llamacpp', 'typesafe', 'openrouter', 'cloudflare', 'openai', 'stub']


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RateLimit(_Strict):
    concurrency: int = 4
    rps: float | None = None


class Limits(_Strict):
    context_tokens: int | None = None
    max_choice_options: int | None = None
    max_request_bytes: int | None = None


class Price(_Strict):
    input_per_mtok: Decimal = Decimal(0)
    output_per_mtok: Decimal = Decimal(0)


class Model(_Strict):
    id: str
    capability: Capability


class Provider(_Strict):
    id: str
    kind: ProviderKind
    base_url: str
    credential_env: str | None = None
    rate: RateLimit = Field(default_factory=RateLimit)


class Deployment(_Strict):
    id: str
    model: str
    provider: str
    settings: dict = Field(default_factory=dict)
    quantization: str | None = None
    joint: bool = False
    limits: Limits = Field(default_factory=Limits)
    price: Price = Field(default_factory=Price)
    rate: RateLimit | None = None
    local: bool = False


class Usage(_Strict):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reported_cost: Decimal | None = None


class InferenceError(AdapterFailure):
    pass


class LimitExceeded(InferenceError):
    def __init__(self, limit: str, value: int, maximum: int, estimated: bool = False):
        self.limit, self.value, self.maximum = limit, value, maximum
        shown = f"estimated {value}" if estimated else str(value)
        super().__init__(f"{limit} limit exceeded: {shown} > {maximum}", retryable=False)


class NotReady(InferenceError):
    def __init__(self, reason: str):
        super().__init__(f"not ready: {reason}", retryable=False)


class RateLimited(InferenceError):
    def __init__(self, retry_after: float | None):
        self.retry_after = retry_after
        super().__init__(f"rate limited (retry after {retry_after}s)")


class ProviderError(InferenceError):
    def __init__(self, status: int, body: str):
        self.status, self.body = status, body
        super().__init__(f"provider error {status}: {body[:200]}", retryable=status >= 500)
