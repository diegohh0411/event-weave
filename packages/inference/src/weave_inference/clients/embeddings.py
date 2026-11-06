from __future__ import annotations

import httpx

from ..types import Deployment, EmbeddingsRequest, EmbeddingsResponse, Provider, Usage
from . import WireClient, base, parse_cost, load, model_name, post, unparseable


class EmbeddingsClient(WireClient):
    """OpenAI-compatible `POST {base}/embeddings`."""

    def build(self, deployment: Deployment, provider: Provider, request: EmbeddingsRequest, credential: str | None) -> httpx.Request:
        return post(base(provider) + "/embeddings", {"model": model_name(deployment), "input": request.texts}, credential)

    def parse(self, deployment: Deployment, response: httpx.Response, request=None) -> tuple[EmbeddingsResponse, Usage]:
        data = load(response)
        try:
            rows = sorted(data["data"], key=lambda r: r["index"] if isinstance(r, dict) and "index" in r else 0)
            vectors = [[float(x) for x in r["embedding"]] for r in rows]
            u = data.get("usage") or {}
            usage = Usage(input_tokens=u.get("prompt_tokens"), reported_cost=parse_cost(u))
            return EmbeddingsResponse(vectors=vectors), usage
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise unparseable(response, error) from error
