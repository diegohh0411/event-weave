from __future__ import annotations

from decimal import Decimal

import httpx

from ..types import ChatRequest, ChatResponse, Deployment, Provider, Usage
from . import WireClient, base, load, model_name, post, unparseable


class ChatClient(WireClient):
    """OpenAI-compatible `POST {base}/chat/completions`; template_args -> chat_template_kwargs (NuExtract3)."""

    def build(self, deployment: Deployment, provider: Provider, request: ChatRequest, credential: str | None) -> httpx.Request:
        body: dict = {"model": model_name(deployment), "messages": request.messages}
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.template_args:
            body["chat_template_kwargs"] = request.template_args
        return post(base(provider) + "/chat/completions", body, credential)

    def parse(self, deployment: Deployment, response: httpx.Response, request=None) -> tuple[ChatResponse, Usage]:
        data = load(response)
        try:
            text = data["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise TypeError("content is not a string")
            u = data.get("usage") or {}
            cost = u.get("cost")
            usage = Usage(input_tokens=u.get("prompt_tokens"), output_tokens=u.get("completion_tokens"),
                          cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                          reported_cost=Decimal(str(cost)) if cost is not None else None)
            return ChatResponse(text=text), usage
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise unparseable(response, error) from error
