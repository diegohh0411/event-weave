from __future__ import annotations

from .types import Deployment, LimitExceeded


def check_limits(deployment: Deployment, request: object) -> None:
    """Refuse over-limit requests locally. Reads optional attributes via getattr:
    choice_option_counts, text_for_context, token_count, body_bytes."""
    limits = deployment.limits
    if limits.max_choice_options is not None:
        for count in getattr(request, "choice_option_counts", None) or ():
            if count > limits.max_choice_options:
                raise LimitExceeded("choice_options", count, limits.max_choice_options)
    if limits.context_tokens is not None:
        tokens = getattr(request, "token_count", None)
        estimated = tokens is None
        if estimated:
            tokens = len((getattr(request, "text_for_context", "") or "").encode()) // 4
        if tokens > limits.context_tokens:
            raise LimitExceeded("context_tokens", tokens, limits.context_tokens, estimated=estimated)
    body = getattr(request, "body_bytes", None)
    if limits.max_request_bytes is not None and body is not None and body > limits.max_request_bytes:
        raise LimitExceeded("request_bytes", body, limits.max_request_bytes)
