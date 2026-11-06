from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from weave_core.adapters import AdapterContext
from weave_core.schema import ledger, runs
from weave_core.stages import Capability, Stage

from weave_inference.catalog import Catalog
from weave_inference.clients.stub import clear_stub_scripts
from weave_inference.gateway import Inference
from weave_inference.probe import Readiness
from weave_inference.types import (ChatRequest, Choice, Deployment, EmbeddingsRequest, LimitExceeded, Limits, Model,
                                   NotReady, Noul, Price, Provider, ProviderError, RateLimit,
                                   SystemOneRequest)

REQ_S1 = SystemOneRequest(state="x", questions=[Choice(id="c", prompt="p", options=["a", "b"])])
REQ_EMB = EmbeddingsRequest(texts=["hi"])


def catalog_of(deployments, providers, models):
    cat = Catalog(models={m.id: m for m in models}, providers={p.id: p for p in providers},
                  deployments={d.id: d for d in deployments})
    cat.readiness = {d.id: Readiness(ready=True) for d in deployments}
    return cat


STUB = Provider(id="stub", kind="stub", base_url="stub://")
MODELS = [Model(id="m-s1", capability=Capability.system_one), Model(id="m-emb", capability=Capability.embeddings),
          Model(id="m-llm", capability=Capability.llm)]


@pytest.fixture
def run_id():
    return uuid4()


def make(engine, run_id, deployments, providers=(STUB,), mapping=None):
    # ledger.run_id has an FK to runs; insert a minimal run row
    cols = {c.name: c for c in runs.columns}
    with engine.begin() as conn:
        values = {}
        for name, c in cols.items():
            if c.nullable or c.default is not None or c.server_default is not None:
                continue
            values[name] = _fill(c)
        values["id"] = run_id
        conn.execute(runs.insert().values(**values))
    Inference.configure(catalog_of(deployments, providers, MODELS), engine)
    return Inference.for_context(AdapterContext(run_id, 1, mapping, Stage.enrich, "fake-enricher"))


def _fill(c):
    import datetime
    t = c.type
    name = type(t).__name__
    if name in ("JSONB", "JSON"):
        return {}
    if name.startswith("DateTime"):
        return datetime.datetime.now(datetime.timezone.utc)
    if name in ("Integer", "BigInteger"):
        return 1
    return "paper" if c.name == "track" else ("created" if c.name == "state" else "x")


def rows(engine):
    with engine.connect() as conn:
        return list(conn.execute(sa.select(ledger.c.stage, ledger.c.adapter, ledger.c.deployment, ledger.c.status,
                                           ledger.c.attempts, ledger.c.cost, ledger.c.estimated).order_by(ledger.c.id)))


def dep(id, model, **kw):
    return Deployment(id=id, model=model, provider="stub", **kw)


def test_every_call_writes_exactly_one_ledger_row(engine, run_id):
    inf = make(engine, run_id, [dep("stub-s1", "m-s1"), dep("stub-emb", "m-emb")],
               mapping={Capability.system_one: "stub-s1", Capability.embeddings: "stub-emb"})
    inf.system_one(REQ_S1)
    inf.embed(REQ_EMB)
    assert [r[:4] for r in rows(engine)] == [("enrich", "fake-enricher", "stub-s1", "ok"),
                                              ("enrich", "fake-enricher", "stub-emb", "ok")]


def test_refused_call_writes_one_row(engine, run_id):
    inf = make(engine, run_id, [dep("stub-s1", "m-s1", limits=Limits(max_choice_options=1))],
               mapping={Capability.system_one: "stub-s1"})
    with pytest.raises(LimitExceeded):
        inf.system_one(REQ_S1)
    assert [(r.status, r.attempts) for r in rows(engine)] == [("refused", 0)]


