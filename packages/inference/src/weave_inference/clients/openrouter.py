from __future__ import annotations

from .systemone import SystemOneClient


class OpenRouterDecisionsClient(SystemOneClient):
    """OpenRouter decisions. ASSUMED: same body/response as System One at `{base}/decisions`
    (vendor docs unreachable); usage may carry `cost`, which becomes reported_cost."""

    path = "/decisions"
