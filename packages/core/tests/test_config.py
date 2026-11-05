import pytest
from weave_core.config import ConfigError, RunConfig, StaticCatalog, validate, config_hash
from weave_core.registry import Registry
from weave_core.stages import Stage, Capability
from fakes import SPECS

@pytest.fixture
def registry():
    r = Registry()
    for cls in SPECS:
        r.register(cls)
    return r

@pytest.fixture
def catalog():
    return StaticCatalog({"good-extractor": (Capability.extraction, True), "chat-model": (Capability.llm, True), "down-extractor": (Capability.extraction, False)})

@pytest.fixture
def good_config():
    return RunConfig(name="test", track="paper", seed=42, stages={cls.spec.stage: {"adapter": cls.spec.name, "deployments": {Capability.extraction: "good-extractor"} if cls.spec.capabilities else {}} for cls in SPECS})

def test_valid_config_fills_defaults(registry, catalog, good_config):
    v = validate(good_config, registry, catalog)
    assert v.stages[Stage.extract].settings == {"window": 1}   # fake-extractor default

def test_hash_ignores_spelled_out_defaults(registry, catalog, good_config):
    explicit = good_config.model_copy(deep=True)
    explicit.stages[Stage.extract].settings = {"window": 1}
    assert config_hash(validate(good_config, registry, catalog)) == config_hash(validate(explicit, registry, catalog))

def test_hash_changes_with_seed(registry, catalog, good_config):
    other = good_config.model_copy(update={"seed": good_config.seed + 1})
    assert config_hash(validate(good_config, registry, catalog)) != config_hash(validate(other, registry, catalog))

def test_tag_stage_is_optional(registry, catalog, good_config):
    del good_config.stages[Stage.tag]
    validate(good_config, registry, catalog)

def test_every_problem_is_reported(registry, catalog, good_config):
    del good_config.stages[Stage.cluster]
    good_config.stages[Stage.extract].adapter = "nope"
    good_config.stages[Stage.enrich].deployments = {}
    with pytest.raises(ConfigError) as e:
        validate(good_config, registry, catalog)
    assert e.value.problems == [
        "missing stage: cluster",
        "stage extract: unknown adapter 'nope'",
        "stage enrich: adapter 'fake-enricher' needs a deployment for 'extraction'",
    ]

@pytest.mark.parametrize("deployment,problem", [
    ("ghost", "stage enrich: deployment 'ghost' is unknown"),
    ("chat-model", "stage enrich: deployment 'chat-model' does not provide 'extraction'"),
    ("down-extractor", "stage enrich: deployment 'down-extractor' is not ready"),
])
def test_bad_deployment(registry, catalog, good_config, deployment, problem):
    good_config.stages[Stage.enrich].deployments = {Capability.extraction: deployment}
    with pytest.raises(ConfigError) as e:
        validate(good_config, registry, catalog)
    assert e.value.problems == [problem]

def test_invalid_settings_named(registry, catalog, good_config):
    good_config.stages[Stage.extract].settings = {"window": "wide"}
    with pytest.raises(ConfigError) as e:
        validate(good_config, registry, catalog)
    assert e.value.problems[0].startswith("stage extract: invalid settings:")
