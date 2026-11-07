from typing import Literal

from fastapi.testclient import TestClient
from pydantic import BaseModel

from weave_api.main import create_app
from weave_core.adapters import AdapterSpec
from weave_core.config import StaticCatalog
from weave_core.registry import Registry
from weave_core.runs import RunService
from weave_core.stages import PIPELINE, Capability, Stage, UnitKind


class Settings(BaseModel):
    mode: Literal["fast", "slow"] = "fast"
    limit: int | None = None


def make_registry():
    registry = Registry()
    for stage in reversed(PIPELINE):
        for name in ("b", "a"):
            spec = AdapterSpec(name=name, version="1", stage=stage, input_kind=UnitKind.document, settings_model=Settings,
                               capabilities=frozenset({Capability.llm}) if name == "a" else frozenset(), cacheable=False)
            registry._adapters[stage, name] = type(f"A_{stage}_{name}", (), {"spec": spec})
    return registry


def client(engine):
    return TestClient(create_app(RunService(engine, make_registry(), StaticCatalog({}))))


def test_adapters_include_settings_schema(engine):
    with client(engine) as c:
        body = c.get("/adapters", params={"limit": 200}).json()
    assert body["total"] == 2 * len(PIPELINE)
    assert [(i["stage"], i["name"]) for i in body["items"]][:4] == [("provide", "a"), ("provide", "b"), ("extract", "a"), ("extract", "b")]
    first = body["items"][0]
    assert first["capabilities"] == ["llm"] and first["cacheable"] is False and first["input_kind"] == "document"
    assert first["optional_stage"] is False
    props = first["settings_schema"]["properties"]
    assert props["mode"]["enum"] == ["fast", "slow"]
    assert {"type": "null"} in props["limit"]["anyOf"]
    tag = next(i for i in body["items"] if i["stage"] == "tag")
    assert tag["optional_stage"] is True


def test_filter_by_stage(engine):
    with client(engine) as c:
        body = c.get("/adapters", params={"stage": "embed"}).json()
        bad = c.get("/adapters", params={"stage": "nope"})
    assert [i["name"] for i in body["items"]] == ["a", "b"] and body["total"] == 2
    assert bad.status_code == 422
