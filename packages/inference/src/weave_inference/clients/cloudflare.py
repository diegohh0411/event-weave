from __future__ import annotations

import os

import httpx

from ..types import (Deployment, ExtractionRequest, ExtractionResponse, Provider, Span, SystemOneRequest,
                     SystemOneResponse, Usage)
from . import WireClient, base, load, model_name, post, unparseable
from .systemone import parse_answers, parse_usage, question_body


class CloudflareClient(WireClient):
    """`POST /accounts/<id>/ai/run/<model>`, envelope `{result, success}`. Body/result shapes are ASSUMED
    (docs give no schema for Clef/Clairic): system_one sends {state, questions}; extraction sends {text, fields}."""

    def build(self, deployment: Deployment, provider: Provider, request: SystemOneRequest | ExtractionRequest, credential: str | None) -> httpx.Request:
        account = deployment.settings.get("account_id") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        if isinstance(request, SystemOneRequest):
            body = {"state": request.state, "questions": {q.id: question_body(q) for q in request.questions}}
        else:
            body = {"text": request.text, "fields": request.fields}
        return post(f"{base(provider)}/accounts/{account}/ai/run/{model_name(deployment)}", body, credential)

    def parse(self, deployment: Deployment, response: httpx.Response, request=None) -> tuple[SystemOneResponse | ExtractionResponse, Usage]:
        data = load(response)
        try:
            if data.get("success") is False:
                raise ValueError(f"success=false: {data.get('errors')}")
            result = data["result"]
            usage = parse_usage(result)
            if "answers" in result:
                return parse_answers(result["answers"], request if isinstance(request, SystemOneRequest) else None), usage
            fields = {name: [Span(**s) for s in spans] for name, spans in result["fields"].items()}
            return ExtractionResponse(fields=fields), usage
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise unparseable(response, error) from error
