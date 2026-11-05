from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..types import Deployment, Provider, ProviderError, Usage


class WireClient:
    """Builds and parses wire messages only; sending, ledger and limits live in the gateway."""

    def build(self, deployment: Deployment, provider: Provider, request: Any, credential: str | None) -> httpx.Request:
        raise NotImplementedError

    def parse(self, deployment: Deployment, response: httpx.Response, request: Any = None) -> tuple[Any, Usage]:
        raise NotImplementedError


def base(provider: Provider) -> str:
    return provider.base_url.rstrip("/")


def model_name(deployment: Deployment) -> str:
    return deployment.settings.get("model_name", deployment.model)


def post(url: str, body: dict, credential: str | None, headers: dict | None = None) -> httpx.Request:
    h = dict(headers or {})
    if credential:
        h["Authorization"] = f"Bearer {credential}"
    return httpx.Request("POST", url, json=body, headers=h)


def unparseable(response: httpx.Response, error: Exception) -> ProviderError:
    return ProviderError(response.status_code, f"unparseable response ({type(error).__name__}: {error}): {response.text[:200]}")


def load(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise unparseable(response, error) from error
    if not isinstance(data, dict):
        raise unparseable(response, TypeError("expected a JSON object"))
    return data


def parse_cost(usage: dict) -> Decimal | None:
    """Reported cost as Decimal; a malformed value is a ValueError (callers map it to ProviderError)."""
    cost = usage.get("cost")
    if cost is None:
        return None
    try:
        return Decimal(str(cost))
    except InvalidOperation as error:
        raise ValueError(f"bad cost {cost!r}") from error
