from __future__ import annotations

import httpx

from ..types import Deployment, ExtractionRequest, ExtractionResponse, Provider, Span, Usage
from . import WireClient, base, load, post, unparseable
from .systemone import parse_usage


class ClairicLocalClient(WireClient):
    """The local Clairic service (`weave_enrichers.clairic_service`): `POST /extract` with {text, fields},
    answering {fields: {name: [span]}, usage}. Same shapes as our ExtractionRequest/Response."""

    def build(self, deployment: Deployment, provider: Provider, request: ExtractionRequest, credential: str | None) -> httpx.Request:
        return post(f"{base(provider)}/extract", {"text": request.text, "fields": request.fields}, credential)

    def parse(self, deployment: Deployment, response: httpx.Response, request=None) -> tuple[ExtractionResponse, Usage]:
        data = load(response)
        try:
            fields = {name: [Span(**s) for s in spans] for name, spans in data["fields"].items()}
            return ExtractionResponse(fields=fields), parse_usage(data)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise unparseable(response, error) from error
