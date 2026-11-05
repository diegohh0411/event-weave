from __future__ import annotations

from decimal import Decimal

import httpx

from ..types import Deployment, EmbeddingsRequest, EmbeddingsResponse, Provider, Usage
from . import WireClient, base, load, model_name, post, unparseable


class EmbeddingsClient(WireClient):
    """OpenAI-compatible `POST {base}/embeddings`."""

    def build(self, deployment: Deployment, provider: Provider, request: EmbeddingsRequest, credential: str | None) -> httpx.Request:
        return post(base(provider) + "/embeddings", {"model": model_name(deployment), "input": request.texts}, credential)

    def parse(self, deployment: Deployment, response: httpx.Response, request=None) -> tuple[EmbeddingsResponse, Usage]:
        data = load(response)
        try:
            rows = sorted(data["data"], key=lambda r: r.get("index", 0))
            vectors = [[float(x) for x in r["embedding"]] for r in rows]
            u = data.get("usage") or {}
            cost = u.get("cost")
            usage = Usage(input_tokens=u.get("prompt_tokens"), reported_cost=Decimal(str(cost)) if cost is not None else None)
            return EmbeddingsResponse(vectors=vectors), usage
        except (KeyError, TypeError, ValueError) as error:
            raise unparseable(response, error) from error
