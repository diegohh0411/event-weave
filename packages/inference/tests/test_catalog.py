from pathlib import Path

import pytest
from weave_core.stages import Capability
from weave_inference.catalog import CatalogError, load_catalog

REPO = Path(__file__).resolve().parents[3]


def write(tmp_path, text):
    p = tmp_path / "inference.yaml"
    p.write_text(text)
    return p


TWO_PROVIDERS_YAML = """
models:
  lfm2-d1: {capability: system_one}
providers:
  openrouter: {kind: openrouter, base_url: "https://openrouter.ai/api/v1", credential_env: OPENROUTER_API_KEY}
  local: {kind: llamacpp, base_url: "http://localhost:8080"}
deployments:
  lfm2-d1@openrouter: {model: lfm2-d1, provider: openrouter}
  lfm2-d1-q8@local: {model: lfm2-d1, provider: local, quantization: Q8_0, local: true}
defaults: {system_one: lfm2-d1@openrouter}
"""

BAD_REFS_YAML = """
models: {m: {capability: llm}}
providers: {p: {kind: stub, base_url: "stub:"}}
deployments:
  d1: {model: nope, provider: p}
  d2: {model: m, provider: ghost}
"""


def test_same_model_two_providers_are_two_deployments(tmp_path):
    cat = load_catalog(write(tmp_path, TWO_PROVIDERS_YAML))
    assert {"lfm2-d1@openrouter", "lfm2-d1-q8@local"} <= cat.deployments.keys()
    assert cat.capability_of("lfm2-d1@openrouter") == Capability.system_one
    assert cat.capability_of("lfm2-d1-q8@local") == Capability.system_one
    assert cat.capability_of("nope") is None


def test_secret_value_in_yaml_is_rejected(tmp_path):
    with pytest.raises(CatalogError, match="credential_env"):
        load_catalog(write(tmp_path, "providers: {x: {kind: openrouter, base_url: u, api_key: sk-123}}"))


def test_unknown_model_or_provider_reference_is_named(tmp_path):
    with pytest.raises(CatalogError) as e:
        load_catalog(write(tmp_path, BAD_REFS_YAML))
    assert "deployment 'd1': unknown model 'nope'" in e.value.problems
    assert "deployment 'd2': unknown provider 'ghost'" in e.value.problems


def test_default_must_be_openrouter_of_right_capability(tmp_path):
    bad = TWO_PROVIDERS_YAML.replace("defaults: {system_one: lfm2-d1@openrouter}", "defaults: {system_one: lfm2-d1-q8@local}")
    with pytest.raises(CatalogError) as e:
        load_catalog(write(tmp_path, bad))
    assert any("default" in p for p in e.value.problems)


def test_default_is_openrouter_for_three_capabilities():
    cat = load_catalog(REPO / "inference.yaml")
    for cap in (Capability.system_one, Capability.llm, Capability.embeddings):
        d = cat.default_for(cap)
        assert d is not None and cat.providers[cat.deployments[d].provider].kind == "openrouter"
        assert cat.capability_of(d) == cap
    assert cat.default_for(Capability.extraction) is None


def test_readiness_defaults_to_not_ready():
    cat = load_catalog(REPO / "inference.yaml")
    assert cat.is_ready("anything") is False


def test_repo_inference_yaml_loads():
    cat = load_catalog(REPO / "inference.yaml")
    assert {"lfm2-d1", "jev-1.14.0", "nuextract3", "embeddinggemma-2", "clairic"} == cat.models.keys()
    assert {c for c in Capability} == {cat.capability_of(d) for d in cat.deployments}
