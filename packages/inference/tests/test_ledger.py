from decimal import Decimal

import httpx
import pytest
import sqlalchemy as sa

from weave_core.schema import ledger
from weave_inference.http import RateLimiter, send_with_retries
from weave_inference.ledger import LedgerEntry, cost_of, record, request_hash
from weave_inference.types import Deployment, Price, ProviderError, RateLimit, Usage


def dep(**kw):
    return Deployment(id="d", model="m", provider="p", price=Price(input_per_mtok=Decimal(1), output_per_mtok=Decimal(1)), **kw)


def test_cost_without_usage_price_is_estimated():
    assert cost_of(dep(), Usage(input_tokens=100, output_tokens=200), None) == (Decimal("0.0003"), True)


def test_reported_cost_wins():
    assert cost_of(dep(), Usage(input_tokens=1, output_tokens=1), Decimal("0.5")) == (Decimal("0.5"), False)


def test_200_with_no_usage_is_estimated_never_null():
    cost, estimated = cost_of(dep(), Usage(input_tokens=None, output_tokens=None), None)
    assert cost == Decimal(0) and estimated is True


def test_local_deployment_costs_zero():
    assert cost_of(dep(local=True), Usage(input_tokens=100, output_tokens=200), None) == (Decimal(0), False)


def test_request_hash_is_canonical():
    assert request_hash({"b": 1, "a": [1, 2]}) == request_hash({"a": [1, 2], "b": 1})
    assert len(request_hash({})) == 64


def entry(**kw):
    base = dict(run_id=None, stage="s", adapter="a", deployment="d", request_hash="h", input_tokens=1, output_tokens=2,
                cached_tokens=None, cost=Decimal("0.0003"), estimated=True, latency_ms=12, attempts=1, status="ok", error=None)
    return LedgerEntry(**{**base, **kw})


def test_record_writes_one_row(engine):
    record(engine, entry())
    with engine.connect() as c:
        rows = c.execute(sa.select(ledger)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["cost"] == Decimal("0.0003") and rows[0]["estimated"] is True and rows[0]["status"] == "ok"


def test_call_failing_after_retries_records_one_error_row(engine):
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    try:
        send_with_retries(c, httpx.Request("GET", "http://x"), RateLimiter(RateLimit()), sleep=lambda s: None)
    except ProviderError as e:
        record(engine, entry(status="error", error=str(e), attempts=3, input_tokens=None, output_tokens=None, cost=Decimal(0), estimated=False))
    with engine.connect() as conn:
        rows = conn.execute(sa.select(ledger)).mappings().all()
    assert len(rows) == 1 and rows[0]["status"] == "error" and rows[0]["attempts"] == 3
