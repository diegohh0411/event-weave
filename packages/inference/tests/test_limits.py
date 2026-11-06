from dataclasses import dataclass, field

import pytest

from weave_inference.limits import check_limits
from weave_inference.types import Deployment, LimitExceeded, Limits, NotReady, InferenceError
from weave_core.adapters import AdapterFailure


def dep(**limits):
    return Deployment(id="d", model="m", provider="p", limits=Limits(**limits))


@dataclass
class Req:
    choice_option_counts: list = field(default_factory=list)
    text_for_context: str = ""
    token_count: int | None = None
    body_bytes: int | None = None


def test_choice_with_too_many_options_refused_locally():
    with pytest.raises(LimitExceeded) as e:
        check_limits(dep(max_choice_options=8), Req(choice_option_counts=[3, 9]))
    assert (e.value.limit, e.value.value, e.value.maximum) == ("choice_options", 9, 8)
    assert e.value.retryable is False


def test_context_estimated_from_bytes():
    with pytest.raises(LimitExceeded) as e:
        check_limits(dep(context_tokens=10), Req(text_for_context="x" * 44))
    assert (e.value.limit, e.value.value, e.value.maximum) == ("context_tokens", 11, 10)
    assert "estimated" in str(e.value)


def test_explicit_token_count_wins_and_is_not_estimated():
    check_limits(dep(context_tokens=10), Req(text_for_context="x" * 400, token_count=10))
    with pytest.raises(LimitExceeded) as e:
        check_limits(dep(context_tokens=10), Req(token_count=11))
    assert "estimated" not in str(e.value)


def test_request_bytes():
    with pytest.raises(LimitExceeded) as e:
        check_limits(dep(max_request_bytes=100), Req(body_bytes=101))
    assert e.value.limit == "request_bytes"


def test_within_limits_and_no_limits_pass():
    check_limits(dep(context_tokens=10, max_choice_options=8, max_request_bytes=5), Req(choice_option_counts=[8], text_for_context="abcd", body_bytes=5))
    check_limits(dep(), object())


def test_error_hierarchy():
    assert issubclass(InferenceError, AdapterFailure)
    assert NotReady("x").retryable is False


def test_context_estimate_includes_options_template_args_and_fields():
    from weave_inference.types import ChatRequest, Choice, ExtractionRequest, SystemOneRequest
    d = dep(context_tokens=50)
    big = "x" * 400
    for req in (SystemOneRequest(state="s", questions=[Choice(id="c", prompt="p", options=[big, "b"])]),
                ChatRequest(messages=[{"role": "user", "content": "hi"}], template_args={"template": big}),
                ExtractionRequest(text="t", fields={"name": big})):
        with pytest.raises(LimitExceeded) as e:
            check_limits(d, req)
        assert e.value.limit == "context_tokens"


def test_extraction_field_limit():
    from weave_inference.types import ExtractionRequest
    check_limits(dep(max_fields=2), ExtractionRequest(text="t", fields={"a": 1, "b": 2}))
    with pytest.raises(LimitExceeded) as e:
        check_limits(dep(max_fields=2), ExtractionRequest(text="t", fields={"a": 1, "b": 2, "c": 3}))
    assert (e.value.limit, e.value.value, e.value.maximum) == ("fields", 3, 2)
    assert e.value.retryable is False
