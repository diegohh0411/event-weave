import json
import uuid
from pathlib import Path

import httpx
import pytest
from weave_core.stages import Capability
from weave_inference.catalog import load_catalog
from weave_inference.probe import probe

FIX = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[3]


def fixture(kind, name="models.json", mutate=None):
    data = json.loads((FIX / kind / name).read_text())
    return mutate(data) if mutate else data


def serve(routes):
    """routes: {(host, path): json | Exception}; unknown routes 404."""
    def handler(request):
        hit = routes.get((request.url.host, request.url.path))
        if isinstance(hit, Exception):
            raise hit
        if hit is None:
            return httpx.Response(404)
        return httpx.Response(200, json=hit)
    return httpx.MockTransport(handler)


def cat_with(*ids):
    cat = load_catalog(REPO / "inference.yaml")
    return type(cat)(cat.models, cat.providers, {i: cat.deployments[i] for i in ids}, cat.defaults)


LOCAL = "host.containers.internal"


def llamacpp_models(modalities):
    return {"data": [{"id": "lfm2-d1", "architecture": {"output_modalities": modalities}}]}


def test_llamacpp_plain_lm_is_llm_not_system_one():
    r = probe(cat_with("lfm2-d1-q8@local"), transport=serve({(LOCAL, "/models"): llamacpp_models(["text"])}))
    assert r["lfm2-d1-q8@local"].render() == "not ready: capability not advertised"
    assert Capability.llm in r["lfm2-d1-q8@local"].advertised
    assert Capability.system_one not in r["lfm2-d1-q8@local"].advertised


def test_llamacpp_decisions_modality_is_system_one():
    cat = cat_with("lfm2-d1-q8@local")
    r = probe(cat, transport=serve({(LOCAL, "/models"): fixture("llamacpp", mutate=lambda d: {"data": [dict(d["data"][0], id="lfm2-d1")]})}))
    got = r["lfm2-d1-q8@local"]
    assert got.render() == "ready" and got.ready
    assert got.reported_model_id == "lfm2-d1"
    assert cat.readiness is r and cat.is_ready("lfm2-d1-q8@local")


def test_llamacpp_embeddings_listed():
    t = serve({(LOCAL, "/models"): {"data": [{"id": "embeddinggemma-2", "architecture": {"output_modalities": ["embeddings"]}}]}})
    cat = cat_with("lfm2-d1-q8@local")
    cat.deployments["lfm2-d1-q8@local"] = cat.deployments["lfm2-d1-q8@local"].model_copy(update={"model": "embeddinggemma-2"})
    assert probe(cat, transport=t)["lfm2-d1-q8@local"].advertised == {Capability.embeddings}


def test_missing_credential_names_variable():
    r = probe(cat_with("lfm2-d1@openrouter"), env={}, transport=serve({}))
    assert r["lfm2-d1@openrouter"].render() == "not ready: credential missing: OPENROUTER_API_KEY"


def test_openrouter_ready_and_embeddings_listing():
    t = serve({("openrouter.ai", "/api/v1/models"): fixture("openrouter"),
               ("openrouter.ai", "/api/v1/embeddings/models"): fixture("openrouter", "embeddings_models.json")})
    r = probe(cat_with("lfm2-d1@openrouter", "nuextract3@openrouter", "embeddinggemma-2@openrouter"),
              env={"OPENROUTER_API_KEY": "k"}, transport=t)
    assert r["lfm2-d1@openrouter"].render() == "ready"
    assert r["lfm2-d1@openrouter"].reported_model_id == "liquid/lfm2-d1"
    assert r["nuextract3@openrouter"].render() == "ready"
    assert r["embeddinggemma-2@openrouter"].render() == "ready"


def test_model_missing_from_listing():
    t = serve({("api.typesafe.ai", "/v1/models"): {"data": [{"id": "other"}]}})
    r = probe(cat_with("jev-1.14.0@typesafe"), env={"TYPESAFE_API_KEY": "k"}, transport=t)
    assert r["jev-1.14.0@typesafe"].render() == "not ready: model missing"


def test_typesafe_models_listing_ready():
    t = serve({("api.typesafe.ai", "/v1/models"): fixture("typesafe")})
    r = probe(cat_with("jev-1.14.0@typesafe"), env={"TYPESAFE_API_KEY": "k"}, transport=t)
    assert r["jev-1.14.0@typesafe"].render() == "ready"


