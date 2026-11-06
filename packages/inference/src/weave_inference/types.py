from __future__ import annotations

from decimal import Decimal
from typing import Generic, Literal, TypeVar

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
    max_choice_options: int | None = Field(default=None, ge=1)
    max_request_bytes: int | None = None
    max_fields: int | None = Field(default=None, ge=1)


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


def remote_model_id(deployment: 'Deployment') -> str:
    """The model id the provider knows: used by both the readiness probe and the wire clients."""
    s = deployment.settings
    return s.get('remote_model') or s.get('model_name') or deployment.model


class Usage(_Strict):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reported_cost: Decimal | None = None


T = TypeVar('T')


class Result(_Strict, Generic[T]):
    value: T
    usage: Usage
    deployment: str


class Choice(_Strict):
    id: str
    prompt: str
    options: list[str]


class Score(_Strict):
    id: str
    prompt: str
    low: float
    high: float


class Noul(_Strict):
    id: str
    prompt: str


Question = Choice | Score | Noul


def _json_bytes(payload) -> int:
    import json
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode())


class SystemOneRequest(_Strict):
    state: str | dict
    questions: list[Choice | Score | Noul]

    @property
    def choice_option_counts(self) -> list[int]:
        return [len(q.options) for q in self.questions if isinstance(q, Choice)]

    @property
    def text_for_context(self) -> str:
        import json
        state = self.state if isinstance(self.state, str) else json.dumps(self.state)
        parts = [state]
        for q in self.questions:
            parts.append(q.prompt)
            if isinstance(q, Choice):
                parts.extend(q.options)
        return "\n".join(parts)

    @property
    def body_bytes(self) -> int:
        return _json_bytes(self.model_dump())


class Answer(_Strict):
    question_id: str
    value: str | float | None
    probabilities: dict[str, float] | None = None


class SystemOneResponse(_Strict):
    answers: list[Answer]


class ChatRequest(_Strict):
    messages: list[dict]
    template_args: dict = Field(default_factory=dict)
    max_tokens: int | None = None

    @property
    def text_for_context(self) -> str:
        import json
        return "\n".join([str(m.get("content", "")) for m in self.messages]
                         + ([json.dumps(self.template_args, ensure_ascii=False, default=str)] if self.template_args else []))

    @property
    def body_bytes(self) -> int:
        return _json_bytes(self.model_dump())


class ChatResponse(_Strict):
    text: str


class EmbeddingsRequest(_Strict):
    texts: list[str]

    @property
    def text_for_context(self) -> str:
        return "\n".join(self.texts)

    @property
    def body_bytes(self) -> int:
        return _json_bytes(self.model_dump())


class EmbeddingsResponse(_Strict):
    vectors: list[list[float]]


class Span(_Strict):
    text: str
    start: int
    end: int
    confidence: float | None = None


class ExtractionRequest(_Strict):
    text: str
    fields: dict

    @property
    def text_for_context(self) -> str:
        import json
        return self.text + ("\n" + json.dumps(self.fields, ensure_ascii=False, default=str) if self.fields else "")

    @property
    def body_bytes(self) -> int:
        return _json_bytes(self.model_dump())


class ExtractionResponse(_Strict):
    fields: dict[str, list[Span]]


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
