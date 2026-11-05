from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import yaml
from weave_core.stages import Capability

from ..ledger import request_hash
from ..types import (Answer, Choice, ChatRequest, ChatResponse, EmbeddingsRequest, EmbeddingsResponse,
                     ExtractionRequest, ExtractionResponse, Noul, Score, SystemOneRequest, SystemOneResponse, Usage)

# Keys: (capability, request_hash) -> full response (dict or model), or a question id -> answer value
# (or full Answer dict) for system_one. Files may spell the tuple key "capability:hash".
StubScript = dict

_REGISTRY: dict[str, StubScript] = {}
_RESPONSES = {Capability.system_one: SystemOneResponse, Capability.llm: ChatResponse,
              Capability.embeddings: EmbeddingsResponse, Capability.extraction: ExtractionResponse}
DIM = 8


def set_stub_script(provider_id: str, script: StubScript) -> None:
    _REGISTRY[provider_id] = script


class StubClient:
    def __init__(self, script: StubScript | None = None):
        self.script = script or {}

    @classmethod
    def for_provider(cls, provider_id: str, settings: dict | None = None) -> "StubClient":
        if provider_id in _REGISTRY:
            return cls(_REGISTRY[provider_id])
        path = (settings or {}).get("script")
        return cls.from_path(path) if path else cls()

    @classmethod
    def from_path(cls, path: str) -> "StubClient":
        return cls(yaml.safe_load(Path(path).read_text()) or {})

    def _lookup(self, capability: Capability, digest: str):
        for key in ((capability.value, digest), f"{capability.value}:{digest}", (capability, digest)):
            if key in self.script:
                return self.script[key]
        return None

    def call(self, capability: Capability, request):
        digest = request_hash(request.model_dump(mode="json"))
        cls = _RESPONSES[capability]
        scripted = self._lookup(capability, digest)
        if scripted is not None:
            response = scripted if isinstance(scripted, cls) else cls.model_validate(scripted)
        elif capability == Capability.system_one:
            response = self._system_one(request, digest)
        elif capability == Capability.llm:
            response = ChatResponse(text="stub")
        elif capability == Capability.embeddings:
            response = EmbeddingsResponse(vectors=[_vector(t) for t in request.texts])
        else:
            response = ExtractionResponse(fields={})
        return response, self._usage(request, response)

    def _system_one(self, request: SystemOneRequest, digest: str) -> SystemOneResponse:
        answers = []
        for q in request.questions:
            if q.id in self.script:
                v = self.script[q.id]
                answers.append(Answer.model_validate(v) if isinstance(v, dict) else Answer(question_id=q.id, value=v))
            elif isinstance(q, Choice):
                pick = q.options[int(digest, 16) % len(q.options)]
                answers.append(Answer(question_id=q.id, value=pick,
                                      probabilities={o: 1.0 if o == pick else 0.0 for o in q.options}))
            elif isinstance(q, Score):
                answers.append(Answer(question_id=q.id, value=(q.low + q.high) / 2))
            # Noul: no default answer (Answer.value cannot be None)
        return SystemOneResponse(answers=answers)

    def _usage(self, request, response) -> Usage:
        out = json.dumps(response.model_dump(mode="json"))
        return Usage(input_tokens=len(request.text_for_context) // 4, output_tokens=len(out) // 4,
                     reported_cost=0)


def _vector(text: str) -> list[float]:
    raw = hashlib.sha256(text.encode()).digest()[:DIM]
    v = [b - 127.5 for b in raw]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]
