from __future__ import annotations

import httpx

from ..types import (Answer, Choice, Deployment, Noul, Provider, ProviderError, Score, SystemOneRequest,
                     SystemOneResponse, Usage)
from . import WireClient, base, cost_of, load, model_name, post, unparseable

MAX_SCORE_LEVELS = 10


def question_body(q: Choice | Score | Noul) -> dict:
    if isinstance(q, Choice):
        return {"type": "choice", "instructions": q.prompt, "criteria": {o: o for o in q.options}}
    if isinstance(q, Score):
        levels = [str(i) for i in range(int(q.low), int(q.high) + 1)]
        if not 1 <= len(levels) <= MAX_SCORE_LEVELS:
            raise ValueError(f"score {q.id}: {len(levels)} levels, System One allows 1..{MAX_SCORE_LEVELS}")
        return {"type": "score", "instructions": q.prompt, "criteria": levels}
    return {"type": "noul", "instructions": q.prompt}


def request_body(deployment: Deployment, request: SystemOneRequest) -> dict:
    return {"model": model_name(deployment), "state": request.state,
            "questions": {q.id: question_body(q) for q in request.questions}}


def parse_usage(data: dict) -> Usage:
    u = data.get("usage") or {}
    return Usage(input_tokens=u.get("input_tokens", u.get("prompt_tokens")),
                 output_tokens=u.get("output_tokens", u.get("completion_tokens")),
                 cached_tokens=u.get("cached_tokens"),
                 reported_cost=cost_of(u))


def parse_answers(answers: dict, request: SystemOneRequest | None) -> SystemOneResponse:
    lows = {q.id: q.low for q in request.questions if isinstance(q, Score)} if request else {}
    out = []
    for qid, a in answers.items():
        kind = a["type"]
        value = a[kind]
        if kind == "score":
            value = float(value) + lows.get(qid, 0)
        elif kind == "noul":
            value = float(value)
        probs = a.get("probabilities")
        if probs is not None:
            probs = {str(k): float(v) for k, v in probs.items()}
        out.append(Answer(question_id=qid, value=value, probabilities=probs))
    return SystemOneResponse(answers=out)


def merge(parts: list[tuple[SystemOneResponse, Usage]]) -> tuple[SystemOneResponse, Usage]:
    def total(values):
        values = [v for v in values if v is not None]
        return sum(values) if values else None
    usage = Usage(input_tokens=total(u.input_tokens for _, u in parts), output_tokens=total(u.output_tokens for _, u in parts),
                  cached_tokens=total(u.cached_tokens for _, u in parts), reported_cost=total(u.reported_cost for _, u in parts))
    return SystemOneResponse(answers=[a for r, _ in parts for a in r.answers]), usage


class SystemOneClient(WireClient):
    """TypeSafe and llama.cpp `POST /v1/systemone` (doc-backed: docs.typesafe.ai/api/system-one)."""

    path = "/v1/systemone"

    def build(self, deployment: Deployment, provider: Provider, request: SystemOneRequest, credential: str | None) -> httpx.Request:
        return post(base(provider) + self.path, request_body(deployment, request), credential)

    def build_many(self, deployment: Deployment, provider: Provider, request: SystemOneRequest, credential: str | None) -> list[httpx.Request]:
        if deployment.joint:
            return [self.build(deployment, provider, request, credential)]
        return [self.build(deployment, provider, request.model_copy(update={"questions": [q]}), credential) for q in request.questions]

    def parse(self, deployment: Deployment, response: httpx.Response, request: SystemOneRequest | None = None) -> tuple[SystemOneResponse, Usage]:
        data = load(response)
        try:
            return parse_answers(data["answers"], request), parse_usage(data)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise unparseable(response, error) from error

    def parse_many(self, deployment: Deployment, responses: list[httpx.Response], request: SystemOneRequest | None = None) -> tuple[SystemOneResponse, Usage]:
        if deployment.joint:
            if len(responses) != 1:
                raise ProviderError(0, "joint deployment expects exactly one response")
            questions = [request]
        elif request is None:
            questions = [None] * len(responses)
        else:
            questions = [request.model_copy(update={"questions": [q]}) for q in request.questions]
        if len(questions) != len(responses):
            raise ProviderError(0, "response count does not match question count")
        return merge([self.parse(deployment, r, q) for r, q in zip(responses, questions)])
