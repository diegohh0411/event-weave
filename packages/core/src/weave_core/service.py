import os
from functools import partial
from pathlib import Path

import yaml

from .config import StaticCatalog
from .db import make_engine
from .registry import Registry
from .runs import RunService
from .settings import Settings
from .stages import Capability


def build_service(probe: bool = True, probe_timeout: float = 5.0) -> RunService:
    """Build the RunService from the environment. Raises weave_inference CatalogError on a bad inference file.

    A bounded, nonfatal probe keeps cold starts (API and workers) from blocking on or dying at model providers;
    callers that must not probe at all (the API's cold start) pass probe=False.
    """
    settings = Settings.from_env()
    registry = Registry()
    registry.load_entry_points()
    inference_file = os.environ.get('WEAVE_INFERENCE_FILE')
    if inference_file:
        try:
            from weave_inference.catalog import load_catalog
            from weave_inference.estimate import LedgerEstimator, store_readiness
            from weave_inference.gateway import Inference
            from weave_inference.probe import probe as probe_catalog
        except ImportError:  # weave-inference is not installed
            pass
        else:
            catalog = load_catalog(Path(inference_file))
            if probe:
                probe_catalog(catalog, timeout=probe_timeout)
            engine = make_engine(settings.database_url)
            Inference.configure(catalog, engine)
            return RunService(engine, registry, catalog, LedgerEstimator(engine, registry),
                              on_start=partial(store_readiness, catalog=catalog))
    path = os.environ.get('WEAVE_DEPLOYMENTS_FILE')
    data = yaml.safe_load(Path(path).read_text()) if path else {}
    catalog = StaticCatalog({key: (Capability(value[0]), value[1]) for key, value in (data or {}).items()})
    return RunService(make_engine(settings.database_url), registry, catalog)
