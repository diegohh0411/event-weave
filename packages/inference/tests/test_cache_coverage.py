import sqlalchemy as sa
from weave_core.cache import cache_key
from weave_core.config import validate
from weave_core.runs import RunService
from weave_core.schema import jobs, stage_outputs
from weave_core.stages import JobStatus, Stage, UnitKind

from test_estimate import config, make_catalog, make_registry
from weave_inference.estimate import cache_coverage


def test_cache_coverage_none_without_prior_run(engine):
    assert cache_coverage(engine, make_registry(), config()) is None


def test_cache_coverage_counts_cached_keys(engine):
    registry = make_registry()
    service = RunService(engine, registry, make_catalog(True))
    run = service.create(config()).run_id
    service.start(run)
    cfg = validate(config(), registry, service.catalog)
    enrich = cfg.stages[Stage.enrich]
    spec = registry.get(Stage.enrich, enrich.adapter).spec
    with engine.begin() as conn:
        for i in range(4):
            conn.execute(jobs.insert().values(run_id=run, stage=Stage.enrich, unit_kind=UnitKind.mention, unit_id=f"m{i}", unit_content_hash="h",
                                              partition="p", order_key=[i], input_hash=f"in{i}", status=JobStatus.done, available_at=sa.func.now()))
        for i in (0, 1):
            conn.execute(stage_outputs.insert().values(cache_key=cache_key(f"in{i}", spec, enrich.settings, enrich.deployments), stage="enrich",
                                                       adapter=spec.name, adapter_version="1", output={}, emits=[], created_at=sa.func.now()))
    coverage = cache_coverage(engine, registry, cfg)
    assert coverage[Stage.enrich] == (2, 4)
    assert coverage[Stage.embed] == (0, 0)  # the prior run has no embed jobs


def test_cached_count_batches_lookups(engine, monkeypatch):
    import weave_inference.estimate as est
    monkeypatch.setattr(est, "LOOKUP_BATCH", 3)
    keys = [f"k{i}" for i in range(10)]
    with engine.begin() as conn:
        for k in keys[::2]:
            conn.execute(stage_outputs.insert().values(cache_key=k, stage="enrich", adapter="a", adapter_version="1", output={}, emits=[], created_at=sa.func.now()))
        assert est._cached_count(conn, keys) == 5