def test_cloudflare_needs_account_and_lists_models():
    cat = cat_with("clairic@cloudflare")
    r = probe(cat, env={"CLOUDFLARE_API_TOKEN": "t"}, transport=serve({}))
    assert r["clairic@cloudflare"].render() == "not ready: credential missing: CLOUDFLARE_ACCOUNT_ID"
    t = serve({("api.cloudflare.com", "/client/v4/accounts/acc/ai/models/search"): fixture("cloudflare")})
    r = probe(cat, env={"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "acc"}, transport=t)
    assert r["clairic@cloudflare"].render() == "ready"


def test_hanging_provider_is_unreachable_others_still_probed():
    t = serve({(LOCAL, "/models"): httpx.ReadTimeout("slow"), ("api.typesafe.ai", "/v1/models"): fixture("typesafe")})
    r = probe(cat_with("lfm2-d1-q8@local", "jev-1.14.0@typesafe"), env={"TYPESAFE_API_KEY": "k"}, transport=t)
    assert r["lfm2-d1-q8@local"].render() == "not ready: unreachable"
    assert r["jev-1.14.0@typesafe"].render() == "ready"


def test_stub_always_ready_with_all_capabilities():
    r = probe(cat_with("stub-system-one"), transport=serve({}))
    assert r["stub-system-one"].ready and r["stub-system-one"].advertised == set(Capability)


def test_one_probe_per_provider():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=llamacpp_models(["decisions"]))
    probe(cat_with("lfm2-d1-q8@local", "nuextract3@local"), transport=httpx.MockTransport(handler))
    assert calls == [f"http://{LOCAL}:8080/models"]


# --- run refusal through core's RunService -------------------------------------------------

def test_run_refused_when_deployment_asks_system_one_of_plain_lm(tmp_path):
    from weave_core.adapters import AdapterSpec, StageResult
    from weave_core.config import ConfigError, RunConfig
    from weave_core.db import make_engine, migrate
    from weave_core.registry import Registry
    from weave_core.runs import RunService
    from weave_core.stages import Stage, UnitKind
    from pydantic import BaseModel
    import sqlalchemy as sa
    import os

    class Settings(BaseModel): ...

    class NeedsSystemOne:
        spec = AdapterSpec("needs-s1", "1", Stage.extract, UnitKind.document, Settings, frozenset({Capability.system_one}))
        def __init__(self, settings, ctx): ...
        def process(self, unit, upstream): return StageResult({})

    registry = Registry()
    registry.register(NeedsSystemOne)
    cat = cat_with("lfm2-d1-q8@local")
    probe(cat, transport=serve({(LOCAL, "/models"): llamacpp_models(["text"])}))

    admin_url = os.environ.get("WEAVE_TEST_DATABASE_URL", "postgresql+psycopg://weave:weave@127.0.0.1:5432/postgres")
    name = "weave_probe_" + uuid.uuid4().hex[:8]
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}"')
    try:
        url = sa.engine.make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        migrate(url)
        engine = make_engine(url)
        cfg = RunConfig(name="t", track="paper", seed=1, stages={
            Stage.extract: {"adapter": "needs-s1", "deployments": {Capability.system_one: "lfm2-d1-q8@local"}}})
        with pytest.raises(ConfigError) as e:
            RunService(engine, registry, cat).create(cfg)
        assert any("is not ready" in p for p in e.value.problems)
        engine.dispose()
    finally:
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.dispose()


def test_blocking_provider_bounded_by_wall_clock_timeout():
    import time
    def handler(request):
        if request.url.host == LOCAL:
            time.sleep(1.5)
        return httpx.Response(200, json=fixture("typesafe"))
    t0 = time.monotonic()
    r = probe(cat_with("lfm2-d1-q8@local", "jev-1.14.0@typesafe"), env={"TYPESAFE_API_KEY": "k"},
              transport=httpx.MockTransport(handler), timeout=0.2)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0
    assert r["lfm2-d1-q8@local"].render() == "not ready: unreachable"
    assert r["jev-1.14.0@typesafe"].render() == "ready"
