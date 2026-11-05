from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
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