def test_failed_call_writes_one_error_row_with_three_attempts(engine, run_id, monkeypatch):
    monkeypatch.setattr("weave_inference.gateway.time.sleep", lambda s: None)
    p = Provider(id="or", kind="openai", base_url="http://x/v1", credential_env="X_KEY")
    monkeypatch.setenv("X_KEY", "k")
    d = Deployment(id="chat", model="m-llm", provider="or")
    inf = make(engine, run_id, [d], providers=[p], mapping={Capability.llm: "chat"})
    Inference.configure(Inference._catalog, engine, httpx.MockTransport(lambda r: httpx.Response(503, text="down")))
    with pytest.raises(ProviderError):
        inf.chat(ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert [(r.status, r.attempts) for r in rows(engine)] == [("error", 3)]


def test_missing_credential_is_not_ready_and_row_written(engine, run_id, monkeypatch):
    monkeypatch.delenv("NOPE_KEY", raising=False)
    p = Provider(id="or", kind="openai", base_url="http://x/v1", credential_env="NOPE_KEY")
    inf = make(engine, run_id, [Deployment(id="chat", model="m-llm", provider="or")], providers=[p],
               mapping={Capability.llm: "chat"})
    with pytest.raises(NotReady, match="credential missing: NOPE_KEY"):
        inf.chat(ChatRequest(messages=[]))
    assert [r.status for r in rows(engine)] == ["refused"]


def test_two_deployments_same_model_have_separate_rows_and_costs(engine, run_id, monkeypatch):
    monkeypatch.setenv("X_KEY", "k")
    p = Provider(id="o", kind="openai", base_url="http://x/v1", credential_env="X_KEY")
    a = Deployment(id="a", model="m-llm", provider="o", price=Price(input_per_mtok=Decimal(1_000_000)))
    b = Deployment(id="b", model="m-llm", provider="o", price=Price(input_per_mtok=Decimal(2_000_000)))
    inf_a = make(engine, run_id, [a, b], providers=[p], mapping={Capability.llm: "a"})
    body = {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
    Inference.configure(Inference._catalog, engine, httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    inf_a.chat(ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    Inference.for_context(AdapterContext(run_id, 1, {Capability.llm: "b"}, Stage.enrich, "fake-enricher")).chat(
        ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert [(r.deployment, r.cost) for r in rows(engine)] == [("a", Decimal("3")), ("b", Decimal("6"))]


def test_usage_missing_marks_cost_estimated(engine, run_id, monkeypatch):
    monkeypatch.setenv("X_KEY", "k")
    p = Provider(id="o", kind="openai", base_url="http://x/v1", credential_env="X_KEY")
    d = Deployment(id="chat", model="m-llm", provider="o", price=Price(input_per_mtok=Decimal(1)))
    inf = make(engine, run_id, [d], providers=[p], mapping={Capability.llm: "chat"})
    body = {"choices": [{"message": {"content": "hi"}}]}
    Inference.configure(Inference._catalog, engine, httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    res = inf.chat(ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert res.value.text == "hi" and res.deployment == "chat"
    (row,) = rows(engine)
    assert (row.status, row.estimated, row.cost, row.attempts) == ("ok", True, Decimal(0), 1)


def test_no_deployment_for_capability_raises_not_ready(engine, run_id):
    inf = make(engine, run_id, [dep("stub-s1", "m-s1")], mapping={})
    with pytest.raises(NotReady):
        inf.system_one(REQ_S1)


def test_not_ready_deployment_is_refused(engine, run_id):
    inf = make(engine, run_id, [dep("stub-s1", "m-s1")], mapping={Capability.system_one: "stub-s1"})
    Inference._catalog.readiness["stub-s1"] = Readiness(ready=False, reason="unreachable")
    with pytest.raises(NotReady, match="not ready: unreachable"):
        inf.system_one(REQ_S1)
    assert [r.status for r in rows(engine)] == ["refused"]


def _chat_inf(engine, run_id, monkeypatch, handler):
    monkeypatch.setattr("weave_inference.gateway.time.sleep", lambda s: None)
    p = Provider(id="or", kind="openai", base_url="http://x/v1", credential_env="X_KEY")
    monkeypatch.setenv("X_KEY", "k")
    inf = make(engine, run_id, [Deployment(id="chat", model="m-llm", provider="or")], providers=[p], mapping={Capability.llm: "chat"})
    Inference.configure(Inference._catalog, engine, httpx.MockTransport(handler))
    return inf


def test_attempts_recorded_from_real_count(engine, run_id, monkeypatch):
    calls = iter([503, 400])
    inf = _chat_inf(engine, run_id, monkeypatch, lambda r: httpx.Response(next(calls), text="x"))
    with pytest.raises(ProviderError):
        inf.chat(ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert [(r.status, r.attempts) for r in rows(engine)] == [("error", 2)]


def test_untyped_parse_failure_is_wrapped_in_provider_error(engine, run_id, monkeypatch):
    inf = _chat_inf(engine, run_id, monkeypatch, lambda r: httpx.Response(200, json={"unexpected": 1}))
    with pytest.raises(ProviderError):
        inf.chat(ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert [(r.status) for r in rows(engine)] == ["error"]


def _s1_inf(engine, run_id, monkeypatch, handler, *, provider=None, dep_kw=None):
    monkeypatch.setattr("weave_inference.gateway.time.sleep", lambda s: None)
    monkeypatch.setenv("X_KEY", "k")
    p = provider or Provider(id="ts", kind="typesafe", base_url="http://x", credential_env="X_KEY")
    d = Deployment(id="s1", model="m-s1", provider=p.id, **(dep_kw or {}))
    inf = make(engine, run_id, [d], providers=[p], mapping={Capability.system_one: "s1"})
    Inference.configure(Inference._catalog, engine, httpx.MockTransport(handler))
    return inf


def test_partial_failure_keeps_accumulated_usage_and_cost(engine, run_id, monkeypatch):
    calls = iter([
        httpx.Response(200, json={"answers": {"a": {"type": "noul", "noul": 0.5}},
                                  "usage": {"input_tokens": 7, "output_tokens": 2, "cost": 0.25}}),
        httpx.Response(400, text="bad"),
    ])
    inf = _s1_inf(engine, run_id, monkeypatch, lambda r: next(calls))
    req = SystemOneRequest(state="x", questions=[Noul(id="a", prompt="p"), Noul(id="b", prompt="q")])
    with pytest.raises(ProviderError):
        inf.system_one(req)
    with engine.connect() as conn:
        (row,) = conn.execute(sa.select(ledger.c.status, ledger.c.cost, ledger.c.input_tokens, ledger.c.output_tokens))
    assert (row.status, row.cost, row.input_tokens, row.output_tokens) == ("error", Decimal("0.25"), 7, 2)


def test_provider_rate_is_inherited_when_deployment_has_none(engine, run_id, monkeypatch):
    from weave_inference import http
    p = Provider(id="ts", kind="typesafe", base_url="http://x", credential_env="X_KEY", rate=RateLimit(concurrency=7))
    body = {"answers": {"c": {"type": "choice", "choice": "a"}}}
    inf = _s1_inf(engine, run_id, monkeypatch, lambda r: httpx.Response(200, json=body), provider=p)
    http._limiters.pop("s1", None)
    inf.system_one(REQ_S1)
    assert http._limiters["s1"]._sem._initial_value == 7
